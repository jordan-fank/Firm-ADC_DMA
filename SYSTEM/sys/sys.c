#include "sys.h"  
//////////////////////////////////////////////////////////////////////////////////	 
//本程序只供学习使用，未经作者许可，不得用于其它任何用途

//系统时钟初始化	
//包括时钟设置/中断管理/GPIO设置等
//STM32F4工程模板-库函数版本
//淘宝店铺：http://mcudev.taobao.com		
//********************************************************************************
//修改说明
//无
//////////////////////////////////////////////////////////////////////////////////  


//THUMB指令不支持汇编内联
//采用如下方法实现执行汇编指令WFI  
__attribute__((naked)) void WFI_SET(void)
{
	__asm volatile("wfi\n\tbx lr\n");
}
//关闭所有中断(但是不包括fault和NMI中断)
__attribute__((naked)) void INTX_DISABLE(void)
{
	__asm volatile("cpsid i\n\tbx lr\n");
}
//开启所有中断
__attribute__((naked)) void INTX_ENABLE(void)
{
	__asm volatile("cpsie i\n\tbx lr\n");
}
//设置栈顶地址
//addr:栈顶地址
__attribute__((naked)) void MSR_MSP(u32 addr)
{
	__asm volatile("msr msp, r0\n\tbx lr\n");
}
















