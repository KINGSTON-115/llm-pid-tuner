# 基于 LLM 的 PID 自动调参系统 (LLM-PID-Tuner)

[![Star History Chart](https://api.star-history.com/svg?repos=KINGSTON-115/llm-pid-tuner&type=Date)](https://star-history.com/#KINGSTON-115/llm-pid-tuner)

> 📺 [B站教程视频](https://b23.tv/WVUuIFb) - 详细视频教程手把手教你使用

这是一个结合了大型语言模型 (LLM) 的 PID 自动调参系统。它通过分析控制系统的实时数据，利用 AI 的逻辑推理能力自动优化 PID 参数（Kp, Ki, Kd），支持**本地仿真测试**和**真实硬件调参**。

---

## 🏗️ 系统架构

无论是在电脑上仿真还是连接真实硬件，系统的工作流如下：

```
┌──────────────────────────────────────────────────────────────────┐
│                        本地仿真模式 (Simulator)                  │
│  ┌─────────────┐    API (JSON)    ┌─────────────┐              │
│  │ simulator.py │ ───────────────► │    LLM      │              │
│  │ (热力学模型)  │ ◄─────────────── │ (AI 调参器)  │              │
│  └─────────────┘                   └─────────────┘              │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│                        真实硬件模式 (Hardware)                   │
│  ┌─────────────┐    串口 (CSV)    ┌─────────────┐    API      │
│  │   MCU       │ ───────────────► │  tuner.py   │ ───────────►│
│  │ (Arduino等)  │                  │ (上位机)     │ ◄───────────┘│
│  └─────────────┘                  └─────────────┘              │
└──────────────────────────────────────────────────────────────────┘
```

---

## 📂 项目结构：我该运行哪个文件？

如果你是第一次使用，请根据你的目的选择对应的脚本：

| 文件名 | 适用人群 | 核心作用 | 需要硬件吗？ |
| :--- | :--- | :--- | :--- |
| **`simulator.py`** | **新手/学习者** | 在电脑上模拟一个加热器，演示 AI 如何自动调参。 | **不需要** |
| **`tuner.py`** | **开发者/创客** | 作为一个“上位机”，通过串口连接你的 Arduino/ESP32 进行实机调参。 | **需要** |
| **`firmware.cpp`** | **硬件工程师** | 烧录到单片机（MCU）里的代码，负责接收指令并控制电机/加热器。 | **需要** |
| **`system_id.py`** | **进阶用户** | 辅助工具，自动计算系统的初始 PID 建议值。 | 可选 |

---

## 🛠️ 快速上手 (以本地仿真为例)

即使你没有硬件，也可以在 1 分钟内跑通演示：

### 1. 克隆项目并准备环境
确保你的电脑安装了 Python 3.8+。
```bash
# 克隆代码仓库
git clone https://github.com/KINGSTON-115/llm-pid-tuner.git

# 进入项目文件夹
cd llm-pid-tuner

# 安装必要的库
pip install requests serial
```

### 2. 获取并配置 API Key
项目支持 **GPT-4, DeepSeek, Claude, Ollama** 等。

**推荐方式（环境变量）：**
在终端（PowerShell 或 CMD）执行：
```powershell
# 以 OpenAI 为例
$env:LLM_API_BASE_URL="https://api.openai.com/v1"
$env:LLM_MODEL_NAME="gpt-4o"
$env:LLM_API_KEY="你的API_KEY"
```

### 3. 运行仿真
```bash
python simulator.py
```

### 📝 运行结果示例：
```text
[第 1 轮] 平均误差: 71.30°C, 最大误差: 97.54°C
[AI] 分析: 温度上升太慢，稳态误差极大...
[AI] 新参数: P=2.0, I=0.5, D=0.05
```

---

## 🔬 核心技术细节

### 1. 物理模型 (物理世界的简化)
仿真模式使用了二阶热力学模型，模拟了“加热器 -> 被控对象 -> 环境散热”的真实过程：
```python
target_heater_temp = ambient + (pwm / 255.0) * heater_coeff
heater_temp += (target_heater_temp - heater_temp) * 0.3  # 热惯性
object_temp += (heater_temp - object_temp) * 0.1         # 热传导
object_temp -= (object_temp - ambient) * 0.01            # 散热损失
```

### 2. 支持的 PID 公式类型
你可以在 `simulator.py` 中自由切换不同的控制公式：

| 类型 | 说明 | 适用场景 |
| :--- | :--- | :--- |
| `standard` | 标准位置式 PID | 多数温度控制 |
| `positional` | 位置式 PID | 需要绝对输出的场景 |
| `incremental` | 增量式 PID | 步进电机、阀门控制 |
| `custom` | 自定义公式 | 允许用户输入 `kp*e + ki*i` 等任意字符串公式 |

### 3. AI 调参的逻辑是什么？
AI 会像经验丰富的工程师一样思考：
- **震荡剧烈？** -> 降低 **P** (Kp)，增加 **D** (Kd) 增加阻尼。
- **升温太慢？** -> 增加 **P** (Kp) 提高动力。
- **总差一点点才到目标？** -> 增加 **I** (Ki) 消除静差。
- **重要原则**：**禁止超调**（防止烧毁）且**稳定第一**。

---

## 🤖 调参适配指南

| 适配对象 | API 地址示例 | 说明 |
| :--- | :--- | :--- |
| **Ollama** | `http://localhost:11434/v1` | **完全免费**！本地运行,自选模型 |
| **LM Studio** | `http://localhost:1234/v1` | 本地运行，界面友好 |
| **Claude 原生** | `https://api.anthropic.com/v1` | 需设置 `$env:LLM_PROVIDER="anthropic"` |
| **国产大模型** | `https://api.deepseek.com` | 极高性价比，推荐 DeepSeek-V3 |

---

## 🏗️ 进阶：连接真实硬件调参

如果你想用它来调试自己的硬件（如 3D 打印机喷头、恒温水箱）：

### 第一步：准备固件
1. 打开 `firmware.cpp`。
2. 根据你的引脚连接修改代码（如 `PWM_PIN`）。
3. 使用 Arduino IDE 或 PlatformIO 将代码烧录到你的开发板。

### 第二步：配置上位机
1. 打开 `tuner.py`。
2. 找到 `SERIAL_PORT`，填入你的串口号（如 `COM3` 或 `/dev/ttyUSB0`）。
3. 确保 `API_KEY` 已按上述方式配置。

---

## ❓ 常见问题 (FAQ)

**Q: 我运行 simulator.py 报错 `ModuleNotFoundError`？**
A: 请执行 `pip install requests`。如果是运行 `tuner.py` 报错，请执行 `pip install pyserial`。

**Q: 为什么 AI 调参很慢？**
A: AI 需要收集一段时间的数据（默认 25 组）才能做出准确判断。你可以修改 `BUFFER_SIZE` 来调整观察窗口。

**Q: 本地模型效果不好怎么办？**
A: PID 调参需要一定的逻辑推理能力。建议本地模型至少使用 7B 以上规模（如 Qwen2-7B），效果最好的是 GPT-4o 或 Claude 3.5。

---

## ⚠️ 安全警告
**在真实硬件上使用时，请务必在场监控！** 虽然 AI 很聪明，但传感器故障或程序死机可能导致持续加热。请确保硬件端有物理级别的断电保护。

---

## 📜 许可证
[MIT License](LICENSE)
