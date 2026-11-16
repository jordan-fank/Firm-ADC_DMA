/**
 * @file adc_stream_service.h
 * @brief 服务层对外接口：ADC 数据流的启动、停止、轮询与状态查询。
 *
 * 上层（APP）只需要包含本头文件，不感知 USB CDC、ADC/DMA 等硬件细节。
 */

#ifndef ADC_STREAM_SERVICE_H
#define ADC_STREAM_SERVICE_H

#include <stdint.h>
#include "platform_status.h"

/**
 * @brief ADC 数据流服务状态。
 */
typedef enum {
    ADC_STREAM_SERVICE_UNINIT = 0,
    ADC_STREAM_SERVICE_STOPPED,
    ADC_STREAM_SERVICE_STREAMING,
    ADC_STREAM_SERVICE_PAUSED,
    ADC_STREAM_SERVICE_ERROR
} adc_stream_service_state_t;

/**
 * @brief ADC 数据流服务统计信息。
 *
 * @param samples_sent 已经成功写入 USB 发送环形缓冲区的 ADC 字节数。
 * @param samples_dropped ADC service 队列满时丢弃的采样字节数。
 * @param commands_received 已解析并执行的上位机命令数量。
 * @param usb_busy_returns USB 发送返回 BUSY 的累计次数。
 * @param usb_busy_episodes 连续 BUSY 的段数（从第一次 BUSY 到恢复成功算一段）。
 * @param adc_pause_count 因背压暂停 ADC 采样的次数。
 * @param adc_resume_count 背压解除后恢复 ADC 采样的次数。
 * @param adc_queue_peak ADC 服务队列的历史最高水位（字节）。
 */
typedef struct {
    uint32_t samples_sent;
    uint32_t samples_dropped;
    uint32_t commands_received;
    uint32_t usb_busy_returns;
    uint32_t usb_busy_episodes;
    uint32_t adc_pause_count;
    uint32_t adc_resume_count;
    uint32_t adc_queue_peak;
} adc_stream_service_stats_t;

/**
 * @brief 初始化 ADC 数据流服务及其依赖的 platform 对象。
 *
 * @return PLATFORM_OK 表示 USB、字节流、ADC 采样流全部初始化成功。
 */
platform_status_t adc_stream_service_init(void);

/**
 * @brief service 层轮询入口。
 *
 * 主循环需要持续调用它，用于处理 USB 接收命令、ADC 队列发送和转速帧更新。
 */
void adc_stream_service_poll(void);

/**
 * @brief 开始编码器触发 ADC 采样并清空本地发送队列。
 *
 * @return PLATFORM_OK 表示底层采样流已经进入运行状态。
 */
platform_status_t adc_stream_service_start(void);

/**
 * @brief 停止底层 ADC 采样。
 *
 * stop 只阻止新的采样，service 仍会在 poll 中继续发送队列里已经缓存的数据。
 *
 * @return PLATFORM_OK 表示底层采样流已经停止。
 */
platform_status_t adc_stream_service_stop(void);

/**
 * @brief 获取 ADC 数据流服务当前状态。
 *
 * @return 当前 service 状态。
 */
adc_stream_service_state_t adc_stream_service_get_state(void);

/**
 * @brief 获取 ADC 数据流服务统计信息快照。
 *
 * @return 当前统计计数的值拷贝。
 */
adc_stream_service_stats_t adc_stream_service_get_stats(void);

/**
 * @brief 获取 ADC 服务队列中当前等待发送的字节数。
 *
 * @return 队列中尚未写入 USB 的 ADC 字节数。
 */
uint32_t adc_stream_service_get_queue_used(void);

/**
 * @brief 判断 CPU 是否可以进入 Sleep（USB 保持供电）。
 *
 * @return 1 表示队列和命令都已处理完，可以睡眠；0 表示仍需继续轮询。
 */
uint8_t adc_stream_service_can_sleep(void);

#endif
