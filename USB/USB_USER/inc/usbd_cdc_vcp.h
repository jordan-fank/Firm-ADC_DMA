#ifndef __USBD_CDC_VCP_H
#define __USBD_CDC_VCP_H

#include "usbd_cdc_core.h"
#include "usbd_conf.h"

typedef struct {
    uint32_t bitrate;
    uint8_t format;
    uint8_t paritytype;
    uint8_t datatype;
} LINE_CODING;

#define DEFAULT_CONFIG 0
#define OTHER_CONFIG 1

#define EVAL_COM_IRQHandler USART3_IRQHandler

extern CDC_IF_Prop_TypeDef VCP_fops;

uint16_t VCP_SendData(const uint8_t *data, uint32_t length);
void EVAL_COM_IRQHandler(void);

#endif
