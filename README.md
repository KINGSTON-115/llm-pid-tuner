# 基于 LLM 的 PID 自动调参系统 (LLM-PID-Tuner)

[![Star History Chart](https://api.star-history.com/svg?repos=KINGSTON-115/llm-pid-tuner&type=Date)](https://star-history.com/#KINGSTON-115/llm-pid-tuner)

> 📺 [B站教程视频](https://b23.tv/WVUuIFb) - 详细视频教程手把手教你使用

[中文](README.md) | [English](README_EN.md)

这是一个结合了大型语言模型 (LLM) 的 PID 自动调参系统。它通过分析控制系统的实时数据，利用 AI 的逻辑推理能力自动优化 PID 参数（Kp, Ki, Kd），支持**本地仿真测试**和**真实硬件调参**。

---

## 🚀 最新更新 (v2.0 PRO)

我们刚刚发布了增强版内核，显著提升了调参效率和稳定性：

1.  **历史感知 (History-Aware)**: AI 现在能“记住”之前的尝试，避免重复错误（例如：上次加 P 导致震荡，这次就会更谨慎）。
2.  **思维链 (Chain-of-Thought)**: 强制 AI 先进行深度逻辑推理，再给出参数，决策更科学。
3.  **高级指标**: 引入了超调量 (Overshoot)、稳态误差、震荡检测等专业控制指标。
4.  **全局视野**: 采样窗口从 30 增加到 100，AI 能看到更完整的波形趋势。

**效果对比**:
*   旧版: 20 轮左右收敛，容易震荡。
*   **新版**: 仅需 **8 轮** 即可收敛，稳态误差 **<0.3%**，速度提升 2-3 倍。

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
| **`simulator.py`** | **新手/学习者** | **推荐起点**。在电脑上模拟一个加热器，演示 AI 如何自动调参。 | **不需要** |
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
项目支持 **GPT-4, DeepSeek, Claude, MiniMax, Ollama** 等。

**推荐方式（环境变量）：**
在终端（PowerShell 或 CMD）执行：
```powershell
# 以 MiniMax 为例
$env:LLM_API_BASE_URL="http://115.190.127.51:19882/v1"
$env:LLM_MODEL_NAME="MiniMax-M2.5"
$env:LLM_API_KEY="你的API_KEY"
```
*或者直接修改 `simulator.py` 或 `tuner.py` 文件顶部的配置区域。*

### 3. 运行仿真
```bash
python simulator.py
```

### 📝 运行结果示例：
```text
[第 1 轮] 数据采集中... 完成 (100 步)
  当前状态: AvgErr=99.04, MaxErr=187.84, Overshoot=0.0%, Status=SLOW_RESPONSE
  [LLM] 正在思考...
  [思考] 第一轮分析：系统存在严重稳态误差（95.06）... 策略：大幅增加积分作用...
  [分析] 严重稳态误差（95%），积分作用不足导致无法达到设定值。需大幅增加I和P。
  [动作] INCREASE_I_AND_P: P 1.0000->3.0000, I 0.1000->0.8000, D 0.0500->0.0500

... (经过几轮迭代) ...

[第 8 轮] 数据采集中... 完成 (100 步)
  当前状态: AvgErr=0.26, MaxErr=0.71, Overshoot=0.3%, Status=STABLE
[SUCCESS] 调参成功！系统已稳定。
```

---

## 🔬 核心技术细节

### 1. 物理模型 (物理世界的简化)
仿真模式使用了二阶热力学模型，模拟了“加热器 -> 被控对象 -> 环境散热”的真实过程：
```python
target_heater_temp = ambient + (pwm / 255.0) * heater_coeff
heater_temp += (target_heater_temp - heater_temp) * 0.1 * CONTROL_INTERVAL  # 热惯性
object_temp += (heater_temp - object_temp) * 0.5         # 热传导
object_temp -= (object_temp - ambient) * 0.05            # 散热损失
```

### 2. AI 调参的逻辑是什么？
AI 会像经验丰富的工程师一样思考（Chain-of-Thought）：
1.  **观察**：看最近 100 个点的波形，计算超调量、稳态误差、震荡频率。
2.  **回忆**：查阅历史记录（“上次加了 P 导致震荡，这次不能加了”）。
3.  **诊断**：判断当前主要矛盾（是响应慢？还是有稳态误差？还是在震荡？）。
4.  **决策**：给出调整方向（如 `INCREASE_I`）和具体参数。

---

## 🤖 调参适配指南

| 适配对象 | API 地址示例 | 说明 |
| :--- | :--- | :--- |
| **Ollama** | `http://localhost:11434/v1` | **完全免费**！本地运行,自选模型 |
| **LM Studio** | `http://localhost:1234/v1` | 本地运行，界面友好 |
| **Claude 原生** | `https://api.anthropic.com/v1` | 需设置 `$env:LLM_PROVIDER="anthropic"` |
| **国产大模型** | `https://api.deepseek.com` | 极高性价比，推荐 DeepSeek-V3 |
| **MiniMax** | `https://api.minimax.chat/v1` | 逻辑推理能力强，适合调参 |

---

## 🏗️ 进阶：连接真实硬件调参

如果你想用它来调试自己的硬件（如 3D 打印机喷头、恒温水箱）：

### 🚀 推荐方式：使用可执行程序 (Windows)
我们在 [Releases](https://github.com/KINGSTON-115/llm-pid-tuner/releases) 中提供了打包好的 `llm-pid-tuner.exe`。
1.  **下载**：从 Release 页面下载 exe 文件。
2.  **配置**：设置环境变量 `$env:LLM_API_KEY="您的Key"`。
3.  **运行**：双击运行，程序会自动扫描串口供您选择。

### 源码运行方式
1.  打开 `tuner.py`，配置好 API Key。
2.  运行 `python tuner.py`。
3.  按提示选择串口即可。

### 固件准备
请参考 `firmware.cpp` 将代码烧录到您的 Arduino/ESP32。

---

## ❓ 常见问题 (FAQ)

**Q: 我运行 simulator.py 报错 `ModuleNotFoundError`？**
A: 请执行 `pip install requests pyserial`。

**Q: 为什么 AI 调参很慢？**
A: AI 需要收集足够的数据（默认 100 组）才能做出准确判断。我们有意加大了观察窗口以提高准确率。你可以修改 `BUFFER_SIZE`，但不建议低于 50。

**Q: 本地模型效果不好怎么办？**
A: PID 调参需要较强的逻辑推理能力。建议本地模型至少使用 7B 以上规模（如 Qwen2.5-7B-Instruct），或者使用云端模型（GPT-4o, Claude 3.5, DeepSeek-V3）。

---

## ⚠️ 安全警告
**在真实硬件上使用时，请务必在场监控！** 虽然 AI 很聪明，但传感器故障或程序死机可能导致持续加热。请确保硬件端有物理级别的断电保护。

---

## 📜 许可证
[MIT License](LICENSE)
