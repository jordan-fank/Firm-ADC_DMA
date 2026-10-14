#ifndef APP_MAIN_H
#define APP_MAIN_H

/**
 * @brief app 层主入口。
 *
 * 负责启动业务服务并进入主循环；main() 只调用这个入口，
 * 业务编排集中放在 APP 层，避免 USER/main.c 直接接触 service 细节。
 */
void app_main(void);

#endif
