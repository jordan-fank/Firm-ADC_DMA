# ADC 采集与上传方案对比

> 整理日期：2026-11-10
> 依据：当前仓库代码（`feat/host-tool` 工作区）、ST 公开数据手册与应用笔记
> 适用对象：本项目编码器触发 ADC 采集 + USB 上传链路的选型与升级评估

---

## 0. 结论摘要（先看这里）

1. **当前方案（STM32F407 + 8-bit + USB FS）在典型工况下够用**：3000 rpm 时 200 kS/s、200 kB/s，离 USB FS 上限还很远。
2. **当前方案在上限工况下接近极限**：12000 rpm 时 800 kS/s、800 kB/s，USB FS 已接近实际吞吐上限。
3. **STM32H7 + 12/16-bit + USB HS 在性能上绰绰有余**：ADC 占用约 22%，USB 带宽占用约 4–8%，FIR 占用约 20%。
4. **H7 方案的主要成本不在带宽**，而在外部 ULPI PHY、PCB 重做、Cortex-M7 的 D-Cache 一致性、上位机二进制协议改造。
5. **FPGA 方案能力过剩**：当前是单通道、800 kS/s、离线成像，用不上 FPGA 的并行和确定性时序优势；纯 FPGA 还会推翻现有 C 分层架构。
6. **推荐路线**：先在 F4 上验证 12-bit → 视最高转速选择 H7（内置 FS 或外接 PHY 的 HS）→ 只有出现多通道/超高速/实时处理需求时再评估 FPGA。

---

## 1. 现状基线

### 1.1 数据链

```
编码器 PA5 上升沿
      │ TIM2_CH1 硬件触发
      ▼
   ADC1 (PA2, 8-bit) ──DMA2 Stream0 (1024 半字循环)──▶ 半满/满中断
                                                        │ 每次 512 点
                                                        ▼
                                    SERVICE 16 KB 环形队列（中断只入队）
                                                        │ 主循环 poll 每次 512 B
                                                        ▼
                                    USB CDC 发送环 32 KB ──▶ 上位机
                                                        │
PB12 Z 相下降沿 ──▶ TIM5 (1 MHz) 时间戳 ──▶ rpm ──▶ USART1 调试行 "D ..."
```

### 1.2 关键参数

| 项目 | 当前值 | 位置 |
|---|---|---|
| MCU | STM32F407VETx，Cortex-M4F，144 MHz | — |
| 外部晶振 | 8 MHz（README 写的 12 MHz 有误） | `USER/stm32f4xx.h:123` |
| ADC | 单通道 PA2，8-bit，采样 56 周期 | `VENDOR/vendor_stm32_encoder_adc.c:481` |
| ADC 时钟 | 36 MHz（APB2 72 MHz ÷ 2） | `vendor_stm32_encoder_adc.c:476` |
| 触发 | PA5 上升沿 → TIM2_CH1 → TRGO | `vendor_stm32_encoder_adc.c:311` |
| DMA | 1024 半字循环缓冲，半满/满各 512 点 | `vendor_stm32_encoder_adc.c:36` |
| 服务队列 | 16 KB | `SERVICE/adc_stream_service.c:36` |
| USB | OTG_FS 12 Mbps，CDC，64 B 包 | `USB/USB_USER/inc/usbd_conf.h:55` |
| USB 发送环 | 32 KB | `usbd_conf.h:59` |
| 单次 IN 传输 | 512 B | `USB/USB_CDC/src/usbd_cdc_core.c:91` |
| 每转触发数 | 4000 | `SERVICE/adc_stream_service.c:34` |
| 当前资源占用 | Flash 约 33 KB / RAM 约 58 KB（开 FIR） | 编译输出 |

### 1.3 控制协议

| 指令 | 字节 | 作用 |
|---|---|---|
| 启动采集 | `4F 00` | 启动 TIM2 → ADC → DMA |
| 停止采集 | `43 00` | 停底层，队列发空 |
| 使能 PB11 | `45 00` | 板级 3 V 电源轨 |
| 失能 PB11 | `44 00` | — |
| 打开 FIR | `46 00` | 仅 `ADC_FIR_ENABLE=1` |
| 关闭 FIR | `52 00` | RAW 旁路 |

---

## 2. 采样率与数据量计算

### 2.1 采样率

ADC 由编码器边沿触发，每个 PA5 上升沿转换一次，没有分频：

```
f_s = 转速(r/s) × 4000 = RPM × 4000 / 60
```

| 转速 | 采样率 | 采样间隔 |
|---|---|---|
| 600 rpm | 40 kS/s | 25 µs |
| 1500 rpm | 100 kS/s | 10 µs |
| **3000 rpm（典型）** | **200 kS/s** | **5 µs** |
| 6000 rpm | 400 kS/s | 2.5 µs |
| **12000 rpm（上限）** | **800 kS/s** | **1.25 µs** |

### 2.2 F407 的 ADC 转换上限

- 8-bit 转换：56 周期采样 + 9 周期转换 = 65 周期
- ADC 时钟 36 MHz → 单次约 **1.8 µs**
- 理论最高约 **553 kS/s**
- 换算转速约 **8300 rpm**；超过后 ADC 跟不上触发，会漏采样

> 12000 rpm 是测速逻辑的上限，不是 ADC 连续转换的上限。

### 2.3 位宽与数据率

| 位宽 | 量化级数 | 每点字节 | 800 kS/s 数据率 |
|---|---|---|---|
| 8-bit | 256 | 1 B | 0.8 MB/s |
| 12-bit | 4096 | 2 B | 1.6 MB/s |
| 16-bit | 65536 | 2 B | 1.6 MB/s |

> 12-bit 通常按 16-bit 对齐存放，所以 12-bit 和 16-bit 的传输量相同。

---

## 3. USB 传输限制分析

当前限制是四层因素叠加，不是单一 bug。

### 3.1 硬件层

| 项目 | 值 |
|---|---|
| 控制器 | OTG_FS，12 Mbps |
| 理论速率 | 1.5 MB/s |
| Bulk 实际吞吐 | 约 0.5–1.0 MB/s |
| IN 端点包 | 64 B |
| 帧周期 | 1 ms |

### 3.2 协议栈层

- 单次 IN 传输最多 512 B（拆成 8 个 64 B 包）
- 发送完成中断后立即续传，不是每帧只发一次
- 32 KB 发送环的缓冲深度：
  - 200 kB/s → 约 160 ms
  - 800 kB/s → 约 40 ms
- 环空间不足时 `vendor_usb_cdc_write()` 返回 `BUSY`

### 3.3 背压链

```
32 KB 发送环满 → BUSY → service 保留 tail 重发
              → 16 KB 队列涨到约 14.8 KB → 暂停 ADC
              → 队列真满 → 丢弃整块，dr 计数增加
```

### 3.4 观测方法

USART1 的 `D ...` 行：

| 字段 | 含义 | 健康值 |
|---|---|---|
| `ub` | USB BUSY 返回次数 | 偶尔增长正常 |
| `ue` | 连续 BUSY 段数 | 少量 |
| `p/rs` | ADC 暂停/恢复次数 | 少量 |
| `q/pk` | 队列当前/峰值水位 | 不持续接近满 |
| `dr` | **丢弃字节数** | **长期为 0** |

`ub` 增长但 `dr = 0` 表示背压生效、没有丢数据。

### 3.5 主机侧

上位机把每个字节转成 hex 文本保存，数据量放大约 3 倍。800 kB/s 会变成约 2.4 MB/s 文本写入，主机侧可能先饱和。

### 3.6 数据率对照

| 配置 | 数据率 | USB FS 状况 |
|---|---|---|
| 8-bit @ 200 kS/s | 200 kB/s | 轻松 |
| 8-bit @ 800 kS/s | 800 kB/s | 接近极限 |
| 12-bit @ 200 kS/s | 400 kB/s | 可以 |
| 12/16-bit @ 800 kS/s | 1.6 MB/s | **不够** |

---

## 4. 候选方案

### 4.1 方案 A：STM32F407 + 8-bit + USB FS（现状）

**配置**：144 MHz、8-bit ADC、USB FS、16 KB 队列、32 KB 发送环。

**优点**
- 现有代码和硬件成熟，无需改动
- 典型 200 kS/s 工况余量充足
- 分层架构清晰，维护成本低

**局限**
- 8-bit 量化较粗（3.3 V 参考下约 12.9 mV/LSB）
- 800 kS/s 时 USB 接近饱和
- ADC 转换上限约 553 kS/s，8300 rpm 以上会漏采样

**适用**：典型转速、对图像精度要求不高的现有产线。

---

### 4.2 方案 B：STM32H7 + 12/16-bit + USB HS + ULPI PHY

**配置**：H7（480/550 MHz）、12/16-bit ADC、USB HS、外接 USB3300 类 PHY。

**H7 ADC 能力（以 H743/H750 为例）**

| 封装 | 16-bit | 12-bit | 8-bit |
|---|---|---|---|
| UFBGA176 | 3.6 MSPS | 5.6 MSPS | 8.33 MSPS |
| LQFP100 | 1.9 MSPS | 4.88 MSPS | 8.33 MSPS |
| LQFP208 | 1.0 MSPS | 2.38 MSPS | 8.33 MSPS |

H723/H725 一档：2× 16-bit ADC 3.6 MSPS，双交错 7.2 MSPS，另有 12-bit ADC 最高 5 MSPS。

> 具体上限与封装、温度、通道类型有关，选型时需按具体型号数据手册复核。

**余量核算（按 800 kS/s、12/16-bit）**

| 资源 | 需求 | H7 能力 | 占用 |
|---|---|---|---|
| ADC | 800 kS/s | 3.6–5.6 MSPS | 约 22% |
| USB | 1.6 MB/s | HS 实际 20–40 MB/s | 约 4–8% |
| FIR | 约 104M MAC/s | 550 MHz + DSP SIMD | 约 20% |
| RAM | 16–64 KB 队列 | 564 KB–1 MB | 充足 |

**带宽结论**：USB HS 毫无压力，1.6 MB/s 只占个位数百分比。

**真正的工程成本**

1. **外部 ULPI PHY**：H7 的 OTG_HS 无内置高速 PHY，必须外接（USB3300 等），12 根信号线。
2. **PCB 重做**：H7 与 F407 非 pin-to-pin；USB HS 需 90 Ω 差分、等长布线；电源和 ADC 参考需重设。
3. **D-Cache 一致性**：Cortex-M7 有数据缓存，DMA 缓冲区必须配置为 non-cacheable，或在 DMA 前后做 cache 维护；注意 DTCM 不能被普通 DMA 访问。
4. **USB HS 协议栈适配**：ULPI 引脚/时钟、HS 端点包 512 B、中断优先级、DMA。
5. **上位机改造**：12/16-bit 为 2 字节/点，需要二进制接收与 `uint16` 解析，不能再走 hex 文本。

**12-bit 与 16-bit 的收益边界**

- 12-bit 相比 8-bit：量化级提升 16 倍，效果明显，是性价比最高的升级。
- 16-bit 的 ENOB 通常约 13–14 bit，不是 16 bit 全有效；收益受运放、参考电压、PCB 布局和传感器噪声限制。
- 12-bit 与 16-bit 传输量相同，**选型依据是模拟精度，不是带宽**。

**适用**：需要满速 800 kS/s 且要 12/16-bit 质量的新一代产品。

---

### 4.3 方案 Q：STM32H7 + 12-bit + USB FS（折中）

**配置**：H7 内置 USB FS，12-bit ADC。

**适用条件**

- 最高转速不超过约 3000–4000 rpm
- 12-bit @ 200–300 kS/s ≈ 0.4–0.6 MB/s，USB FS 可覆盖

**优点**

- 不需要外部 ULPI PHY，PCB 改动小
- 复用现有 USB 协议栈和上位机（只需处理 2 字节/点）
- 成本接近方案 A

**局限**

- 满速 800 kS/s、12-bit 时仍会超 USB FS
- 无法覆盖 12000 rpm 上限工况

**适用**：精度优先、转速可控、希望低成本升级的场景。

---

### 4.4 方案 C：FPGA

**优势**

- 多通道严格并行同步采集
- 采样率可达数 MS/s 至数百 MS/s
- 亚微秒级确定性时序
- 可在采集端做实时并行处理
- 可对接 LVDS/JESD204 高速 ADC、PCIe、千兆网

**代价**

- FPGA 一般无内置模拟 ADC，需外接 ADC 并重新设计模拟前端
- USB 需外挂 PHY/桥接芯片，或改用 SoC FPGA（Zynq 等）
- 需要 HDL 技能、时序收敛、ILA 调试
- PCB 通常 6–8 层，BGA 布线，电源完整性要求高
- 开发周期通常 1–3 个月起（不含模拟前端）
- 纯 FPGA 方案下，现有 `APP/SERVICE/PLATFORM` 的 C 代码基本无法复用

**适用条件（满足其一才值得评估）**

- 采样率需要持续超过 1–3 MS/s
- 需要 4 路以上严格同步采集
- 需要采集端实时处理，不能等采集结束
- 需要与高速 ADC、相机、激光器硬同步
- 数据率必须走 PCIe/千兆网
- 已有 FPGA 团队与平台

> 当前项目是单通道、800 kS/s、上位机离线成像，上述条件均不成立。

---

## 5. 横向对比

| 维度 | A：F4 + 8-bit + FS | Q：H7 + 12-bit + FS | B：H7 + 12/16-bit + HS | C：FPGA |
|---|---|---|---|---|
| 满足 800 kS/s | 接近 ADC 上限 | 可以 | 轻松 | 轻松 |
| 满足 12/16-bit | 否 | 是（典型转速） | 是 | 是（外部 ADC） |
| USB 带宽 | 800 kB/s 接近极限 | 0.4–0.6 MB/s 可覆盖 | 1.6 MB/s 毫无压力 | 取决于接口 |
| 开发周期 | 0 | 2–4 周 | 3–6 周（含硬件） | 1–3 个月起 |
| 代码复用 | 100% | 高（换 IMPLEMENT/VENDOR） | 高 | 低（纯 FPGA） |
| 硬件改动 | 无 | PCB 重做 | PCB 重做 + PHY | 全新硬件 |
| 成本 | 最低 | 低 | 中 | 高 |
| 风险 | 低 | 低–中 | 中（PHY、cache、协议栈） | 高 |
| 多通道扩展 | 难 | 受 ADC 通道限制 | 受 ADC 通道限制 | 强 |
| 实时处理 | 主循环够用 | 够用 | 够用 | 强 |
| 当前需求匹配度 | 典型工况够用 | 精度优先的折中 | 满速 + 精度 | 能力过剩 |

> 表中评分为定性判断，具体以实际工况、产能和团队能力为准。

---

## 6. 推荐实施路线

### 阶段 0：F407 + 12-bit 验证（不改硬件）

- 将 ADC 配置从 `ADC_Resolution_8b` 改为 12-bit
- 数据按 2 字节/点打包，上位机改用二进制解析
- 目标：在典型转速下对比 8-bit/12-bit 图像质量
- 验证标准：`dr = 0`、图像噪声和细节有可见改善
- 回退：改回 8-bit，上位机恢复原解析

### 阶段 1：H7 + 12-bit + 内置 USB FS

- 前提：12-bit 收益确认，且最高转速 ≤ 约 3000–4000 rpm
- 复用 `APP/SERVICE/PLATFORM`，重写 `IMPLEMENT/VENDOR`
- 验证标准：0.4–0.6 MB/s 连续采集无丢样
- 回退：保留 F407 版本作为备份

### 阶段 2：H7 + ULPI PHY + USB HS

- 前提：必须在满速 800 kS/s 下使用 12/16-bit
- 同步完成：ULPI PHY、USB HS 布线、D-Cache 配置、上位机二进制协议
- 验证标准：1.6 MB/s 连续采集 5 分钟以上，`dr = 0`，无 cache 错乱
- 回退：可退回方案 Q 的速率

### 阶段 3：评估 FPGA

- 仅在出现多通道、持续 >1–3 MS/s、实时处理或硬同步需求时启动
- 优先考虑 SoC FPGA，保留部分 C 代码

---

## 7. 附录

### 7.1 关键代码位置

| 内容 | 位置 |
|---|---|
| ADC 位宽 | `VENDOR/vendor_stm32_encoder_adc.c:481` |
| ADC 时钟/预分频 | `vendor_stm32_encoder_adc.c:476` |
| TIM2 触发配置 | `vendor_stm32_encoder_adc.c:311` |
| DMA 缓冲大小 | `vendor_stm32_encoder_adc.c:36` |
| 服务队列/分块 | `SERVICE/adc_stream_service.c:36` |
| USB 端点包/发送环 | `USB/USB_USER/inc/usbd_conf.h:55`、`:59` |
| CDC 单次传输 | `USB/USB_CDC/src/usbd_cdc_core.c:91` |
| USB 写入与 BUSY | `VENDOR/vendor_usb_cdc.c:83` |
| 背压暂停/恢复 | `SERVICE/adc_stream_service.c:202`、`:220` |

### 7.2 参考资料

- ST AN5354《Getting started with 16-bit ADC on STM32H7 MCUs》
  https://www.st.com/resource/en/application_note/an5354-getting-started-with-16bit-adc-on-stm32h7-mcus-stmicroelectronics.pdf
- STM32H743 数据手册
  https://www.st.com/resource/en/datasheet/stm32h743xg.pdf
- STM32H723 数据手册
  https://www.st.com/resource/en/datasheet/stm32h723ze.pdf

### 7.3 术语

| 术语 | 含义 |
|---|---|
| S/s、kS/s、MSPS | 每秒采样点数 |
| ENOB | 有效位数，反映实际精度 |
| CDC | USB 通信设备类，虚拟串口 |
| FS / HS | 全速 12 Mbps / 高速 480 Mbps |
| ULPI | USB 高速 PHY 接口 |
| D-Cache | Cortex-M7 数据缓存 |
| 背压 | 下游忙时暂停上游，防止丢数据 |

---

## 8. 一句话总结

**当前 F4 + 8-bit + USB FS 在典型工况够用、在满速工况接近极限；H7 + 12/16-bit + USB HS 性能绰绰有余，是合理的升级方向；FPGA 能力过剩，只有未来出现多通道、超高速或实时处理需求时才值得评估。**
