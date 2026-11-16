# ADC 采集上限与 USB 带宽计算书

> 整理日期：2026-11-10
> 适用平台：STM32F407ZGT6、STM32H743、FPGA + 外置 ADC
> 需求范围：3000–12000 rpm 实时采集 + USB 上传
> 计算依据：芯片数据手册、ST AN5354、当前仓库代码

---

## 0. 计算结论摘要

### 0.1 ADC 采集

| 平台 | 12000 rpm 下可达配置 | 最大采样率 | 对应最大转速 | 覆盖 12000 rpm |
|---|---|---|---|---|
| F407ZG（当前 8-bit/56 周期） | 8-bit | 562.5 kS/s | 8,437 rpm | **否** |
| F407ZG（8-bit/28 周期） | 8-bit | 1.000 MSPS | 15,000 rpm | 是（25% 余量） |
| F407ZG（12-bit/28 周期） | 12-bit | 900.0 kS/s | 13,500 rpm | 是（12.5% 余量） |
| H743 LQFP100 | 16-bit | 1.9 MSPS | 28,500 rpm | 是（2.4 倍余量） |
| H743 BGA | 16-bit | 3.6 MSPS | 54,000 rpm | 是（4.5 倍余量） |
| FPGA + 1 MSPS 16-bit ADC | 16-bit | 1.0 MSPS | 15,000 rpm | 是（1.25 倍余量） |
| FPGA + 15 MSPS 16-bit ADC | 16-bit | 15 MSPS | 225,000 rpm | 是（严重过剩） |

### 0.2 USB 上传

| 接口 | 实际吞吐 | 8-bit @12000 rpm（0.8 MB/s） | 12/16-bit @12000 rpm（1.6 MB/s） |
|---|---|---|---|
| USB FS | 0.5–1.0 MB/s | 边界 | **不够** |
| USB HS | 20–40 MB/s | 绰绰有余 | 够（占 4–8%） |

### 0.3 总体判断

- ADC 和 USB 是两个硬门槛，算清楚即可确定方案。
- F407ZG 当前配置覆盖不了 12000 rpm；优化采样周期后可覆盖，但 USB FS 在 12/16-bit 下不够。
- 12/16-bit + 12000 rpm 的最直接方案是 **H743 + USB HS + ULPI PHY**。
- FPGA 的 ADC/USB 都能做到，但相对当前需求能力过剩。

---

## 1. 前提与修正

### 1.1 F407ZGT6 的主频

- **芯片规格**：STM32F407ZGT6，Cortex-M4，最高 **168 MHz**。
- **本项目实际配置**：`USER/system_stm32f4xx.c` 中 HSE=8 MHz、PLL_M=8、PLL_N=288、PLL_P=2：

```
VCO    = 8 MHz / 8 × 288 = 288 MHz
SYSCLK = 288 / 2         = 144 MHz
HCLK   = 144 MHz
APB1   = 36 MHz（÷4）
APB2   = 72 MHz（÷2）
ADC 时钟 = 72 / 2 = 36 MHz
```

> 芯片支持 168 MHz，但当前工程配置为 144 MHz。两者要区分。

### 1.2 ADC 上限由 ADC 时钟决定，不由 CPU 主频决定

F407 的 ADC 最高时钟是 **36 MHz**。把 CPU 提到 168 MHz 不会让 ADC 更快：

| CPU 主频 | APB2 | ADC 分频 | ADC 时钟 | 结果 |
|---|---|---|---|---|
| 144 MHz | 72 MHz | ÷2 | **36 MHz** | ADC 跑满 |
| 168 MHz | 84 MHz | ÷2 | 42 MHz | 超规格，不允许 |
| 168 MHz | 84 MHz | ÷4 | 21 MHz | 反而降低 42% |

结论：**144 MHz 配置恰好让 ADC 跑在 36 MHz 上限**，是 ADC 吞吐的最优配置；168 MHz 只提升 CPU 处理能力，不提升 ADC 采样率。

### 1.3 转换周期修正

STM32F4 ADC 的总转换时间 = 采样周期 + 转换周期：

| 分辨率 | 转换周期 |
|---|---|
| 12-bit | 12 |
| 10-bit | 10 |
| 8-bit | **8** |
| 6-bit | 6 |

当前配置：8-bit + 56 周期采样：

```
总周期 = 56 + 8 = 64
单次时间 = 64 / 36 MHz = 1.778 µs
最大采样率 = 562.5 kS/s
```

> 此前若按 9 个转换周期计算会得到 553 kS/s，正确值为 **562.5 kS/s**。

### 1.4 计算假设

1. 每转 4000 个编码器触发（B 相上升沿，`ADC_STREAM_SERVICE_ENCODER_COUNTS_PER_REV = 4000`）。
2. 8-bit 每点 1 字节；12-bit 和 16-bit 每点按 2 字节计算。
3. ADC 上限按“无间隙连续转换”计算，不考虑触发抖动和模拟建立时间。
4. USB 数据率为纯载荷，不含协议开销；实际吞吐受主机和驱动影响。
5. H743 的 ADC 上限与封装相关，按 ST 数据手册 / AN5354 的分档计算。
6. FPGA 本身不含采集级 ADC，按外置 ADC 型号计算。

---

## 2. 采样率需求

每转 4000 个触发：

```
f_s = rpm × 4000 / 60 = rpm × 66.667
```

| 转速 | 采样率需求 | 采样间隔 |
|---|---|---|
| 3000 rpm | **200 kS/s** | 5.000 µs |
| 6000 rpm | **400 kS/s** | 2.500 µs |
| 9000 rpm | **600 kS/s** | 1.667 µs |
| 12000 rpm | **800 kS/s** | 1.250 µs |

---

## 3. ADC 上限计算

### 3.1 通用公式

```
f_s(max) = f_ADC / (SMP + N)
rpm(max) = f_s(max) × 60 / 4000 = f_s(max) × 0.015
```

- `f_ADC`：ADC 时钟频率
- `SMP`：采样周期数
- `N`：转换周期数

### 3.2 STM32F407ZG：ADC 时钟 36 MHz，最高 12-bit

| 分辨率 | 采样周期 | 总周期 | 最大采样率 | 对应最大转速 | 覆盖 12000 rpm |
|---|---|---|---|---|---|
| 8-bit | 56（当前） | 64 | 562.5 kS/s | **8,437 rpm** | **否** |
| 8-bit | 28 | 36 | 1.000 MSPS | 15,000 rpm | 是（25% 余量） |
| 8-bit | 15 | 23 | 1.565 MSPS | 23,478 rpm | 是 |
| 8-bit | 3 | 11 | 3.273 MSPS | 49,091 rpm | 是（精度受限） |
| 12-bit | 56 | 68 | 529.4 kS/s | 7,941 rpm | 否 |
| 12-bit | 28 | 40 | 900.0 kS/s | 13,500 rpm | 是（12.5% 余量） |
| 12-bit | 15 | 27 | 1.333 MSPS | 20,000 rpm | 是 |
| 12-bit | 3 | 15 | 2.400 MSPS | 36,000 rpm | 是（精度受限） |

**关键结论**

- 当前固件（8-bit + 56 周期）ADC 上限 562.5 kS/s，只覆盖到 **8,437 rpm**。
- 12000 rpm 需要 800 kS/s，ADC 只能做 562.5 kS/s，约 **30% 的触发会被漏掉**，每转不再是 4000 点。
- 要覆盖 12000 rpm：8-bit 至少把采样周期降到 28；12-bit 也要降到 28 或 15。
- 采样周期越短，对输入源阻抗和驱动能力要求越高，需要核对模拟前端。

> F407 无 16-bit ADC，16-bit 档位直接排除。

### 3.3 STM32H743：480 MHz，16-bit ADC

H743 的 ADC 上限与封装强相关：

| 封装 | 16-bit | 12-bit | 8-bit |
|---|---|---|---|
| UFBGA176 | 3.6 MSPS | 5.6 MSPS | 8.33 MSPS |
| LQFP100 | 1.9 MSPS | 4.88 MSPS | 8.33 MSPS |
| LQFP208 | 1.0 MSPS | 2.38 MSPS | 8.33 MSPS |

换算为转速上限（× 0.015）：

| 封装 | 16-bit | 12-bit | 8-bit |
|---|---|---|---|
| UFBGA176 | **54,000 rpm** | 84,000 rpm | 124,950 rpm |
| LQFP100 | **28,500 rpm** | 73,200 rpm | 124,950 rpm |
| LQFP208 | **15,000 rpm** | 35,700 rpm | 124,950 rpm |

**覆盖 12000 rpm 的能力**

- 16-bit：三种封装都能覆盖；LQFP208 余量最小（15,000 rpm，25% 余量）。
- 12-bit：余量很大（35,700–84,000 rpm）。
- 8-bit：远超需求。

> H743 的 480 MHz CPU 不改变 ADC 上限，但为 FIR、协议栈和数据处理提供余量。

### 3.4 FPGA + 外置 ADC

FPGA 本身没有采集级 ADC（内置 XADC 为 12-bit/1 MSPS 监控级），上限取决于外置 ADC：

| 外置 ADC 示例 | 分辨率 | 采样率 | 对应最大转速 | 覆盖 12000 rpm |
|---|---|---|---|---|
| AD7606B | 16-bit | 800 kSPS | 12,000 rpm | 刚好，无余量 |
| ADS8681 / ADS8860 | 16-bit | 1 MSPS | 15,000 rpm | 是（25% 余量） |
| LTC2387-16 | 16-bit | 15 MSPS | 225,000 rpm | 是（严重过剩） |
| AD9265 | 16-bit | 125 MSPS | 1,875,000 rpm | 是（严重过剩） |
| AD9226 | 12-bit | 65 MSPS | 975,000 rpm | 是（严重过剩） |

FPGA 读取这些 ADC（SPI/并行/LVDS）没有压力，800 kS/s 对 FPGA 是零头。

### 3.5 ADC 汇总

| 平台 | 最高分辨率 | 12000 rpm 可用配置 | 覆盖情况 |
|---|---|---|---|
| F407ZG（当前） | 12-bit | 8-bit/56 | 只到 8,437 rpm，不覆盖 |
| F407ZG（优化） | 12-bit | 8-bit/28 或 12-bit/28 | 覆盖，余量小 |
| H743 LQFP100 | 16-bit | 16-bit 1.9 MSPS | 覆盖，2.4 倍余量 |
| H743 BGA | 16-bit | 16-bit 3.6 MSPS | 覆盖，4.5 倍余量 |
| FPGA + 1 MSPS 16-bit ADC | 16-bit | 16-bit 1 MSPS | 覆盖，1.25 倍余量 |
| FPGA + 15 MSPS 16-bit ADC | 16-bit | 16-bit 15 MSPS | 严重过剩 |

---

## 4. USB 传输上限计算

### 4.1 USB FS / HS 能力

| 接口 | 理论带宽 | 端点包 | 理论有效吞吐 | 实际吞吐（经验） |
|---|---|---|---|---|
| USB FS | 12 Mbps = 1.5 MB/s | 64 B | 约 1.2 MB/s | **0.5–1.0 MB/s** |
| USB HS | 480 Mbps = 60 MB/s | 512 B | 约 53 MB/s | **20–40 MB/s** |

说明：

- FS 每 1 ms 一帧，bulk 64 B 包，受总线调度限制。
- HS 每 125 µs 一个 microframe，bulk 512 B 包。
- 实际值受主机驱动影响，Windows `usbser.sys` 通常偏低。

### 4.2 本项目数据率

| 转速 | 采样率 | 8-bit 数据率 | 12/16-bit 数据率 |
|---|---|---|---|
| 3000 rpm | 200 kS/s | 0.2 MB/s | 0.4 MB/s |
| 6000 rpm | 400 kS/s | 0.4 MB/s | 0.8 MB/s |
| 9000 rpm | 600 kS/s | 0.6 MB/s | 1.2 MB/s |
| 12000 rpm | 800 kS/s | **0.8 MB/s** | **1.6 MB/s** |

### 4.3 FS 覆盖判断

| 配置 | 数据率 | FS 实际 0.5–1.0 MB/s | 结论 |
|---|---|---|---|
| 8-bit @ 3000 rpm | 0.2 MB/s | 20–40% | 轻松 |
| 8-bit @ 12000 rpm | 0.8 MB/s | 80–160% | 边界 |
| 12/16-bit @ 3000 rpm | 0.4 MB/s | 40–80% | 可以 |
| 12/16-bit @ 12000 rpm | 1.6 MB/s | 160–320% | 不够 |

### 4.4 三个平台的 USB 能力

关键点：**USB 能力由板级接口决定，不是由 MCU 型号决定。**

| 平台 | FS | HS | 说明 |
|---|---|---|---|
| F407ZG | 有（PA11/PA12，当前在用） | 有 OTG_HS，需外接 ULPI PHY | 换板即可上 HS |
| H743 | 有 | 有 OTG_HS，需外接 ULPI PHY | 与 F407 相同的 HS 条件 |
| FPGA | 需外挂 | FX2 / FT232H / Zynq PS + PHY | 需要桥接芯片或 SoC |

因此：

- FS 上限（0.5–1.0 MB/s）对 F407 和 H743 完全一样。
- HS 上限（20–40 MB/s）对 F407、H743、FPGA 也基本一样，前提是外接 PHY/桥接。
- 换 H743 不会自动让 USB 变快；**换 HS 才会**。

### 4.5 USB 汇总

| 场景 | FS | HS |
|---|---|---|
| 8-bit @ 3000 rpm（0.2 MB/s） | 够 | 绰绰有余 |
| 8-bit @ 12000 rpm（0.8 MB/s） | 边界 | 绰绰有余 |
| 12/16-bit @ 3000 rpm（0.4 MB/s） | 够 | 绰绰有余 |
| 12/16-bit @ 12000 rpm（1.6 MB/s） | 不够 | 够（占 4–8%） |

---

## 5. 综合矩阵

### 5.1 平台 × 位宽 × 转速

| 平台配置 | ADC 覆盖 12000 rpm | 12-bit | 16-bit | USB 覆盖 12/16-bit@12000 | 结论 |
|---|---|---|---|---|---|
| F407 + 8-bit + FS（现状） | 否（8,437） | 否 | 否 | 0.8 MB/s 边界 | 不满足满量程 |
| F407（SMP=28）+ 8-bit + FS | 是 | — | — | 0.8 MB/s 边界 | 勉强，USB 无余量 |
| F407（SMP=28）+ 12-bit + HS + PHY | 是（13,500） | 是 | 否 | 1.6 MB/s，HS 够 | 可行，需新板 + PHY |
| H743（LQFP100）+ 16-bit + HS + PHY | 是（28,500） | 是 | 是 | 1.6 MB/s，HS 够 | **推荐** |
| FPGA + 1 MSPS 16-bit ADC + HS | 是（15,000） | 是 | 是 | 够 | 可行但能力过剩 |
| FPGA + 15 MSPS 16-bit ADC + HS | 是（225,000） | 是 | 是 | 够 | 严重过剩 |

### 5.2 CPU 处理能力（FIR 130 抽头 @ 800 kS/s）

| 平台 | 估算 CPU 占用 | 说明 |
|---|---|---|
| F407 @ 144 MHz | 约 35–70% | 余量很小，可能跑不动 |
| H743 @ 480 MHz | 约 20% | 有余量 |
| FPGA | 硬件并行，占用极低 | 开发复杂 |

---

## 6. 结论与选型建议

### 6.1 两个硬门槛

1. **ADC 门槛**：覆盖 12000 rpm 需要 800 kS/s。
   - F407 当前配置只有 562.5 kS/s，覆盖不了。
   - F407 降采样周期到 28，可做 1 MSPS 8-bit 或 900 kS/s 12-bit，覆盖但余量小。
   - H743 任意封装都能覆盖 16-bit 12000 rpm。
   - FPGA 取决于外置 ADC，选 ≥1 MSPS 即可。

2. **USB 门槛**：
   - 8-bit @ 12000 rpm = 0.8 MB/s，FS 边界；HS 轻松。
   - 12/16-bit @ 12000 rpm = 1.6 MB/s，FS 不够，必须 HS。
   - FS/HS 上限与 MCU 型号无关，HS 必须外接 ULPI PHY。

### 6.2 推荐

- **只做 8-bit、接受 FS 边界**：优化 F407 采样周期即可，不必换平台。
- **12/16-bit + 满量程 12000 rpm**：**H743 + USB HS + ULPI PHY**，余量合理。
- **FPGA**：ADC 和 USB 都能做到，但相对当前需求能力过剩，成本和周期高。

### 6.3 剩余风险

1. 每圈严格 4000 点：12000 rpm 下不能漏触发，否则图像错位。
2. 模拟前端：12/16-bit 的实际精度取决于运放、参考电压和 PCB，不只是 ADC 位数。
3. 上位机：12/16-bit 要改成二进制接收和流式处理，hex 文本会拖慢主机。
4. 固件处理：F407 在 800 kS/s 下跑 130 抽头 FIR 余量很小。

---

## 7. 附录

### 7.1 计算公式

```
采样率需求：  f_s = rpm × 4000 / 60
ADC 最大速率：f_s(max) = f_ADC / (SMP + N)
最大转速：    rpm(max) = f_s(max) × 60 / 4000
数据率：      8-bit: f_s × 1 B；12/16-bit: f_s × 2 B
```

### 7.2 关键代码位置

| 内容 | 位置 |
|---|---|
| ADC 位宽 | `VENDOR/vendor_stm32_encoder_adc.c:481` |
| ADC 采样时间 | `vendor_stm32_encoder_adc.c:29` |
| ADC 预分频 | `vendor_stm32_encoder_adc.c:476` |
| TIM2 触发配置 | `vendor_stm32_encoder_adc.c:311` |
| 每转触发数 | `SERVICE/adc_stream_service.c:34` |
| 队列与分块 | `SERVICE/adc_stream_service.c:36-37` |
| 时钟/PLL 配置 | `USER/system_stm32f4xx.c:316-331` |
| USB 端点与发送环 | `USB/USB_USER/inc/usbd_conf.h:55-60` |
| CDC 单次传输 | `USB/USB_CDC/src/usbd_cdc_core.c:91` |

### 7.3 参考资料

- ST AN5354《Getting started with 16-bit ADC on STM32H7 MCUs》
  https://www.st.com/resource/en/application_note/an5354-getting-started-with-16bit-adc-on-stm32h7-mcus-stmicroelectronics.pdf
- STM32H743 数据手册
  https://www.st.com/resource/en/datasheet/stm32h743xg.pdf
- STM32H723 数据手册
  https://www.st.com/resource/en/datasheet/stm32h723ze.pdf
- STM32F407 数据手册与 RM0090 参考手册
- USB 2.0 Specification（FS/HS bulk 传输上限）

### 7.4 实测复核方法

1. **采样率**：USART1 的 `D` 行中每秒 `(Δh + Δf) × 512` 即为实际采样点数。
2. **触发频率**：示波器测 PA5 上升沿频率，乘以 60/4000 即为 rpm。
3. **USB 吞吐**：上位机统计每秒接收字节数。
4. **丢样**：观察 `D` 行的 `dr`，长期应为 0。

> 文档中的 H743 上限按数据手册典型条件计算，实际值随封装、温度和通道类型变化，选型时需按具体型号复核。
