/**
 * @file platform_status.h
 * @brief 平台状态码定义
 * 
 * 定义了整个平台抽象层统一使用的状态返回码，
 * 所有 platform 层 API 均使用此枚举作为返回值。
 */

#ifndef PLATFORM_STATUS_H
#define PLATFORM_STATUS_H

/**
 * @brief 平台状态枚举
 */
typedef enum {
    PLATFORM_OK = 0,            /**< 操作成功 */
    PLATFORM_ERROR = -1,        /**< 通用错误 */
    PLATFORM_BUSY = -2,         /**< 资源忙，无法执行操作 */
    PLATFORM_INVALID_PARAM = -3,/**< 参数无效 */
    PLATFORM_NOT_READY = -4     /**< 模块未初始化或未就绪 */
} platform_status_t;

#endif
