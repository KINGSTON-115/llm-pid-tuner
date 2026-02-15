# 基于 LLM 的极简 PID 自动调参系统 (CLI 版)

一个纯文本交互的闭环调参系统，不需要 GUI 或数据可视化。

## 系统架构

```
┌─────────────┐    串口 (CSV)    ┌─────────────┐    API (JSON)    ┌─────────────┐
│   MCU       │ ───────────────► │   Python    │ ───────────────► │    LLM      │
│ (firmware)  │                  │  (tuner.py) │                  │ (GPT-4等)   │
│             │ ◄─────────────── │             │ ◄─────────────── │             │
└─────────────┘    串口 (CMD)    └─────────────┘    JSON 返回     └─────────────┘
```

## 数据流

1. **MCU → Python**: 每 50ms 通过串口发送 CSV 格式数据
2. **Python → LLM**: 收集 100 行数据后，构造 Prompt 发送给 LLM
3. **LLM → Python**: 返回优化后的 PID 参数 (JSON 格式)
4. **Python → MCU**: 发送 `SET P:x I:y D:z` 指令更新参数

## 文件说明

| 文件 | 描述 |
|------|------|
| `firmware.cpp` | MCU 端固件 (Arduino/PlatformIO) |
| `tuner.py` | Python 上位机桥接脚本 |
| `README.md` | 项目说明 |

## 快速开始

### 1. 上传固件到 MCU

使用 Arduino IDE 或 PlatformIO 将 `firmware.cpp` 编译并上传到 Arduino/ESP32。

### 2. 配置 Python 环境

```bash
# 安装依赖
pip install pyserial openai

# 或者仅安装 pyserial (使用 requests 调用 API)
pip install pyserial requests
```

### 3. 配置参数

编辑 `tuner.py` 顶部的配置：

```python
SERIAL_PORT = "/dev/ttyUSB0"  # 串口名称
API_KEY = "your-api-key"       # LLM API Key
MODEL_NAME = "gpt-4"          # 使用的模型
```

### 4. 运行

```bash
python tuner.py
```

## 指令格式

### MCU 端指令 (Python → MCU)

| 指令 | 说明 |
|------|------|
| `SET P:1.5 I:0.2 D:0.05` | 设置 PID 参数 |
| `SETPOINT:150` | 设置目标值 |
| `RESET` | 重置系统 |
| `STATUS` | 查询当前状态 |

### LLM 返回格式 (Python ← LLM)

```json
{
  "analysis": "超调过大，减少 P",
  "p": 1.2,
  "i": 0.1,
  "d": 0.08,
  "status": "TUNING"
}
```

## 内置仿真模型

`firmware.cpp` 中包含一个虚拟加热系统模型：

```cpp
// 温度变化率 = 加热输入 - 散热损失
new_temp = current_temp + (pwm/255 * heating_factor - (temp - ambient) * cooling_factor)
```

无需连接实际硬件即可测试整个调参流程。

## 调参逻辑

LLM 会根据以下规则调整参数：

| 现象 | 原因 | 调整 |
|------|------|------|
| 震荡剧烈 | Kp 过大或 Kd 过小 | 减小 P 或增大 D |
| 响应太慢 | Kp 过小 | 增大 P |
| 稳态误差 | Ki 过小 | 增大 I |
| 超调过大 | Kp 过大 | 减小 P 或增大 D |

## 依赖

- **MCU**: Arduino IDE / PlatformIO
- **Python**: Python 3.8+
- **Python 包**: pyserial, openai (可选: requests)
- **LLM API**: OpenAI / Anthropic / 兼容 OpenAI API 的其他 provider
