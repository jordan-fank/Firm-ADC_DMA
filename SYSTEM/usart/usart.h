#ifndef  __USART_H
#define  __USART_H

#define RX_BUFFER_SIZE 64
extern volatile uint8_t rx_buffer[RX_BUFFER_SIZE];
extern volatile uint16_t rx_index;
extern volatile uint8_t data_ready;
extern uint16_t Distence1, Speed1, Distence2, Speed2;
extern uint16_t Dir1, Dir2; // ?????1?,??uint8_t????
extern uint16_t UpDistence,DownDistence;
extern uint16_t ResetUpDown;
// ??????
extern volatile uint16_t motor_speed;
extern volatile uint16_t motor_distance;
extern volatile uint8_t motor_direction;
extern uint8_t status;
extern uint8_t control_flag;
void USART1_Init(void);
void ParseMotorCommand(void);
void USART_SendString(USART_TypeDef* USARTx, char *str);

#endif
