//#include <stm32f4xx.h>
//#include <string.h>
//#include <stdlib.h>
//#include <stdio.h>

//#define RX_BUFFER_SIZE 64
//volatile uint8_t rx_buffer[RX_BUFFER_SIZE];
//volatile uint16_t rx_index = 0;
//volatile uint8_t data_ready = 0;
//uint8_t status = 0;
//uint8_t control_flag = 0;

//// ??????
//volatile uint16_t motor_speed = 0;
//volatile uint16_t motor_distance = 0;
//volatile uint8_t motor_direction = 0;

////''''''''''''''''''''''''''''''''''''''
//uint16_t Distence1, Speed1, Distence2, Speed2;
//uint16_t Dir1, Dir2; // ?????1?,??uint8_t????
//uint16_t UpDistence,DownDistence;
//uint16_t ResetUpDown;


//#define MAX_DATA_LEN 10
//char dataBuffer[MAX_DATA_LEN] = {0};
//volatile uint8_t dataIndex = 0;



//void USART1_Init(void) {
//    GPIO_InitTypeDef GPIO_InitStructure;
//    USART_InitTypeDef USART_InitStructure;
//    NVIC_InitTypeDef NVIC_InitStructure;
//    
//    // 1. ????
//    RCC_AHB1PeriphClockCmd(RCC_AHB1Periph_GPIOA, ENABLE);
//    RCC_APB2PeriphClockCmd(RCC_APB2Periph_USART1, ENABLE);
//    
//    // 2. ??GPIO
//    // PA9 - USART1_TX, PA10 - USART1_RX
//    GPIO_InitStructure.GPIO_Pin = GPIO_Pin_9 | GPIO_Pin_10;
//    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_AF;
//    GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
//    GPIO_InitStructure.GPIO_OType = GPIO_OType_PP;
//    GPIO_InitStructure.GPIO_PuPd = GPIO_PuPd_UP;
//    GPIO_Init(GPIOA, &GPIO_InitStructure);
//    
//    // 3. ????
//    GPIO_PinAFConfig(GPIOA, GPIO_PinSource9, GPIO_AF_USART1);
//    GPIO_PinAFConfig(GPIOA, GPIO_PinSource10, GPIO_AF_USART1);
//    
//    // 4. ??USART
//    USART_InitStructure.USART_BaudRate = 2000000;
//    USART_InitStructure.USART_WordLength = USART_WordLength_8b;
//    USART_InitStructure.USART_StopBits = USART_StopBits_1;
//    USART_InitStructure.USART_Parity = USART_Parity_No;
//    USART_InitStructure.USART_HardwareFlowControl = USART_HardwareFlowControl_None;
//    USART_InitStructure.USART_Mode = USART_Mode_Rx | USART_Mode_Tx;
//    USART_Init(USART1, &USART_InitStructure);
//    
//    // 5. ??????
//    USART_ITConfig(USART1, USART_IT_RXNE, ENABLE);
//    
//    // 6. ??NVIC
//    NVIC_InitStructure.NVIC_IRQChannel = USART1_IRQn;
//    NVIC_InitStructure.NVIC_IRQChannelPreemptionPriority = 0;
//    NVIC_InitStructure.NVIC_IRQChannelSubPriority = 0;
//    NVIC_InitStructure.NVIC_IRQChannelCmd = ENABLE;
//    NVIC_Init(&NVIC_InitStructure);
//    
//    // 7. ??USART
//    USART_Cmd(USART1, ENABLE);
//}


//void USART1_IRQHandler(void) {
//    if (USART_GetITStatus(USART1, USART_IT_RXNE) != RESET)
//    {
//        uint8_t receivedChar = USART_ReceiveData(USART1);
//        
//        // ???'\0'???????
//        if(receivedChar == '\0' || dataIndex >= MAX_DATA_LEN-1)
//        {
//            switch(status)
//            {
//                case 0:
//                    Distence1 = (uint16_t)atoi(dataBuffer);
//                    break;
//                case 1:
//                    Speed1 = (uint16_t)atoi(dataBuffer);
//                    break;
//                case 2:
//                    Distence2 = (uint16_t)atoi(dataBuffer);
//                    break;
//                case 3:
//                    Speed2 = (uint16_t)atoi(dataBuffer);
//                    break;
//                case 4:
//                    // ????????????
//                    Dir1 = (dataBuffer[0] == '1') ? 1 : 0;
//                    break;
//                case 5:
//								{
//                    Dir2 = (dataBuffer[0] == '1') ? 1 : 0;
//                    // ????????
//                    control_flag = 1; // ??????
////										DM430_Control(Speed1, Distence1, 0);
////										DM430_Control(Speed2, Distence2 - Distence1, 0);
////										DM430_Control(Distence1, Speed1, 0);
////										DM430_Control(Distence2 + Distence1, Speed1, 1);
//                    break;
//								}
//								case 6:
//								{
//										UpDistence = (uint16_t)atoi(dataBuffer);
//										ResetUpDown += UpDistence;
//										control_flag = 2;
//										break;
//								}
//								case 7:
//								{
//										DownDistence = (uint16_t)atoi(dataBuffer);
//										ResetUpDown -= UpDistence;
//										control_flag = 3;
//										break;
//								}
//            }
//            
//            memset(dataBuffer, 0, MAX_DATA_LEN);
//            dataIndex = 0;
////            status = (status < 5) ? status + 1 : 0;
//        }
//        // ??????'\n'??????
//        else if(receivedChar == '\n')
//        {
//            status = 0;
//            memset(dataBuffer, 0, MAX_DATA_LEN);
//            dataIndex = 0;
//        }
//        else
//        {
//						if(receivedChar == 'D')
//						{
//							status = 0;
//						}
//						else if(receivedChar == 'V')
//						{
//							status = 1;
//						}
//						else if(receivedChar == 'd')
//						{
//							status = 2;
//						}
//						else if(receivedChar == 'v')
//						{
//							status = 3;
//						}
//						else if(receivedChar == 'R')
//						{
//							status = 4;
//						}
//						else if(receivedChar == 'r')
//						{
//							status = 5;
//						}
//						else if(receivedChar == 'U')
//						{
//							status = 6;
//						}
//						else if(receivedChar == 'L')
//						{
//							status = 7;
//						}
//						else if(receivedChar == 'E')
//						{
//							control_flag = 4;
//						}
//						else if(dataIndex < MAX_DATA_LEN-1)
//						{
//							dataBuffer[dataIndex++] = receivedChar;
//						}
//        }
//    }
//}
////// ????????
//void USART_SendString(USART_TypeDef* USARTx, char *str) {
//    while(*str) {
//        uint32_t timeout = 100000; // 超时计数器
//        
//        // 等待发送寄存器空
//        while(!(USARTx->SR & USART_SR_TXE)) {
//            if(--timeout == 0) {
//                // 超时处理
//                return;
//            }
//        }
//        
//        // 发送数据
//        USARTx->DR = (*str++ & 0xFF);
//    }
//    
//    // 等待传输完成
//    uint32_t timeout = 100000;
//    while(!(USARTx->SR & USART_SR_TC)) {
//        if(--timeout == 0) {
//            break;
//        }
//    }
//}




