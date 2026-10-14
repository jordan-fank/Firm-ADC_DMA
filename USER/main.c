#include "app_main.h"

/**
 * @brief 固件启动后的 C 入口函数。
 *
 * 只转入 APP 层入口，保持 USER 层启动文件尽量薄，
 * 具体业务流程由 app_main() 继续编排。
 *
 * @return 正常情况下不会返回；保留 return 0 只是满足 C 入口形式。
 */
int main(void)
{
    app_main();
    return 0;
}
