/**
 * @file adc_stream_service.c
 * @brief 服务层核心：ADC 数据流状态机、双向环形队列、命令解析与转速上报。
 *
 * 分层位置：APP -> SERVICE(本文件) -> PLATFORM -> IMPLEMENT -> VENDOR。
 * 本文件只依赖 platform_*.h 抽象接口，不直接访问任何 STM32 寄存器。
 *
 * 两条数据通路：
 *   上行：ADC 采样回调 -> adc_queue（16 KB 环形队列）-> poll 每次最多 512 字节
 *         -> USB CDC 发送环 -> 上位机；
 *   下行：USB 接收回调 -> rx_queue（64 字节命令队列）-> poll 解析 O\0 / C\0
 *         -> 启动或停止采样。
 *
 * 背压策略：
 *   - USB 发送环忙：保留 adc_tail 不动，下一次 poll 原样重发同一批数据；
 *   - ADC 队列水位过高：暂停 ADC 触发（TIM2/TIM5 继续跑，测速不断），
 *     队列降到半满以下再恢复。
 *
 * 中断约定：
 *   采样回调 / USB 接收回调只做入队，所有耗时操作都放在 adc_stream_service_poll()
 *   里执行，避免在中断上下文中操作 USB 或启动/停止采样。
 */

#include "adc_stream_service.h"
#include "platform_board.h"
#include "adc_fir.h"   /* [NEW] FIR 滤波模块；ADC_FIR_ENABLE=0 时此头只定义开关宏 */
#include <string.h>

/* service 层运行参数：修改采样频率、转速刷新周期、队列大小优先看这里。 */
#define ADC_STREAM_SERVICE_TRIGGER_COUNTS 1U          /* 1 个编码器计数触发 1 次 ADC */
/* 转速更新频率 */
#define ADC_STREAM_SERVICE_SPEED_UPDATE_MS 500U       /* 转速帧上报周期：500 ms */
/* 只用 PA5（B 相）上升沿：4000 线编码器每转产生 4000 个硬件触发。 */
#define ADC_STREAM_SERVICE_ENCODER_COUNTS_PER_REV 4000U /* 编码器每转计数（当前 Z 相测速不用） */
#define ADC_STREAM_SERVICE_RX_QUEUE_SIZE 64U          /* USB 接收命令环形队列容量 */
#define ADC_STREAM_SERVICE_ADC_QUEUE_SIZE 16384U      /* ADC 原始字节环形队列容量（16 KB） */
#define ADC_STREAM_SERVICE_TX_CHUNK_SIZE 512U         /* 单次 poll 写入 USB 的最大字节数 */
/*
 * 队列高水位保护阈值。
 * 需要容纳：一整圈 DMA（1024 个样本）的突发提交 + 额外一个发送块。
 * 由于环形队列固定空出一个槽位，只留两个块（2×512）会在停止时
 * DMA 冲刷那一刻差 1 个字节，所以这里取 3 个块。
 */
#define ADC_STREAM_SERVICE_ADC_QUEUE_PAUSE_GUARD \
    (3U * ADC_STREAM_SERVICE_TX_CHUNK_SIZE)
/*
 * [NEW] 转速帧已从 USB 数据流移除：不再插入 A5 5A 52 04 帧，
 * 避免上位机把它当成 9 个 ADC 样本。转速仍由 USART1 的 D 行遥测输出。
 *
 * [NEW] FIR 运行时命令识别：
 *   46 00 ('F' + 0x00) -> 打开低通
 *   52 00 ('R' + 0x00) -> 关闭，RAW 旁路
 * ADC_FIR_ENABLE=0 时展开为 0，命令解析与改动前完全一致。
 */
#if ADC_FIR_ENABLE
#define ADC_STREAM_SERVICE_IS_FIR_COMMAND(byte) \
    (((byte) == (uint8_t)'F') || ((byte) == (uint8_t)'R'))
#else
#define ADC_STREAM_SERVICE_IS_FIR_COMMAND(byte) 0
#endif

/*
 * [NEW] 板级使能引脚（PB11）命令识别：
 *   45 00 ('E' + 0x00) -> PB11 高电平，使能
 *   44 00 ('D' + 0x00) -> PB11 低电平，失能
 * 与 FIR 无关，始终参与编译。
 */
#define ADC_STREAM_SERVICE_IS_ENABLE_COMMAND(byte) \
    (((byte) == (uint8_t)'E') || ((byte) == (uint8_t)'D'))

/*
 * ADC主数据流：ADC采样中断只把原始字节放入环形队列。
 * 主循环poll再按最多128字节一批写入USB CDC发送环，数据中不插入AA/BB帧头。
 *
 * @param state service 当前状态。
 * @param adc_stream platform 层 ADC 采样流对象，负责启动/停止/暂停采样。
 * @param byte_stream platform 层字节流对象，负责向 USB CDC 写入数据。
 * @param usb_device platform 层 USB 设备对象，负责初始化 USB 协议栈。
 * @param pending_command 保存已经收到但尚未等到结尾 0x00 的 O/C 命令。
 * @param stop_requested 上位机请求停止后置位，等待 ADC 队列发空后清除。
 * @param stats 发送、丢样、命令数量等统计信息。
 * @param rx_queue USB 接收命令环形队列。
 * @param rx_head/rx_tail RX 环形队列写入/读取下标。
 * @param adc_queue ADC 原始采样字节环形队列。
 * @param adc_head/adc_tail ADC 环形队列写入/读取下标。
 * @param tx_chunk poll 中一次批量写 USB 的临时缓冲区。
 * @param paused_for_tx 因 USB 或本地队列压力暂停采样时置位。
 */
typedef struct {
    adc_stream_service_state_t state;
    platform_adc_stream_t *adc_stream;
    platform_byte_stream_t *byte_stream;
    platform_usb_device_t *usb_device;
    uint8_t pending_command;
    uint8_t stop_requested;
    adc_stream_service_stats_t stats;
    uint8_t rx_queue[ADC_STREAM_SERVICE_RX_QUEUE_SIZE];
    volatile uint32_t rx_head;
    volatile uint32_t rx_tail;
    uint8_t adc_queue[ADC_STREAM_SERVICE_ADC_QUEUE_SIZE];
    volatile uint32_t adc_head;
    volatile uint32_t adc_tail;
    uint8_t tx_chunk[ADC_STREAM_SERVICE_TX_CHUNK_SIZE];
    uint8_t paused_for_tx;
    uint8_t usb_busy_active;
#if ADC_FIR_ENABLE
    /*
     * [NEW] FIR 发送暂存。
     * tx_raw 保存本批原始字节，滤波结果仍写回 tx_chunk。
     * tx_pending_valid 保证同一批数据只滤波一次：USB BUSY 时下一次 poll
     * 直接重发 tx_chunk，不能重新执行 prepare（否则 FIR 状态会被重复推进）。
     * ADC_FIR_ENABLE=0 时这些字段完全不参与编译，结构体与原版一致。
     */
    uint8_t tx_raw[ADC_STREAM_SERVICE_TX_CHUNK_SIZE];
    uint16_t tx_pending_count;
    uint8_t tx_pending_valid;
#endif
} adc_stream_service_t;

static adc_stream_service_t adc_stream_service;

static void adc_stream_service_sample_callback(platform_adc_stream_t *stream,
                                               const uint8_t *samples,
                                               uint32_t sample_count,
                                               void *context);
static void adc_stream_service_rx_callback(platform_byte_stream_t *stream,
                                           const uint8_t *data,
                                           uint32_t length,
                                           void *context);

/**
 * @brief 计算环形队列的下一个下标。
 *
 * @param index 当前下标。
 * @param size 环形队列容量。
 * @return 加 1 后的下标，越界时回绕到 0。
 */
static uint32_t adc_stream_service_next_index(uint32_t index, uint32_t size)
{
    index++;
    if (index >= size) {
        index = 0U;
    }

    return index;
}

/**
 * @brief 计算环形队列中已缓存的数据个数。
 *
 * @param head 写入下标。
 * @param tail 读取下标。
 * @param size 环形队列容量。
 * @return 队列中当前有效元素数量。
 */
static uint32_t adc_stream_service_queue_count(volatile uint32_t head,
                                               volatile uint32_t tail,
                                               uint32_t size)
{
    if (head >= tail) {
        return head - tail;
    }

    return size - (tail - head);
}

/**
 * @brief 清空 ADC 采样环形队列。
 *
 * 只重置读写下标，不擦除缓冲区内容；旧数据会被后续写入自然覆盖。
 */
static void adc_stream_service_reset_adc_queue(void)
{
    adc_stream_service.adc_head = 0U;
    adc_stream_service.adc_tail = 0U;
}

/**
 * @brief 通过 platform 字节流发送一段数据。
 *
 * @param data 待发送数据缓冲区。
 * @param length 待发送字节数。
 * @return PLATFORM_OK 表示数据已写入底层发送队列，PLATFORM_BUSY 表示底层空间不足。
 */
static platform_status_t adc_stream_service_send_bytes(const uint8_t *data, uint32_t length)
{
    if ((adc_stream_service.byte_stream == 0) ||
        (adc_stream_service.byte_stream->ops == 0) ||
        (adc_stream_service.byte_stream->ops->send == 0)) {
        return PLATFORM_NOT_READY;
    }

    return adc_stream_service.byte_stream->ops->send(adc_stream_service.byte_stream, data, length);
}

/**
 * @brief 因发送压力暂停新的 ADC 采样触发。
 *
 * USB 发送或本地 ADC 队列有压力时，只暂停 ADC 采样。
 * TIM2 和 TIM5 保持运行，保证测速计数在发送背压期间仍然连续。
 */
static void adc_stream_service_pause_for_tx(void)
{
    if ((adc_stream_service.adc_stream != 0) &&
        (adc_stream_service.adc_stream->ops != 0) &&
        (adc_stream_service.adc_stream->ops->pause != 0) &&
        (adc_stream_service.paused_for_tx == 0U)) {
        if (adc_stream_service.adc_stream->ops->pause(adc_stream_service.adc_stream) == PLATFORM_OK) {
            adc_stream_service.paused_for_tx = 1U;
            adc_stream_service.stats.adc_pause_count++;
        }
    }
}

/**
 * @brief 当 ADC 队列压力下降后恢复采样。
 *
 * 只有在曾经因发送压力暂停、未收到停止请求，并且 ADC 队列低于半满时才恢复。
 */
static void adc_stream_service_resume_after_tx(void)
{
    uint32_t used;

    used = adc_stream_service_queue_count(adc_stream_service.adc_head,
                                          adc_stream_service.adc_tail,
                                          ADC_STREAM_SERVICE_ADC_QUEUE_SIZE);

    if ((adc_stream_service.adc_stream != 0) &&
        (adc_stream_service.adc_stream->ops != 0) &&
        (adc_stream_service.adc_stream->ops->resume != 0) &&
        (adc_stream_service.paused_for_tx != 0U) &&
        (adc_stream_service.stop_requested == 0U) &&
        (used < (ADC_STREAM_SERVICE_ADC_QUEUE_SIZE / 2U))) {
        if (adc_stream_service.adc_stream->ops->resume(adc_stream_service.adc_stream) == PLATFORM_OK) {
            adc_stream_service.paused_for_tx = 0U;
            adc_stream_service.stats.adc_resume_count++;
        }
    }
}

/**
 * @brief 把一批 ADC 采样字节压入 service 环形队列。
 *
 * @param samples ADC 采样回调给出的 8bit 原始值数组。
 * @param sample_count 本次要写入队列的采样数。
 *
 * 队列满时增加丢样计数并暂停后续采样；队列接近满时也会提前暂停采样。
 */
static void adc_stream_service_adc_queue_push_block(const uint8_t *samples,
                                                    uint32_t sample_count)
{
    uint32_t head;
    uint32_t tail;
    uint32_t used;
    uint32_t free_count;
    uint32_t first_count;
    uint32_t new_head;

    if ((samples == 0) || (sample_count == 0U)) {
        return;
    }

    head = adc_stream_service.adc_head;
    tail = adc_stream_service.adc_tail;
    used = adc_stream_service_queue_count(head,
                                          tail,
                                          ADC_STREAM_SERVICE_ADC_QUEUE_SIZE);
    free_count = (ADC_STREAM_SERVICE_ADC_QUEUE_SIZE - 1U) - used;

    if (sample_count > free_count) {
        adc_stream_service.stats.samples_dropped += sample_count;
        adc_stream_service_pause_for_tx();
        return;
    }

    first_count = ADC_STREAM_SERVICE_ADC_QUEUE_SIZE - head;
    if (first_count > sample_count) {
        first_count = sample_count;
    }

    memcpy(&adc_stream_service.adc_queue[head], samples, first_count);
    if (sample_count > first_count) {
        memcpy(&adc_stream_service.adc_queue[0],
               &samples[first_count],
               sample_count - first_count);
    }

    new_head = head + sample_count;
    if (new_head >= ADC_STREAM_SERVICE_ADC_QUEUE_SIZE) {
        new_head -= ADC_STREAM_SERVICE_ADC_QUEUE_SIZE;
    }

    /* 等整块 DMA 数据全部拷贝完成后再发布新的 head，避免 poll 读到半块数据。 */
    adc_stream_service.adc_head = new_head;
    used += sample_count;
    if (used > adc_stream_service.stats.adc_queue_peak) {
        adc_stream_service.stats.adc_queue_peak = used;
    }
    if (used >=
        (ADC_STREAM_SERVICE_ADC_QUEUE_SIZE -
         ADC_STREAM_SERVICE_ADC_QUEUE_PAUSE_GUARD)) {
        adc_stream_service_pause_for_tx();
    }
}

/**
 * @brief 从 ADC 队列复制一批待发送数据到 tx_chunk。
 *
 * @return 本批可发送的字节数，最大为 ADC_STREAM_SERVICE_TX_CHUNK_SIZE。
 */
static uint32_t adc_stream_service_prepare_adc_chunk(void)
{
    uint32_t count;
    uint32_t first_count;
    uint32_t tail;

#if ADC_FIR_ENABLE
    /*
     * [NEW] 同一批数据只滤波一次。
     * USB BUSY 时上一批已经滤好的 tx_chunk 还在，直接返回它的长度，
     * 由 send_adc_chunk() 原样重发；绝不能重新执行下面的 FIR，
     * 否则同一段输入会被推进两次，FIR 状态与数据都会错。
     */
    if (adc_stream_service.tx_pending_valid != 0U) {
        return (uint32_t)adc_stream_service.tx_pending_count;
    }
#endif

    count = adc_stream_service_queue_count(adc_stream_service.adc_head,
                                           adc_stream_service.adc_tail,
                                           ADC_STREAM_SERVICE_ADC_QUEUE_SIZE);
    if (count > ADC_STREAM_SERVICE_TX_CHUNK_SIZE) {
        count = ADC_STREAM_SERVICE_TX_CHUNK_SIZE;
    }

#if ADC_FIR_ENABLE
    /*
     * [NEW] 队列为空时必须立即返回，不能继续执行下面的 FIR 和 pending 赋值。
     *
     * 否则会留下“待发送 0 字节”的 pending 状态：
     *   tx_pending_valid = 1、tx_pending_count = 0；
     * 下一轮 prepare 在这里的 pending 短路分支直接返回 0，
     * send_adc_chunk 看到 count==0 又直接返回 PLATFORM_OK，
     * 永远不会走到“发送成功后清 tx_pending_valid”的唯一分支。
     * 结果就是 USB 上行永久卡死：队列涨到高水位后暂停采样，
     * 上位机一个字节也收不到（ub/ue 也一直是 0，因为根本没调用发送）。
     * 开机后第一次 poll 时队列必然是空的，所以这个死锁会立即触发。
     */
    if (count == 0U) {
        return 0U;
    }
#endif

    tail = adc_stream_service.adc_tail;
    first_count = ADC_STREAM_SERVICE_ADC_QUEUE_SIZE - tail;
    if (first_count > count) {
        first_count = count;
    }

    memcpy(adc_stream_service.tx_chunk,
           &adc_stream_service.adc_queue[tail],
           first_count);
    if (count > first_count) {
        memcpy(&adc_stream_service.tx_chunk[first_count],
               &adc_stream_service.adc_queue[0],
               count - first_count);
    }

#if ADC_FIR_ENABLE
    /*
     * [NEW] 取出的原始字节先留一份到 tx_raw，再把滤波结果写回 tx_chunk。
     * tx_chunk 在 send 成功之前必须保持不变，供 BUSY 重试使用。
     * count <= TX_CHUNK_SIZE(512)，可以安全转换为 uint16_t。
     */
    memcpy(adc_stream_service.tx_raw, adc_stream_service.tx_chunk, count);
    adc_fir_process_block(adc_stream_service.tx_raw,
                          adc_stream_service.tx_chunk,
                          (uint16_t)count);
    adc_stream_service.tx_pending_count = (uint16_t)count;
    adc_stream_service.tx_pending_valid = 1U;
#endif

    return count;
}

/**
 * @brief 丢弃已经成功写入 USB 发送队列的 ADC 字节。
 *
 * @param count 本次已经发送成功的字节数量。
 */
static void adc_stream_service_drop_sent_adc(uint32_t count)
{
    uint32_t new_tail;

    new_tail = adc_stream_service.adc_tail + count;
    if (new_tail >= ADC_STREAM_SERVICE_ADC_QUEUE_SIZE) {
        new_tail -= ADC_STREAM_SERVICE_ADC_QUEUE_SIZE;
    }

    adc_stream_service.adc_tail = new_tail;
}

/**
 * @brief 从 ADC 队列取一批数据并写入 USB CDC 字节流。
 *
 * @return PLATFORM_OK 表示本次没有数据或发送成功；忙/错误时会暂停采样等待下次 poll。
 */
static platform_status_t adc_stream_service_send_adc_chunk(void)
{
    uint32_t count;
    platform_status_t status;

    count = adc_stream_service_prepare_adc_chunk();
    if (count == 0U) {
        return PLATFORM_OK;
    }

    status = adc_stream_service_send_bytes(adc_stream_service.tx_chunk, count);
    if (status == PLATFORM_OK) {
        adc_stream_service_drop_sent_adc(count);
        adc_stream_service.stats.samples_sent += count;
        adc_stream_service.usb_busy_active = 0U;
#if ADC_FIR_ENABLE
        /* [NEW] 这批已成功发出，允许下一次 prepare 取新数据并滤波。 */
        adc_stream_service.tx_pending_valid = 0U;
#endif
        return PLATFORM_OK;
    }

    /*
     * USB 发送环短暂 BUSY 属于正常背压：adc_tail 保持不动，
     * 下一次 poll 原样重发同一批数据。16 KB 服务队列可以吸收这段短暂停顿，
     * 只有水位超过高水位阈值时才暂停采样。
     */
    if (status == PLATFORM_BUSY) {
        adc_stream_service.stats.usb_busy_returns++;
        if (adc_stream_service.usb_busy_active == 0U) {
            adc_stream_service.usb_busy_active = 1U;
            adc_stream_service.stats.usb_busy_episodes++;
        }
        return status;
    }

    /* 非 BUSY 的传输错误同样暂停新采样，防止队列被持续写满。 */
    adc_stream_service_pause_for_tx();
    return status;
}

/**
 * @brief 停止请求后检查 ADC 队列是否已经发送完。
 *
 * 上位机发 C\0 后底层采样立即停止，但队列里可能还有旧数据；
 * poll 持续发送到队列为空后清除 stop_requested。
 */
static void adc_stream_service_finish_stop_if_drained(void)
{
    if (adc_stream_service.stop_requested == 0U) {
        return;
    }

    if (adc_stream_service_queue_count(adc_stream_service.adc_head,
                                       adc_stream_service.adc_tail,
                                       ADC_STREAM_SERVICE_ADC_QUEUE_SIZE) == 0U) {
        adc_stream_service.stop_requested = 0U;
    }
}

#if ADC_FIR_ENABLE
/**
 * [NEW] 运行时切换 FIR 模式，并作废已经暂存但尚未发送的 pending 块。
 *
 * pending 块是按旧模式处理好的数据，切换模式后不能再原样发出去。
 * 清除它是安全的：adc_tail 还没有推进，下一次 prepare 会从队列的同一位置
 * 重新取出同一批字节，按新模式重新处理。
 *
 * 只有模式真正改变时才清除。模式没变时，pending 块已经推进过一次 FIR 状态，
 * 如果清掉再重新处理同一批输入，FIR 历史会前进两次，输出就错了。
 *
 * @param mode 目标模式（LOWPASS / BYPASS）。
 */
static void adc_stream_service_apply_fir_mode(adc_fir_mode_t mode)
{
    if (adc_fir_get_mode() == mode) {
        return;
    }

    adc_fir_set_mode(mode);
    adc_stream_service.tx_pending_count = 0U;
    adc_stream_service.tx_pending_valid = 0U;
}
#endif

/**
 * @brief 执行已经完整解析出的上位机命令。
 *
 * @param command 命令字节：'O' 开始采样、'C' 停止采样；
 *                'E' 使能 PB11、'D' 失能 PB11；
 *                ADC_FIR_ENABLE=1 时还支持 'F' 开滤波、'R' 关闭（RAW 旁路）。
 */
static void adc_stream_service_execute_command(uint8_t command)
{
    adc_stream_service.stats.commands_received++;

    if (command == (uint8_t)'O') {
        /*
         * 收到O\0后直接开始采集，不回传O 00，避免应答混入图像数据。
         */
        (void)adc_stream_service_start();
    } else if (command == (uint8_t)'C') {
        /*
         * 收到C\0后立即停止底层采样，再由poll把ADC队列中剩余字节发完。
         */
        if ((adc_stream_service.state == ADC_STREAM_SERVICE_STREAMING) ||
            (adc_stream_service.state == ADC_STREAM_SERVICE_PAUSED)) {
            (void)adc_stream_service_stop();
        }
        adc_stream_service.stop_requested = 1U;
    } else if (command == (uint8_t)'E') {
        /*
         * [NEW] 45 00：PB11 输出高电平，使能板级 3 V 电源轨。
         * 与采集状态无关，任何时候都可以切换。
         */
        (void)platform_board_set_enable(1U);
    } else if (command == (uint8_t)'D') {
        /*
         * [NEW] 44 00：PB11 输出低电平，失能板级 3 V 电源轨。
         */
        (void)platform_board_set_enable(0U);
    }
#if ADC_FIR_ENABLE
    else if (command == (uint8_t)'F') {
        /* [NEW] 46 00：打开 FIR 低通。下一次 prepare 开始使用滤波后的数据。 */
        adc_stream_service_apply_fir_mode(ADC_FIR_MODE_LOWPASS);
    } else if (command == (uint8_t)'R') {
        /* [NEW] 52 00：关闭 FIR，RAW 旁路；输出与未加滤波时逐字节一致。 */
        adc_stream_service_apply_fir_mode(ADC_FIR_MODE_BYPASS);
    }
#endif
}

/**
 * @brief 按 O\0/C\0 协议解析一个 USB 接收字节。
 *
 * @param byte 当前从 RX 队列取出的字节。
 *
 * pending_command 保存已经看到的命令字符；只有后续收到 0x00 才真正执行。
 */
static void adc_stream_service_parse_rx_byte(uint8_t byte)
{
    if (adc_stream_service.pending_command == 0U) {
        /*
         * [NEW] 命令字符：O/C 采集启停；E/D 使能引脚；F/R 滤波开关
         * （ADC_FIR_ENABLE=0 时 F/R 展开为 0，行为与原版一致）。
         */
        if ((byte == (uint8_t)'O') || (byte == (uint8_t)'C') ||
            (ADC_STREAM_SERVICE_IS_ENABLE_COMMAND(byte) != 0U) ||
            (ADC_STREAM_SERVICE_IS_FIR_COMMAND(byte) != 0U)) {
            adc_stream_service.pending_command = byte;
        }
    } else {
        if (byte == 0U) {
            adc_stream_service_execute_command(adc_stream_service.pending_command);
            adc_stream_service.pending_command = 0U;
        } else if ((byte == (uint8_t)'O') || (byte == (uint8_t)'C') ||
                   (ADC_STREAM_SERVICE_IS_ENABLE_COMMAND(byte) != 0U) ||
                   (ADC_STREAM_SERVICE_IS_FIR_COMMAND(byte) != 0U)) {
            adc_stream_service.pending_command = byte;
        } else {
            adc_stream_service.pending_command = 0U;
        }
    }
}

/**
 * @brief 处理 USB RX 环形队列中已经缓存的命令字节。
 *
 * 该函数在主循环 poll 中执行，避免在 USB 接收回调里直接启动/停止采样。
 */
static void adc_stream_service_process_rx_queue(void)
{
    while (adc_stream_service.rx_tail != adc_stream_service.rx_head) {
        uint8_t byte = adc_stream_service.rx_queue[adc_stream_service.rx_tail];
        adc_stream_service.rx_tail = adc_stream_service_next_index(adc_stream_service.rx_tail,
                                                                   ADC_STREAM_SERVICE_RX_QUEUE_SIZE);
        adc_stream_service_parse_rx_byte(byte);
    }
}

/**
 * @brief 处理 ADC 数据队列的 USB 批量发送。
 *
 * 发送成功后尝试恢复因发送压力暂停的采样，并检查停止请求是否已经收尾完成。
 */
static void adc_stream_service_process_adc_tx(void)
{
    if (adc_stream_service_send_adc_chunk() == PLATFORM_OK) {
        adc_stream_service_resume_after_tx();
        adc_stream_service_finish_stop_if_drained();
    }
}

/**
 * @brief 初始化 ADC 数据流 service。
 *
 * 从 board 对象获取 platform 设备，依次初始化 USB 字节流、注册 RX 回调、
 * 配置 ADC 采样参数、注册采样回调、初始化 USB 设备。
 *
 * @return PLATFORM_OK 表示所有依赖初始化成功。
 */
platform_status_t adc_stream_service_init(void)
{
    const platform_board_t *board = platform_board_get();
    platform_adc_stream_config_t adc_config;
    platform_status_t status;

    if ((board == 0) || (board->adc_stream == 0) ||
        (board->byte_stream == 0) || (board->usb_device == 0)) {
        adc_stream_service.state = ADC_STREAM_SERVICE_ERROR;
        return PLATFORM_NOT_READY;
    }

    adc_stream_service.adc_stream = board->adc_stream;
    adc_stream_service.byte_stream = board->byte_stream;
    adc_stream_service.usb_device = board->usb_device;
    adc_stream_service.pending_command = 0U;
    adc_stream_service.stop_requested = 0U;
    adc_stream_service.rx_head = 0U;
    adc_stream_service.rx_tail = 0U;
    adc_stream_service.paused_for_tx = 0U;
    adc_stream_service.usb_busy_active = 0U;
    adc_stream_service.stats.samples_sent = 0U;
    adc_stream_service.stats.samples_dropped = 0U;
    adc_stream_service.stats.commands_received = 0U;
    adc_stream_service.stats.usb_busy_returns = 0U;
    adc_stream_service.stats.usb_busy_episodes = 0U;
    adc_stream_service.stats.adc_pause_count = 0U;
    adc_stream_service.stats.adc_resume_count = 0U;
    adc_stream_service.stats.adc_queue_peak = 0U;
    adc_stream_service_reset_adc_queue();

#if ADC_FIR_ENABLE
    /*
     * [NEW] FIR 只初始化一次。adc_fir_init() 内部检查 arm_fir_init_q15 的返回值；
     * 若失败，process 会退化为直通，不会让采集链路起不来。
     */
    adc_fir_init();
    adc_stream_service.tx_pending_valid = 0U;
#endif

    if ((adc_stream_service.byte_stream->ops == 0) ||
        (adc_stream_service.adc_stream->ops == 0) ||
        (adc_stream_service.usb_device->ops == 0)) {
        adc_stream_service.state = ADC_STREAM_SERVICE_ERROR;
        return PLATFORM_NOT_READY;
    }

    status = adc_stream_service.byte_stream->ops->init(adc_stream_service.byte_stream);
    if (status != PLATFORM_OK) {
        adc_stream_service.state = ADC_STREAM_SERVICE_ERROR;
        return status;
    }

    status = adc_stream_service.byte_stream->ops->set_rx_callback(adc_stream_service.byte_stream,
                                                                  adc_stream_service_rx_callback,
                                                                  &adc_stream_service);
    if (status != PLATFORM_OK) {
        adc_stream_service.state = ADC_STREAM_SERVICE_ERROR;
        return status;
    }

    adc_config.trigger_counts_per_sample = ADC_STREAM_SERVICE_TRIGGER_COUNTS;
    adc_config.speed_update_ms = ADC_STREAM_SERVICE_SPEED_UPDATE_MS;
    adc_config.encoder_counts_per_rev = ADC_STREAM_SERVICE_ENCODER_COUNTS_PER_REV;
    status = adc_stream_service.adc_stream->ops->init(adc_stream_service.adc_stream, &adc_config);
    if (status != PLATFORM_OK) {
        adc_stream_service.state = ADC_STREAM_SERVICE_ERROR;
        return status;
    }

    status = adc_stream_service.adc_stream->ops->set_sample_callback(adc_stream_service.adc_stream,
                                                                     adc_stream_service_sample_callback,
                                                                     &adc_stream_service);
    if (status != PLATFORM_OK) {
        adc_stream_service.state = ADC_STREAM_SERVICE_ERROR;
        return status;
    }

    status = adc_stream_service.usb_device->ops->init(adc_stream_service.usb_device);
    if (status != PLATFORM_OK) {
        adc_stream_service.state = ADC_STREAM_SERVICE_ERROR;
        return status;
    }

    adc_stream_service.state = ADC_STREAM_SERVICE_STOPPED;
    return PLATFORM_OK;
}

/**
 * @brief service 主轮询函数。
 *
 * 主循环需要反复调用：先处理命令，再发 ADC 数据，最后处理转速帧。
 */
void adc_stream_service_poll(void)
{
    adc_stream_service_process_rx_queue();
    adc_stream_service_process_adc_tx();
}

/**
 * @brief 启动 ADC 数据流。
 *
 * 清空旧 ADC 队列和转速缓存后调用 platform ADC stream 的 start ops。
 * 已经处于采集中时重复收到 O\0 不做任何操作（幂等），避免丢数据。
 *
 * @return PLATFORM_OK 表示底层已开始由编码器触发 ADC 采样。
 */
platform_status_t adc_stream_service_start(void)
{
    platform_status_t status;

    if ((adc_stream_service.adc_stream == 0) ||
        (adc_stream_service.adc_stream->ops == 0) ||
        (adc_stream_service.adc_stream->ops->start == 0)) {
        adc_stream_service.state = ADC_STREAM_SERVICE_ERROR;
        return PLATFORM_NOT_READY;
    }

    /*
     * [NEW] 重复 O\0 幂等：已经采集中时直接返回，不做下面的队列清空、
     * FIR 复位和统计清零。否则上位机重连或重复下发 O\0 时，队列里
     * 最多 16 KB 的已采集数据会被静默丢掉，而且不计入 samples_dropped。
     * 需要真正重新开始一次采集时，应先发 C\0 停止，再发 O\0。
     */
    if (adc_stream_service.state == ADC_STREAM_SERVICE_STREAMING) {
        return PLATFORM_OK;
    }

    adc_stream_service.stop_requested = 0U;
    adc_stream_service.paused_for_tx = 0U;
    adc_stream_service.usb_busy_active = 0U;
    adc_stream_service.stats.samples_sent = 0U;
    adc_stream_service.stats.samples_dropped = 0U;
    adc_stream_service.stats.usb_busy_returns = 0U;
    adc_stream_service.stats.usb_busy_episodes = 0U;
    adc_stream_service.stats.adc_pause_count = 0U;
    adc_stream_service.stats.adc_resume_count = 0U;
    adc_stream_service.stats.adc_queue_peak = 0U;
    adc_stream_service_reset_adc_queue();

#if ADC_FIR_ENABLE
    /*
     * [NEW] 新的一次采集：清 FIR 历史状态，下一次处理用首个样本预热；
     * 同时丢弃上一次可能残留的 pending 块（它对应的是旧队列数据）。
     */
    adc_fir_reset();
    adc_stream_service.tx_pending_valid = 0U;
#endif

    status = adc_stream_service.adc_stream->ops->start(adc_stream_service.adc_stream);
    if (status == PLATFORM_OK) {
        adc_stream_service.state = ADC_STREAM_SERVICE_STREAMING;
    }

    return status;
}

/**
 * @brief 停止 ADC 数据流。
 *
 * 调用底层 stop ops 停止新采样；已经进入 service 队列的数据仍由 poll 继续发送。
 *
 * @return PLATFORM_OK 表示底层采样流已经停止。
 */
platform_status_t adc_stream_service_stop(void)
{
    platform_status_t status;

    if ((adc_stream_service.adc_stream == 0) ||
        (adc_stream_service.adc_stream->ops == 0) ||
        (adc_stream_service.adc_stream->ops->stop == 0)) {
        adc_stream_service.state = ADC_STREAM_SERVICE_ERROR;
        return PLATFORM_NOT_READY;
    }

    /*
     * [NEW] 这里刻意不调用 adc_fir_reset()：
     * stop 之后队列里可能还有数据要继续滤波发完，保留 FIR 状态可以保证
     * 这批数据与停止前连续。下一次收到 O\0 时会在 start() 里统一 reset。
     */
    status = adc_stream_service.adc_stream->ops->stop(adc_stream_service.adc_stream);
    if (status == PLATFORM_OK) {
        adc_stream_service.state = ADC_STREAM_SERVICE_STOPPED;
        adc_stream_service.paused_for_tx = 0U;
    }

    return status;
}

/**
 * @brief 获取 service 当前状态。
 *
 * @return 当前 adc_stream_service_state_t 状态值。
 */
adc_stream_service_state_t adc_stream_service_get_state(void)
{
    return adc_stream_service.state;
}

/**
 * @brief 获取 service 统计信息。
 *
 * @return 统计信息结构体值拷贝，包含已发送、已丢弃、已执行命令数量。
 */
adc_stream_service_stats_t adc_stream_service_get_stats(void)
{
    return adc_stream_service.stats;
}

/**
 * @brief 获取 ADC 服务队列中当前缓存的字节数。
 *
 * 仅供调试遥测和背压观察使用，不参与数据通路。
 *
 * @return ADC 队列中尚未写入 USB 的字节数。
 */
uint32_t adc_stream_service_get_queue_used(void)
{
    return adc_stream_service_queue_count(adc_stream_service.adc_head,
                                          adc_stream_service.adc_tail,
                                          ADC_STREAM_SERVICE_ADC_QUEUE_SIZE);
}

/**
 * @brief 判断当前是否可以进入 Sleep 低功耗。
 *
 * 只有同时满足以下条件才允许睡眠：
 *   - service 处于 STOPPED 状态；
 *   - ADC 队列已经发空；
 *   - 没有待处理的 USB 接收命令。
 * 任何一项不满足都返回 0，防止漏掉数据或命令。
 *
 * @return 1 表示可以睡眠，0 表示必须保持轮询。
 */
uint8_t adc_stream_service_can_sleep(void)
{
    if (adc_stream_service.state != ADC_STREAM_SERVICE_STOPPED) {
        return 0U;
    }

    if (adc_stream_service_queue_count(adc_stream_service.adc_head,
                                       adc_stream_service.adc_tail,
                                       ADC_STREAM_SERVICE_ADC_QUEUE_SIZE) != 0U) {
        return 0U;
    }

    if (adc_stream_service.rx_head != adc_stream_service.rx_tail) {
        return 0U;
    }

    return 1U;
}

/**
 * @brief ADC 采样回调。
 *
 * @param stream 产生采样的 platform ADC stream，本函数不使用该参数。
 * @param samples 本次 DMA 半缓冲得到的 8bit 原始 ADC 数组。
 * @param sample_count 本次数组中的有效采样数。
 * @param context 注册回调时传入的 adc_stream_service_t 对象指针。
 *
 * 中断路径上只入队，不直接发送 USB，避免在 ADC/DMA 中断里做重活。
 */
static void adc_stream_service_sample_callback(platform_adc_stream_t *stream,
                                               const uint8_t *samples,
                                               uint32_t sample_count,
                                               void *context)
{
    adc_stream_service_t *self = (adc_stream_service_t *)context;

    (void)stream;

    /*
     * 这里不直接调用USB发送，避免在ADC中断里做过多工作。
     * 每一个采样点都会先进ADC环形队列，再由poll批量上行。
     */
    if ((self == 0) || (self->state != ADC_STREAM_SERVICE_STREAMING)) {
        return;
    }

    adc_stream_service_adc_queue_push_block(samples, sample_count);
}

/**
 * @brief USB 字节流接收回调。
 *
 * @param stream 产生接收事件的 platform byte stream，本函数不使用该参数。
 * @param data USB 收到的数据缓冲区。
 * @param length 本次收到的字节数。
 * @param context 注册回调时传入的 adc_stream_service_t 对象指针。
 *
 * 回调里只把字节放入 RX 环形队列，命令解析留给 poll 执行。
 */
static void adc_stream_service_rx_callback(platform_byte_stream_t *stream,
                                           const uint8_t *data,
                                           uint32_t length,
                                           void *context)
{
    uint32_t i;
    adc_stream_service_t *self = (adc_stream_service_t *)context;

    (void)stream;

    if ((self == 0) || (data == 0)) {
        return;
    }

    for (i = 0U; i < length; i++) {
        uint32_t next = adc_stream_service_next_index(self->rx_head, ADC_STREAM_SERVICE_RX_QUEUE_SIZE);

        if (next == self->rx_tail) {
            break;
        }

        self->rx_queue[self->rx_head] = data[i];
        self->rx_head = next;
    }
}
