# LLM-Based PID Auto-Tuner (LLM-PID-Tuner)

[![Star History Chart](https://api.star-history.com/svg?repos=KINGSTON-115/llm-pid-tuner&type=Date)](https://star-history.com/#KINGSTON-115/llm-pid-tuner)

> 📺 [Video Tutorial (Bilibili)](https://b23.tv/WVUuIFb)
> 📺 [YouTube Tutorial](https://youtu.be/Giruc9kN53Y)

[中文](README.md) | [English](README_EN.md)

This is a PID auto-tuning system powered by Large Language Models (LLM). By analyzing real-time data from control systems, it leverages the logical reasoning capabilities of AI to automatically optimize PID parameters (Kp, Ki, Kd). It supports both **local simulation** and **real hardware tuning**.

---

## 🚀 Latest Update (v2.0 PRO)

We have just released the enhanced kernel, significantly improving tuning efficiency and stability:

1.  **History-Aware**: The AI now "remembers" previous attempts to avoid repeating mistakes (e.g., "Increasing P caused oscillation last time, so I'll be more cautious this time").
2.  **Chain-of-Thought (CoT)**: Forces the AI to perform deep logical reasoning before providing parameters, resulting in more scientific decisions.
3.  **Advanced Metrics**: Introduces professional control metrics such as Overshoot, Steady-State Error, and Oscillation Detection.
4.  **Global Vision**: Sampling window increased from 30 to 100 points, allowing the AI to see more complete waveform trends.

**Performance Comparison**:
*   Old Version: Converged in ~20 rounds, prone to oscillation.
*   **New Version**: Converges in just **8 rounds**, with steady-state error **<0.3%**, 2-3x faster speed.

---

## 🏗️ System Architecture

Whether simulating on a PC or connecting to real hardware, the workflow is as follows:

```
┌──────────────────────────────────────────────────────────────────┐
│                        Local Simulation Mode                     │
│  ┌─────────────┐    API (JSON)    ┌─────────────┐              │
│  │ simulator.py │ ───────────────► │    LLM      │              │
│  │ (Thermal Model)│ ◄─────────────── │ (AI Tuner)  │              │
│  └─────────────┘                   └─────────────┘              │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│                        Real Hardware Mode                        │
│  ┌─────────────┐    Serial (CSV)  ┌─────────────┐    API      │
│  │   MCU       │ ───────────────► │  tuner.py   │ ───────────►│
│  │ (Arduino etc)│                  │ (Host App)  │ ◄───────────┘│
│  └─────────────┘                  └─────────────┘              │
└──────────────────────────────────────────────────────────────────┘
```

---

## 📂 Project Structure: Which file should I run?

| File Name | Target Audience | Core Function | Hardware Required? |
| :--- | :--- | :--- | :--- |
| **`simulator.py`** | **Beginners/Learners** | **Recommended Start**. Simulates a heater on PC to demonstrate AI auto-tuning. | **No** |
| **`tuner.py`** | **Developers/Makers** | Acts as a "Host App", connecting to your Arduino/ESP32 via serial for real tuning. | **Yes** |
| **`firmware.cpp`** | **Hardware Engineers** | Code to flash into the MCU, responsible for receiving commands and controlling motors/heaters. | **Yes** |
| **`system_id.py`** | **Advanced Users** | Utility tool to automatically calculate initial PID suggestions using step response. | Optional |

---

## 🛠️ Quick Start (Local Simulation)

Even without hardware, you can run a demo in 1 minute:

### 1. Clone and Setup
Ensure Python 3.8+ is installed.
```bash
git clone https://github.com/KINGSTON-115/llm-pid-tuner.git
cd llm-pid-tuner
pip install requests serial
```

### 2. Configure API Key
Supports **GPT-4, DeepSeek, Claude, MiniMax, Ollama**, etc.

**Recommended (Environment Variables):**
```powershell
# Example for OpenAI
$env:LLM_API_BASE_URL="https://api.openai.com/v1"
$env:LLM_MODEL_NAME="gpt-4"
$env:LLM_API_KEY="sk-..."
```
*Or modify the configuration section at the top of `simulator.py` or `tuner.py`.*

### 3. Run Simulation
```bash
python simulator.py
```

---

## 🏗️ Advanced: Real Hardware Tuning

If you want to tune your own hardware (e.g., 3D printer hotend, water bath):

### 🚀 Recommended: Use Executable (Windows)
We provide a pre-built `llm-pid-tuner.exe` in [Releases](https://github.com/KINGSTON-115/llm-pid-tuner/releases).
1.  **Download**: Get the exe from the Release page.
2.  **Run**: Double-click it. It will auto-generate a `config.json` on first run.
3.  **Config**: Edit `config.json` with your API Key.
4.  **Connect**: The program will auto-scan serial ports for you to select.

### Source Code Method
1.  Open `tuner.py`, configure your API Key.
2.  Run `python tuner.py`.
3.  Select the serial port as prompted.

### Firmware Preparation
Refer to `firmware.cpp` to flash the code onto your Arduino/ESP32.

---

## ❓ FAQ

**Q: `ModuleNotFoundError` when running simulator.py?**
A: Run `pip install requests pyserial`.

**Q: Why is AI tuning slow?**
A: The AI needs sufficient data (default 100 points) to make accurate judgments. We intentionally increased the observation window to improve accuracy. You can modify `BUFFER_SIZE`, but <50 is not recommended.

**Q: Local model performance is poor?**
A: PID tuning requires strong logical reasoning. We recommend local models at least 7B size (e.g., Qwen2.5-7B-Instruct), or cloud models (GPT-4o, Claude 3.5).

---

## ⚠️ Safety Warning
**Always monitor the system when tuning real hardware!** Although the AI is smart, sensor failures or program crashes could lead to continuous heating. Ensure hardware-level thermal runaway protection is in place.

---

## 📜 License
[MIT License](LICENSE)
