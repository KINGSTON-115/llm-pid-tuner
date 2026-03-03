# Release v2.0 PRO - Hardware Tuning Edition

🎉 We're thrilled to announce the standalone executable for LLM-PID-Tuner v2.0 PRO!

## 📦 What's Included in This Release?

This release includes the packaged `llm-pid-tuner.exe`, a host application designed specifically for **real hardware tuning**.
*   **No Python Required**: Double-click to run directly — no need to install Python or any dependencies.
*   **Interactive Serial Port Selection**: The program automatically scans and lists connected serial devices, making it easy to select your target device.
*   **Enhanced Kernel**: Built-in the latest History-Aware and Chain-of-Thought tuning logic.

## 🚀 Quick Start

1.  **Download**: Click on `llm-pid-tuner.exe` in the Assets below to download.
2.  **Prepare Hardware**: Connect your microcontroller (e.g., Arduino, ESP32) to your computer.
3.  **Run**: Double-click `llm-pid-tuner.exe`.
    *   On first run, the program will automatically generate a `config.json` file.
4.  **Configure**: Open `config.json` in a text editor and fill in your API Key and other settings:
    ```json
    {
        "SERIAL_PORT": "AUTO",
        "BAUD_RATE": 115200,
        "LLM_API_KEY": "sk-your-key",
        "LLM_API_BASE_URL": "https://api.openai.com/v1",
        "LLM_MODEL_NAME": "gpt-4"
    }
    ```
5.  **Restart**: After saving the file, rerun the program.

## 📝 Configuration Guide

*   **`SERIAL_PORT`**: Defaults to `"AUTO"` (auto-scan), or you can specify a port like `"COM3"`.
*   **`LLM_API_BASE_URL`**: Supports OpenAI, DeepSeek, MiniMax, and all OpenAI-compatible interfaces.
*   **Environment Variables**: Still supported (takes priority over config file), convenient for script invocation.

---
*Happy Tuning!* 🎛️
