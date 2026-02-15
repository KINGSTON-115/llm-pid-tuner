# 基于 LLM 的 PID 自动调参系统 - 开发文档

## 一、项目概述

### 1.1 目标
构建一个基于 LLM 的闭环 PID 自动调参系统，不需要 GUI 界面，通过纯文本 CLI 实现：
- MCU 产生数据 → Python 转发 → LLM 分析 → 参数下发 → MCU 实时调整

### 1.2 系统架构
```
┌─────────────┐    串口 (CSV)    ┌─────────────┐    API (JSON)    ┌─────────────┐
│   MCU       │ ───────────────► │   Python    │ ───────────────► │    LLM      │
│ (firmware)  │                  │  (tuner.py) │                  │ (MiniMax)   │
│             │ ◄─────────────── │             │ ◄─────────────── │             │
└─────────────┘    串口 (CMD)    └─────────────┘    JSON 返回     └─────────────┘
```

---

## 二、初始版本 (第一版)

### 2.1 核心文件

| 文件 | 说明 |
|------|------|
| `firmware.cpp` | MCU 端 Arduino 固件 |
| `tuner.py` | Python 上位机桥接脚本 |

### 2.2 firmware.cpp 功能
- 内置简单仿真模型：`output += (pwm - output) * 0.1`
- 50ms 周期通过串口打印 CSV 数据
- 监听串口接收 PID 参数指令

### 2.3 tuner.py 功能
- 串口读取数据
- 数据缓冲池：100 行触发一次 LLM 分析
- 调用外部 LLM API 分析并返回新参数

---

## 三、遇到的问题

### 3.1 问题 1：仿真模型太简陋

**现象**：PWM 持续饱和 255，温度上升缓慢，LLM 持续建议增大 Kp 但无效

**原因**：原始仿真模型过于简单
```python
# 原始模型
new_temp = current_temp + (pwm/255 * heating_factor - (temp - ambient) * cooling_factor)
# 参数：heating_factor=2.0, cooling_factor=0.02
```

每 50ms 最多升温 2°C，从 0°C 到 100°C 需要很长时间，且很快达到稳态。

### 3.2 问题 2：数据量太大

**现象**：每轮需要 100 行数据 (~5秒) 才触发一次 LLM 分析

**原因**：BUFFER_SIZE = 100

**影响**：调参效率低

### 3.3 问题 3：LLM 返回格式不稳定

**现象**：有时返回空内容，有时带 markdown 代码块

**原因**：MiniMax 模型返回格式不统一

---

## 四、改进方案

### 4.1 仿真模型改进 (simulator.py)

**新模型**：二阶加热系统
```python
class HeatingSimulator:
    def __init__(self):
        # 加热器温度 (受 PWM 控制)
        self.heater_temp = 20.0
        # 环境温度
        self.ambient_temp = 20.0
        # 加热器功率系数
        self.heater_coeff = 300.0
        # 传热系数
        self.heat_transfer = 0.5
        # 散热系数
        self.cooling_coeff = 0.3
        # 传感器噪声
        self.noise_level = 0.3
    
    def update(self):
        # 1. 加热器温度由 PWM 决定
        target_heater_temp = self.ambient_temp + (self.pwm / 255.0) * self.heater_coeff
        self.heater_temp += (target_heater_temp - self.heater_temp) * 0.3
        
        # 2. 物体温度变化
        heat_from_heater = (self.heater_temp - self.temp) * self.heat_transfer
        heat_to_ambient = (self.temp - self.ambient_temp) * self.cooling_coeff
        net_heat = heat_from_heater - heat_to_ambient
        self.temp += net_heat * CONTROL_INTERVAL
        
        # 3. 添加噪声
        self.temp += random.gauss(0, self.noise_level)
```

**效果**：
- 加热到 ~62°C 达到稳态（有明显稳态误差）
- LLM 能通过调整 Ki 消除稳态误差
- 展现真实的 PID 控制特性

### 4.2 数据量优化

| 参数 | 原值 | 新值 | 效果 |
|------|------|------|------|
| BUFFER_SIZE | 100 | 20 | 每轮 1 秒 |
| LLM 分析数据 | 30 行 | 15 行 | 减少 token 消耗 |

### 4.3 LLM 返回解析优化

增加多种解析方式：
```python
# 方法1: 提取 JSON 块
json_match = re.search(r'\{[^{}]*\}', result_text, re.DOTALL)

# 方法2: 直接解析
json.loads(result_text)

# 方法3: 提取 key-value 对
p_match = re.search(r'"p"\s*:\s*([0-9.]+)', result_text)
```

---

## 五、测试结果

### 5.1 优化后测试

| 轮次 | PID 参数 | 平均误差 | LLM 判断 |
|------|----------|----------|----------|
| 1 | P:1.0→3.0, I:0.1→0.5 | 75°C | 响应太慢 ↑ |
| 2 | → 4.0, 1.0 | 52°C | P/I 偏小 ↑ |
| 3 | → 8.0, 0.5, 0.2 | 47°C | 接近目标 ↑ |
| 4 | → 9.0, 0.8 | 44°C | 有稳态误差 |
| 5 | → 12.0, 1.2, 0.3 | 44°C | 继续调整 |
| 6 | → 15.0, 1.8 | 43°C | 快到位 |
| 7 | → 14.0, 1.8, 0.5 | ~1°C | **DONE** |

### 5.2 最终指标

- **总耗时**: 159.6 秒 (~2.5 分钟)
- **调参轮数**: 8 轮
- **最终 PID**: P=14.0, I=1.8, D=0.5
- **稳态误差**: ~1.5°C
- **效率提升**: 5 倍 (100行→20行)

---

## 六、文件结构

```
llm-pid-tuner/
├── firmware.cpp      # MCU 端固件 (Arduino)
├── tuner.py         # 上位机桥接 (支持 LLM/OpenClaw 双模式)
├── simulator.py     # 本地模拟器 (测试用)
└── README.md       # 项目说明
```

---

## 七、使用方法

### 7.1 本地模拟测试
```bash
cd llm-pid-tuner
python3 simulator.py
```

### 7.2 串口连接 MCU
```bash
python3 tuner.py
```

### 7.3 配置 LLM
编辑 `tuner.py` 顶部：
```python
API_KEY = "your-api-key"
MODEL_NAME = "gpt-4"  # 或 "MiniMax-M2.5"
TUNER_MODE = "llm"   # 或 "openclaw"
```

---

## 八、LLM Prompt 设计

```python
SYSTEM_PROMPT = """你是一个 PID 控制算法专家。

## 判断规则
- 震荡剧烈 → 减小 Kp 或增大 Kd
- 响应太慢 → 增大 Kp
- 稳态误差 → 增大 Ki
- 超调过大 → 减小 Kp 或增大 Kd

## 输出格式
{"analysis": "简短分析", "p": <float>, "i": <float>, "d": <float>, "status": "TUNING 或 DONE"}
"""
```

---

## 九、总结

1. **仿真模型**：从简单一阶模型改为二阶加热系统，更真实
2. **调参效率**：数据量减少 5 倍，从 5 秒/轮 → 1 秒/轮
3. **LLM 能力**：能正确识别响应速度、稳态误差、过冲等问题
4. **最终效果**：8 轮调参达到稳态误差 ~1.5°C
