/*-----------------------------------------------------------------------*/
/* Low level disk I/O module skeleton for FatFs     (C)ChaN, 2013        */
/*-----------------------------------------------------------------------*/
/* If a working storage control module is available, it should be        */
/* attached to the FatFs via a glue function rather than modifying it.   */
/* This is an example of glue functions to attach various exsisting      */
/* storage control module to the FatFs module with a defined API.        */
/*-----------------------------------------------------------------------*/

#include "diskio.h"		/* FatFs lower layer API */
#include "sdio_sdcard.h"
//#include "w25qxx.h"
#include "malloc.h"		
//#include "spi.h"
//////////////////////////////////////////////////////////////////////////////////	 
 
//FATFS底层(diskio) 驱动代码	   
//STM32F4工程模板-库函数版本
//淘宝店铺：http://mcudev.taobao.com								  
////////////////////////////////////////////////////////////////////////////////// 	 


#define SD_CARD	 0  //SD卡,卷标为0
#define EX_FLASH 1	//外部flash,卷标为1

#define FLASH_SECTOR_SIZE 	512			  
//对于W25Q128
//前12M字节给fatfs用,12M字节后,用于存放字库,字库占用3.09M.	剩余部分,给客户自己用	 			    
u16	    FLASH_SECTOR_COUNT=2048*12;	//W25Q1218,前12M字节给FATFS占用
#define FLASH_BLOCK_SIZE   	8     	//每个BLOCK有8个扇区

//初始化磁盘
DSTATUS disk_initialize (BYTE pdrv)
{
	if (pdrv != SD_CARD) return STA_NOINIT;  // ???SD?
	
	if(SD_Init() != 0)       // SD????
		return STA_NOINIT;   // ?????
	else 
		return 0;            // ?????
}  


//获得磁盘状态
DSTATUS disk_status (BYTE pdrv)
{ 
	return (pdrv == SD_CARD) ? 0 : STA_NOINIT;
} 

//读扇区
//drv:磁盘编号0~9
//*buff:数据接收缓冲首地址
//sector:扇区地址
//count:需要读取的扇区数
DRESULT disk_read (
	BYTE pdrv,		// ??????
	BYTE *buff,		// ?????
	DWORD sector,	// ????(LBA)
	UINT count		// ????(1..128)
)
{
	if (pdrv != SD_CARD || !count) 
		return RES_PARERR;	// ????
	
	u8 res = SD_ReadDisk(buff, sector, count);
	while(res) {  // ???????
		SD_Init();	          // ?????SD?
		res = SD_ReadDisk(buff, sector, count);	
	}
	
	return (res == 0) ? RES_OK : RES_ERROR;
}

//写扇区
//drv:磁盘编号0~9
//*buff:发送数据首地址
//sector:扇区地址
//count:需要写入的扇区数
#if _USE_WRITE
DRESULT disk_write (
	BYTE pdrv,			// ??????
	const BYTE *buff,	// ???????
	DWORD sector,		// ????(LBA)
	UINT count			// ????(1..128)
)
{
	if (pdrv != SD_CARD || !count) 
		return RES_PARERR;	// ????
	
	u8 res = SD_WriteDisk((u8*)buff, sector, count);
	while(res) {  // ???????
		SD_Init();	          // ?????SD?
		res = SD_WriteDisk((u8*)buff, sector, count);	
	}
	
	return (res == 0) ? RES_OK : RES_ERROR;
}
#endif


//其他表参数的获得
 //drv:磁盘编号0~9
 //ctrl:控制代码
 //*buff:发送/接收缓冲区指针
#if _USE_IOCTL
DRESULT disk_ioctl (
	BYTE pdrv,		// ??????
	BYTE cmd,		// ????
	void *buff		// ?????
)
{
	if(pdrv != SD_CARD) 
		return RES_PARERR;  // ??????
	
	switch(cmd) {
	case CTRL_SYNC:         // ????
		return RES_OK;
		
	case GET_SECTOR_SIZE:   // ??????
		*(DWORD*)buff = 512;
		return RES_OK;
		
	case GET_BLOCK_SIZE:    // ???????
		*(WORD*)buff = SDCardInfo.CardBlockSize;
		return RES_OK;
		
	case GET_SECTOR_COUNT:  // ??????
		*(DWORD*)buff = SDCardInfo.CardCapacity/512;
		return RES_OK;
		
	default:
		return RES_PARERR;  // ????
	}
}
#endif
//获得时间
//User defined function to give a current time to fatfs module      */
//31-25: Year(0-127 org.1980), 24-21: Month(1-12), 20-16: Day(1-31) */                                                                                                                                                                                                                                          
//15-11: Hour(0-23), 10-5: Minute(0-59), 4-0: Second(0-29 *2) */                                                                                                                                                                                                                                                
DWORD get_fattime (void)
{				 
	return 0;
}			 
//动态分配内存
void *ff_memalloc (UINT size)			
{
	return (void*)mymalloc(SRAMIN,size);
}
//释放内存
void ff_memfree (void* mf)		 
{
	myfree(SRAMIN,mf);
}

















