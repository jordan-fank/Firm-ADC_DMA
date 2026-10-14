#include <stm32f4xx.h>
#include "malloc.h" 
#include "ff.h"  
#include "exfuns.h" 
#include "sdio_sdcard.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

FATFS fatfs;   // FATFS??????
FIL myfile;    // ????
FRESULT res;   // ????



int init_sd_fatfs(void)
{
    // ???SD???
    if(SD_Init() != 0) {
        return 1;
    }
    
    // ??????
    res = f_mount(&fatfs, "0:", 1); // "0:" ??SD?
    if(res != FR_OK) {
        return 2;
    }
    
    return 0;
}

// ?????????
int write_to_file(const char* filename, const char* content)
{
    // ????(?????)
    res = f_open(&myfile, filename, FA_CREATE_ALWAYS | FA_WRITE);
    if(res != FR_OK) {
        return 1;
    }
    
    // ????
    UINT bytes_written;
    res = f_write(&myfile, content, strlen(content), &bytes_written);
    if(res != FR_OK) {
        f_close(&myfile);
        return 2;
    }
    
    // ????
    f_close(&myfile);
    
    return 0;
}
// ??????
int append_to_file(const char* filename, const char* content)
{
    // ????(????????)
    res = f_open(&myfile, filename, FA_OPEN_ALWAYS | FA_WRITE);
    if(res != FR_OK) return 1;
    
    // ?????????
    res = f_lseek(&myfile, f_size(&myfile));
    if(res != FR_OK) {
        f_close(&myfile);
        return 2;
    }
    
    // ????
    UINT bytes_written;
    res = f_write(&myfile, content, strlen(content), &bytes_written);
    if(res != FR_OK || bytes_written != strlen(content)) {
        f_close(&myfile);
        return 3;
    }
    
    // ????
    f_close(&myfile);
    return 0;
}

// ??????
int read_from_file(const char* filename, char* buffer, UINT buffer_size)
{
    // ????
    res = f_open(&myfile, filename, FA_READ);
    if(res != FR_OK) {
        return 1;
    }
    
    // ??????
    UINT bytes_read;
    res = f_read(&myfile, buffer, buffer_size-1, &bytes_read);
    if(res != FR_OK) {
        f_close(&myfile);
        return 2;
    }
    
    // ????
    f_close(&myfile);
    
    // ????????
    buffer[bytes_read] = '\0';
    
    return bytes_read;
}

// ??????
int list_directory(const char* path)
{
    DIR dir;
    FILINFO fno;
    
    res = f_opendir(&dir, path);
    if(res != FR_OK) {
        return 1;
    }
    
    printf("Directory listing of %s:\r\n", path);
    
    for(;;) {
        res = f_readdir(&dir, &fno);
        if(res != FR_OK || fno.fname[0] == 0) break;
        
        if(fno.fattrib & AM_DIR) {
            printf("DIR: %s\r\n", fno.fname);
        } else {
            printf("FILE: %s (%d bytes)\r\n", fno.fname, (int)fno.fsize);
        }
    }
    
    f_closedir(&dir);
    return 0;
}
int append_array_to_file(const char* filename, const uint8_t* array, uint32_t size) {
    FIL myfile; // ??????
    FRESULT res;

    // ????(???FA_OPEN_APPEND???)
    res = f_open(&myfile, filename, FA_OPEN_ALWAYS | FA_WRITE);
    if (res != FR_OK) return 1;

    // ???????
    res = f_lseek(&myfile, f_size(&myfile));
    if (res != FR_OK) {
        f_close(&myfile);
        return 2;
    }

    // ????
    UINT bytes_written;
    res = f_write(&myfile, array, size, &bytes_written);
    if (res != FR_OK || bytes_written != size) {
        f_close(&myfile);
        return 3;
    }

    // ????
    f_close(&myfile);
    return 0;
}

void write_adc_to_file_text(uint16_t adc_value) {//txt write
    FIL file;
    FRESULT res;
    char buffer[32]; // ????ADC???????
    
    // ????(????????,?????)
    res = f_open(&file, "adc_logL.txt", FA_OPEN_ALWAYS | FA_WRITE);
    if(res != FR_OK) {
        // ????
        return;
    }
    
    // ???????
    res = f_lseek(&file, f_size(&file));
    if(res != FR_OK) {
        f_close(&file);
        return;
    }
    
    // ???ADC?????
    int len = sprintf(buffer, "%u\r\n", adc_value); // ?????,??????
    
    // ????
    UINT bytes_written;
    res = f_write(&file, buffer, len, &bytes_written);
    
    // ????
    f_close(&file);
}

//int write_array_text(const char* filename, const uint8_t* array, uint32_t size) {
//    FIL file;
//    FRESULT res = f_open(&file, filename, FA_CREATE_ALWAYS | FA_WRITE);
//    if(res != FR_OK) return 1;
//    
//    char buffer[64]; // ????
//    uint32_t offset = 0;
//    
//    for(uint32_t i = 0; i < size; i++) {
//        // ???????
//        int len = sprintf(buffer + offset, "%d", array[i]);
//        offset += len;
//        
//        // ???????(????????)
//        if(i < size - 1) {
//            buffer[offset++] = ',';
//        }
//        
//        // ????16????????????
//        if((i > 0 && (i+1) % 16 == 0) || offset > sizeof(buffer) - 10) {
//            buffer[offset++] = '\n';
//            UINT bytes_written;
//            res = f_write(&file, buffer, offset, &bytes_written);
//            if(res != FR_OK || bytes_written != offset) {
//                f_close(&file);
//                return 2;
//            }
//            offset = 0;
//        }
//    }
//    
//    // ??????
//    if(offset > 0) {
//        buffer[offset++] = '\n';
//        UINT bytes_written;
//        res = f_write(&file, buffer, offset, &bytes_written);
//        if(res != FR_OK || bytes_written != offset) {
//            f_close(&file);
//            return 3;
//        }
//    }
//    
//    f_close(&file);
//    return 0;
//}
#define WRITE_BUFFER_SIZE 256

//int write_array_text(const char* filename, const uint8_t* array, uint32_t size) {
//    FIL file;
//    FRESULT res = f_open(&file, filename, FA_CREATE_ALWAYS | FA_WRITE);
//    if(res != FR_OK) return 1;
//    
//    char write_buffer[WRITE_BUFFER_SIZE];
//    uint32_t buffer_offset = 0;
//    UINT bytes_written;
//    
//    for(uint32_t i = 0; i < size; i++) {
//        // ???????
//        int len;
//        if(i == 0) {
//            len = sprintf(write_buffer + buffer_offset, "%d", array[i]);
//        } else {
//            len = sprintf(write_buffer + buffer_offset, " %d", array[i]);
//        }
//        buffer_offset += len;
//        
//        // ?16?????
//        if((i > 0 && (i+1) % 16 == 0)) {
//            if(buffer_offset < WRITE_BUFFER_SIZE - 2) {
//                write_buffer[buffer_offset++] = '\r';
//                write_buffer[buffer_offset++] = '\n';
//            }
//        }
//        
//        // ????????????????
//        if(buffer_offset > WRITE_BUFFER_SIZE - 20 || i == size - 1) {
//            // ???????????
//            if(i == size - 1 && buffer_offset < WRITE_BUFFER_SIZE - 2) {
//                write_buffer[buffer_offset++] = '\r';
//                write_buffer[buffer_offset++] = '\n';
//            }
//            
//            res = f_write(&file, write_buffer, buffer_offset, &bytes_written);
//            if(res != FR_OK || bytes_written != buffer_offset) {
//                f_close(&file);
//                return 2;
//            }
//            buffer_offset = 0;
//        }
//    }
//    
//    f_close(&file);
//    return 0;
//}
int write_array_text(const char* filename, const uint8_t* array, uint32_t size) {
  FIL file;
    FRESULT res;
    
    // ????(????)
    res = f_open(&file, filename, FA_OPEN_ALWAYS | FA_WRITE);
    if(res != FR_OK) return 1;
    
    // ???????
    res = f_lseek(&file, f_size(&file));
    if(res != FR_OK) {
        f_close(&file);
        return 2;
    }
    
    char write_buffer[WRITE_BUFFER_SIZE];
    uint32_t buffer_offset = 0;
    UINT bytes_written;
    
    for(uint32_t i = 0; i < size; i++) {
        // ???????
        int len;
        if(i == 0) {
            len = sprintf(write_buffer + buffer_offset, "%d", array[i]);
        } else {
            len = sprintf(write_buffer + buffer_offset, " %d", array[i]);
        }
        buffer_offset += len;
        
        // ?16?????
        if((i > 0 && (i+1) % 16 == 0)) {
            if(buffer_offset < WRITE_BUFFER_SIZE - 2) {
                write_buffer[buffer_offset++] = '\r';
                write_buffer[buffer_offset++] = '\n';
            }
        }
        
        // ????????????????
        if(buffer_offset > WRITE_BUFFER_SIZE - 20 || i == size - 1) {
            // ??????????????
            if(i != size - 1 && buffer_offset < WRITE_BUFFER_SIZE - 2) {
                write_buffer[buffer_offset++] = '\r';
                write_buffer[buffer_offset++] = '\n';
            }
            
            res = f_write(&file, write_buffer, buffer_offset, &bytes_written);
            if(res != FR_OK || bytes_written != buffer_offset) {
                f_close(&file);
                return 3;
            }
            buffer_offset = 0;
        }
    }
    
    f_close(&file);
    return 0;
}
