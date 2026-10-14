#include "adc_stream_service.h"
#include "platform_board.h"
#include <string.h>

/* service 层运行参数：修改采样频率、转速刷新周期、队列大小优先看这里。 */
#define ADC_STREAM_SERVICE_TRIGGER_COUNTS 1U
/* 转速更新频率 */
#define ADC_STREAM_SERVICE_SPEED_UPDATE_MS 500U
/* PA5/B rising edge only: one hardware trigger per 4000-PPR encoder period. */
#define ADC_STREAM_SERVICE_ENCODER_COUNTS_PER_REV 4000U
#define ADC_STREAM_SERVICE_RX_QUEUE_SIZE 64U
#define ADC_STREAM_SERVICE_ADC_QUEUE_SIZE 16384U
#define ADC_STREAM_SERVICE_TX_CHUNK_SIZE 512U
/*
 * Leave room for a complete 1024-sample DMA lap plus one additional chunk.
 * Because the ring queue keeps one slot unused, two chunks alone are one byte
 * short of accepting the stop-time DMA flush.
 */
#define ADC_STREAM_SERVICE_ADC_QUEUE_PAUSE_GUARD \
    (3U * ADC_STREAM_SERVICE_TX_CHUNK_SIZE)
#define ADC_STREAM_SERVICE_SPEED_FRAME_SIZE 9U
#define ADC_STREAM_SERVICE_SPEED_FRAME_HEADER0 0xA5U
#define ADC_STREAM_SERVICE_SPEED_FRAME_HEADER1 0x5AU
#define ADC_STREAM_SERVICE_SPEED_FRAME_TYPE 0x52U
#define ADC_STREAM_SERVICE_SPEED_FRAME_PAYLOAD_LEN 0x04U

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
 * @param latest_rpm 最近一次从底层读取到的转速。
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
    uint32_t latest_rpm;
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

    /* Publish the new head only after the complete DMA half-block is copied. */
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

    count = adc_stream_service_queue_count(adc_stream_service.adc_head,
                                           adc_stream_service.adc_tail,
                                           ADC_STREAM_SERVICE_ADC_QUEUE_SIZE);
    if (count > ADC_STREAM_SERVICE_TX_CHUNK_SIZE) {
        count = ADC_STREAM_SERVICE_TX_CHUNK_SIZE;
    }

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
        return PLATFORM_OK;
    }

    /*
     * A transient USB-ring BUSY is normal backpressure. Keep adc_tail in place
     * and retry exactly the same chunk on the next poll. The 16 KiB service
     * queue absorbs the short stall; sampling is paused only at high water.
     */
    if (status == PLATFORM_BUSY) {
        adc_stream_service.stats.usb_busy_returns++;
        if (adc_stream_service.usb_busy_active == 0U) {
            adc_stream_service.usb_busy_active = 1U;
            adc_stream_service.stats.usb_busy_episodes++;
        }
        return status;
    }

    /* Non-BUSY transport errors still stop new samples to protect the queue. */
    adc_stream_service_pause_for_tx();
    return status;
}

/**
 * @brief 发送一帧转速数据。
 *
 * 帧格式为 A5 5A 52 04 + rpm 小端 4 字节 + 累加校验。
 * 转速帧不进入 ADC 数据队列，USB 当下忙时由调用者选择放弃。
 *
 * @return PLATFORM_OK 表示转速帧已经写入底层发送队列。
 */
static platform_status_t adc_stream_service_send_speed_frame(void)
{
    uint8_t frame[ADC_STREAM_SERVICE_SPEED_FRAME_SIZE];
    uint8_t i;
    uint8_t checksum;
    uint32_t rpm;

    /*
     * 转速独立成帧，沿用参考工程A5 5A 52格式。
     * 定时更新到来时只发送一次，不排队、不补发。
     */
    rpm = adc_stream_service.latest_rpm;
    frame[0] = ADC_STREAM_SERVICE_SPEED_FRAME_HEADER0;
    frame[1] = ADC_STREAM_SERVICE_SPEED_FRAME_HEADER1;
    frame[2] = ADC_STREAM_SERVICE_SPEED_FRAME_TYPE;
    frame[3] = ADC_STREAM_SERVICE_SPEED_FRAME_PAYLOAD_LEN;
    frame[4] = (uint8_t)(rpm & 0xFFU);
    frame[5] = (uint8_t)((rpm >> 8) & 0xFFU);
    frame[6] = (uint8_t)((rpm >> 16) & 0xFFU);
    frame[7] = (uint8_t)((rpm >> 24) & 0xFFU);

    checksum = 0U;
    for (i = 0U; i < (ADC_STREAM_SERVICE_SPEED_FRAME_SIZE - 1U); i++) {
        checksum = (uint8_t)(checksum + frame[i]);
    }
    frame[8] = checksum;

    return adc_stream_service_send_bytes(frame, sizeof(frame));
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

/**
 * @brief 执行已经完整解析出的上位机命令。
 *
 * @param command 命令字节，目前支持 'O' 开始采样、'C' 停止采样。
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
    }
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
        if ((byte == (uint8_t)'O') || (byte == (uint8_t)'C')) {
            adc_stream_service.pending_command = byte;
        }
    } else {
        if (byte == 0U) {
            adc_stream_service_execute_command(adc_stream_service.pending_command);
            adc_stream_service.pending_command = 0U;
        } else if ((byte == (uint8_t)'O') || (byte == (uint8_t)'C')) {
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
 * @brief 读取底层转速更新并尝试发送转速帧。
 *
 * 如果本次没有新转速或者 USB 发送空间不足，不做补发，等待下一次速度窗口。
 */
static void adc_stream_service_process_speed_update(void)
{
    platform_adc_stream_speed_t speed;

    if ((adc_stream_service.adc_stream == 0) ||
        (adc_stream_service.adc_stream->ops == 0) ||
        (adc_stream_service.adc_stream->ops->get_speed_update == 0)) {
        return;
    }

    if (adc_stream_service.adc_stream->ops->get_speed_update(adc_stream_service.adc_stream,
                                                             &speed) != PLATFORM_OK) {
        return;
    }

    if (speed.updated != 0U) {
        /*
         * 和参考工程一致：本次定时窗口结束就尝试发一帧转速。
         * 如果USB环形缓冲当下不足，本帧直接放弃，不做补发。
         */
        adc_stream_service.latest_rpm = speed.rpm;
        (void)adc_stream_service_send_speed_frame();
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
    adc_stream_service.latest_rpm = 0U;
    adc_stream_service.stats.samples_sent = 0U;
    adc_stream_service.stats.samples_dropped = 0U;
    adc_stream_service.stats.commands_received = 0U;
    adc_stream_service.stats.usb_busy_returns = 0U;
    adc_stream_service.stats.usb_busy_episodes = 0U;
    adc_stream_service.stats.adc_pause_count = 0U;
    adc_stream_service.stats.adc_resume_count = 0U;
    adc_stream_service.stats.adc_queue_peak = 0U;
    adc_stream_service_reset_adc_queue();

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
    adc_stream_service_process_speed_update();
}

/**
 * @brief 启动 ADC 数据流。
 *
 * 清空旧 ADC 队列和转速缓存后调用 platform ADC stream 的 start ops。
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

    adc_stream_service.stop_requested = 0U;
    adc_stream_service.paused_for_tx = 0U;
    adc_stream_service.usb_busy_active = 0U;
    adc_stream_service.latest_rpm = 0U;
    adc_stream_service.stats.samples_sent = 0U;
    adc_stream_service.stats.samples_dropped = 0U;
    adc_stream_service.stats.usb_busy_returns = 0U;
    adc_stream_service.stats.usb_busy_episodes = 0U;
    adc_stream_service.stats.adc_pause_count = 0U;
    adc_stream_service.stats.adc_resume_count = 0U;
    adc_stream_service.stats.adc_queue_peak = 0U;
    adc_stream_service_reset_adc_queue();

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

uint32_t adc_stream_service_get_queue_used(void)
{
    return adc_stream_service_queue_count(adc_stream_service.adc_head,
                                          adc_stream_service.adc_tail,
                                          ADC_STREAM_SERVICE_ADC_QUEUE_SIZE);
}

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
