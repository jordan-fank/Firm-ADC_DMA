# CMSIS-DSP（最小子集）

本目录是从 ARM CMSIS 5.9.0（DSP 1.10.0）中裁剪出来的最小文件集，只用于
`SERVICE/adc_fir.c` 调用 `arm_fir_q15()`。

## 来源

- 包：`ARM.CMSIS` 5.9.0
- 路径：`CMSIS/DSP/Include`、`CMSIS/DSP/Source/FilteringFunctions`、
  `CMSIS/Core/Include`
- 许可：Apache-2.0（各文件头部保留原始 SPDX 版权声明）

## 文件

| 文件 | 说明 |
|---|---|
| `Include/arm_math_types.h`、`arm_math_memory.h` | DSP 类型/内存访问定义 |
| `Include/dsp/{none,utils,support_functions,fast_math_functions,basic_math_functions,filtering_functions}.h` | `filtering_functions.h` 的传递闭包 |
| `Core/cmsis_compiler.h`、`cmsis_armclang.h` | 编译器内建函数定义（锁定 5.9.0） |
| `Source/arm_fir_q15.c`、`arm_fir_init_q15.c` | 唯一参与编译的 DSP 源码 |

## 注意事项

1. 只 include `dsp/filtering_functions.h`，不要 include `arm_math.h`——后者会拉入
   全部 DSP 模块头文件，这里没有收录。
2. **不要**在同一个 `.c` 里同时 include `stm32f4xx.h` 和本目录的头文件：
   工程 `CORE/core_cm4_simd.h`（CMSIS 3.20）与 5.x 的 `cmsis_armclang.h` 对
   `__SMMLA` 的定义类型不同，会报 `conflicting types`。`adc_fir.c` 已按此约束编写。
3. `arm_fir_q15.c` 与 `adc_fir.c` 在工程里使用文件级 `-O2`，其余文件保持全局 `-O1`。
4. 升级 CMSIS 时同步替换本目录，并重新确认 `arm_fir_init_q15` 的“偶数抽头”约束。
