#include "usbd_cdc_vcp.h"
#include "vendor_usb_cdc.h"

static uint16_t VCP_Init(void);
static uint16_t VCP_DeInit(void);
static uint16_t VCP_Ctrl(uint32_t Cmd, uint8_t *Buf, uint32_t Len);
static uint16_t VCP_DataTx(uint16_t *Buf, uint32_t Len);
static uint16_t VCP_DataRx(uint8_t *Buf, uint32_t Len);

static LINE_CODING linecoding = {
    115200U,
    0x00U,
    0x00U,
    0x08U
};

CDC_IF_Prop_TypeDef VCP_fops = {
    VCP_Init,
    VCP_DeInit,
    VCP_Ctrl,
    VCP_DataTx,
    VCP_DataRx
};

uint16_t VCP_SendData(const uint8_t *data, uint32_t length)
{
    vendor_usb_cdc_status_t status = vendor_usb_cdc_write(data, length);

    return (status == VENDOR_USB_CDC_OK) ? USBD_OK : USBD_FAIL;
}

static uint16_t VCP_Init(void)
{
    return USBD_OK;
}

static uint16_t VCP_DeInit(void)
{
    return USBD_OK;
}

static uint16_t VCP_Ctrl(uint32_t Cmd, uint8_t *Buf, uint32_t Len)
{
    (void)Len;

    switch (Cmd) {
    case SET_LINE_CODING:
        linecoding.bitrate = (uint32_t)(Buf[0] | (Buf[1] << 8) | (Buf[2] << 16) | (Buf[3] << 24));
        linecoding.format = Buf[4];
        linecoding.paritytype = Buf[5];
        linecoding.datatype = Buf[6];
        break;

    case GET_LINE_CODING:
        Buf[0] = (uint8_t)(linecoding.bitrate);
        Buf[1] = (uint8_t)(linecoding.bitrate >> 8);
        Buf[2] = (uint8_t)(linecoding.bitrate >> 16);
        Buf[3] = (uint8_t)(linecoding.bitrate >> 24);
        Buf[4] = linecoding.format;
        Buf[5] = linecoding.paritytype;
        Buf[6] = linecoding.datatype;
        break;

    case SEND_ENCAPSULATED_COMMAND:
    case GET_ENCAPSULATED_RESPONSE:
    case SET_COMM_FEATURE:
    case GET_COMM_FEATURE:
    case CLEAR_COMM_FEATURE:
    case SET_CONTROL_LINE_STATE:
    case SEND_BREAK:
    default:
        break;
    }

    return USBD_OK;
}

static uint16_t VCP_DataTx(uint16_t *Buf, uint32_t Len)
{
    return VCP_SendData((const uint8_t *)Buf, Len);
}

static uint16_t VCP_DataRx(uint8_t *Buf, uint32_t Len)
{
    vendor_usb_cdc_handle_rx_packet(Buf, Len);
    return USBD_OK;
}

void EVAL_COM_IRQHandler(void)
{
}
