#ifndef  __FUNCTION_H
#define  __FUNCTION_H

#define WRITE_BUFFER_SIZE 256

int init_sd_fatfs(void);
int write_to_file(const char* filename, const char* content);
int append_to_file(const char* filename, const char* content);
int read_from_file(const char* filename, char* buffer, UINT buffer_size);
int list_directory(const char* path);
int append_array_to_file(const char* filename, const uint8_t* array, uint32_t size);
void write_adc_to_file_text(uint16_t adc_value);
int write_array_text(const char* filename, const uint8_t* array, uint32_t size);
#endif
