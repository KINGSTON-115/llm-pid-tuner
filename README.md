# 基于 LLM 的 PID 自动调参系统

一个纯 CLI 的 PID 自动调参系统，支持本地仿真和真实硬件两种模式。

## 系统架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        本地仿真模式                               │
│  ┌─────────────┐    API (JSON)    ┌─────────────┐              │
│  │ simulator.py │ ───────────────► │    LLM      │              │
│  │ (纯Python)  │ ◄─────────────── │ (MiniMax)   │              │
│  └─────────────┘                   └─────────────┘              │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│                        真实硬件模式                              │
│  ┌─────────────┐    串口 (CSV)    ┌─────────────┐    API      │
│  │   MCU       │ ───────────────► │  tuner.py   │ ───────────►│
│  │ (firmware)  │                  │             │ ◄───────────┘│
│  └─────────────┘                  └─────────────┘              │
└──────────────────────────────────────────────────────────────────┘
```

## 快速开始 (本地仿真模式)

### 1. 克隆项目

```bash
git clone https://github.com/KINGSTON-115/llm-pid-tuner.git
cd llm-pid-tuner
```

### 2. 配置 LLM API

编辑 `simulator.py` 顶部的配置：

```python
# MiniMax API (推荐)
API_URL = "http://115.190.127.51:19882/v1/chat/completions"
API_KEY = "your-api-key"
MODEL_NAME = "MiniMax-M2.5"

# 或使用 OpenAI
API_URL = "https://api.openai.com/v1/chat/completions"
API_KEY = "sk-your-key"
MODEL_NAME = "gpt-4"
```

### 3. 运行

```bash
python simulator.py
```

### 4. 配置目标温度

编辑 `simulator.py`:

```python
SETPOINT = 80.0    # 目标温度 (默认 100°C)
```

### 5. 选择 PID 公式

编辑 `simulator.py` 选择 PID 公式类型:

```python
PID_FORMULA = "standard"  # 可选: standard, parallel, positional, velocity, incremental, custom
```

### 6. 自定义 PID 公式 (可选)

如果选择 `custom`，可以修改:

```python
CUSTOM_PID_FORMULA = "kp * error + ki * integral + kd * derivative"
```

可用变量: `error`, `integral`, `derivative`, `prev_error`, `prev_prev_error`, `kp`, `ki`, `kd`

### PID 公式类型说明

| 类型 | 说明 | 适用场景 |
|------|------|----------|
| `standard` | 标准位置式 PID | 多数温度控制 |
| `parallel` | 并行 PID | 与 standard 等价 |
| `positional` | 位置式 PID | 需要绝对输出的场景 |
| `velocity` | 速度式 PID | 连续变化系统 |
| `incremental` | 增量式 PID | 步进电机、阀门控制 |
| `custom` | 自定义 | 高级用户自定义公式 |

## 输出示例

```
============================================================
开始 PID 自动调参实验 (MiniMax M2.5)
============================================================

开始采集数据 (目标温度: 100.0°C, 初始温度: 0.0°C)
============================================================
[数据] t=50ms T=2.5°C PWM=200.5 Error=+97.5
[数据] t=550ms T=27.1°C PWM=77.1 Error=+72.9
[数据] t=1050ms T=43.0°C PWM=65.0 Error=+57.0

[第 1 轮] 平均误差: 71.30°C, 最大误差: 97.54°C

[MiniMax] 调用 API 中...
[MiniMax] 分析: 温度上升太慢，稳态误差极大...
[MiniMax] 新参数: P=2.0, I=0.5, D=0.05
```

## 核心参数配置

编辑 `simulator.py`:

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `SETPOINT` | 100.0 | 目标温度 (°C) |
| `BUFFER_SIZE` | 25 | 每轮采集数据点数 |
| `CONTROL_INTERVAL` | 0.05 | 控制周期 (秒) |
| `MIN_ERROR_THRESHOLD` | 0.3 | 收敛阈值 (°C) |

## 物理模型

系统使用二阶热力学模型：

```
heater_temp: 加热器温度 (受 PWM 控制)
     ↓ 热传导
object_temp: 被控对象温度 (我们要控制的)
     ↓ 散热
 ambient: 环境温度
```

加热过程：
```python
target_heater_temp = ambient + (pwm / 255.0) * heater_coeff
heater_temp += (target_heater_temp - heater_temp) * 0.3  # 热惯性
object_temp += (heater_temp - object_temp) * 0.1  # 热传导
object_temp -= (object_temp - ambient) * 0.01  # 散热损失
```

## 真实硬件模式

### 1. 上传固件

将 `firmware.cpp` 编译上传到 Arduino/ESP32。

### 2. 配置串口

编辑 `tuner.py` 顶部的配置：

```python
# ============================================================================
# 配置 (根据你的环境修改)
# ============================================================================

# 串口配置
SERIAL_PORT = "COM3"        # Windows: COM3, Linux: /dev/ttyUSB0, macOS: /dev/cu.usbserial-xxx
SERIAL_BAUD = 115200        # 波特率

# API 配置
API_URL = "http://115.190.127.51:19882/v1/chat/completions"  # 或使用其他 API
API_KEY = "your-api-key"
MODEL_NAME = "MiniMax-M2.5"
```

### 3. 各平台串口名称

| 平台 | 示例串口 | 如何查找 |
|------|----------|----------|
| **Windows** | `COM3`, `COM4` | 设备管理器 → 端口 (COM 和 LPT) |
| **Linux** | `/dev/ttyUSB0`, `/dev/ttyACM0` | `ls /dev/tty*` |
| **macOS** | `/dev/cu.usbserial-*`, `/dev/cu.SLAB_USBtoUART` | `ls /dev/cu.*` |

### 4. 运行

```bash
# Windows
python tuner.py

# Linux / macOS
python3 tuner.py
```

### 5. 可配置参数

编辑 `tuner.py` 或 `firmware.cpp` 可调整：

| 参数 | 文件 | 说明 |
|------|------|------|
| `SERIAL_PORT` | tuner.py | 串口名称 |
| `SERIAL_BAUD` | tuner.py | 波特率 (默认 115200) |
| `CONTROL_INTERVAL` | firmware.cpp | 控制周期 (ms) |
| `PWM_PIN` | firmware.cpp | PWM 输出引脚 |
| `TEMP_SENSOR` | firmware.cpp | 温度传感器类型 |

## LLM 调参逻辑

LLM 根据实时数据调整 PID 参数：

| 现象 | 原因 | 调整建议 |
|------|------|----------|
| 震荡剧烈 | Kp 过大或 Kd 过小 | 减小 P 或增大 D |
| 响应太慢 | Kp 过小 | 增大 P |
| 稳态误差 | Ki 过小 | 增大 I |
| 超调过大 | Kp 过大 | 减小 P 或增大 D |

## LLM 返回格式

```json
{
  "analysis": "温度上升太慢，稳态误差极大",
  "p": 2.0,
  "i": 0.5,
  "d": 0.05,
  "status": "TUNING"
}
```

## 依赖

- Python 3.8+
- requests

安装:
```bash
pip install requests
```

## 文件说明

| 文件 | 描述 |
|------|------|
| `simulator.py` | 本地仿真模式 (无需硬件) |
| `tuner.py` | 真实硬件模式 (需要 MCU) |
| `firmware.cpp` | MCU 端固件 |
| `PROJECT_DOC.md` | 开发文档 |

## 实验结果

| 目标温度 | 最佳参数 | 平均误差 | 收敛轮次 |
|----------|----------|----------|----------|
| 100°C | P=2.3, I=0.8, D=0.1 | 0.45°C | 4 轮 |
| 80°C | P=3.0, I=0.5, D=0.05 | 0.71°C | 5 轮 |

## 注意事项

1. **连续运行模式**: 系统不再每轮重置仿真，让温度持续运行以达到稳态
2. **收敛判断**: 误差 < 0.3°C 时可认为已收敛
3. **API 配置**: 确保 API 可访问，默认使用 MiniMax 国内节点
