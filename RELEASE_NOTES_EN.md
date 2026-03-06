# Release v2.1.0 PRO - Beginner-Friendly Hardware Tuning Build

Release date: 2026-03-06

This release is not about adding flashy features. It is about making the project **more reliable, easier to understand, and easier to use on real hardware**.

## What changed

### 1. New packaged `exe`

- The `llm-pid-tuner.exe` asset in this Release is updated to the latest build
- It targets the cleaned-up hardware tuning flow for Windows users

### 2. More stable tuning flow

- PID guardrails reduce the chance of runaway parameter jumps
- Fallback suggestions keep the loop moving when the LLM output is unusable
- The runtime remembers the best stable result seen so far
- It rolls back if a later suggestion clearly performs worse
- It stops early when the system is already “good enough”

### 3. Better LLM endpoint compatibility

- Improved support for OpenAI-compatible APIs
- Direct HTTP fallback path when SDK behavior is not enough
- More robust JSON parsing for inconsistent model outputs

### 4. Docs rewritten for beginners

- `README.md` now leads with the packaged `exe` workflow
- clearer `config.json` guidance
- clearer serial / API / troubleshooting notes
- updated `PROJECT_DOC.md` for maintainers and contributors

## Who should download this build

- Users who want to tune Arduino / ESP32 / other serial-connected hardware
- Windows users who do not want to install Python first
- People who want to start using the tool before reading the source code

## Recommended usage

1. Download `llm-pid-tuner.exe` from this Release
2. Prepare your board, ideally starting from `firmware.cpp`
3. Run the exe once to generate `config.json`
4. Fill in your API and serial settings
5. Run the exe again and start tuning

## Config example

```json
{
  "SERIAL_PORT": "AUTO",
  "BAUD_RATE": 115200,
  "LLM_API_KEY": "sk-your-key",
  "LLM_API_BASE_URL": "https://api.openai.com/v1",
  "LLM_MODEL_NAME": "gpt-4",
  "LLM_PROVIDER": "openai"
}
```

For MiniMax, DeepSeek, Ollama, or LM Studio style OpenAI-compatible endpoints, you usually only need to change:

- `LLM_API_BASE_URL`
- `LLM_MODEL_NAME`
- `LLM_API_KEY`

and keep:

```json
{
  "LLM_PROVIDER": "openai"
}
```

## Upgrade notes

- If you downloaded the older Release from four days ago, download the new `llm-pid-tuner.exe`
- If you already have an older `config.json`, compare it with the updated `README.md`
- If you run from source, also review `README_EN.md` and `PROJECT_DOC.md`

## Safety reminder

This project is built to help you reach usable PID parameters faster. It does **not** replace hardware safety protections.

For real hardware, still keep:

- over-temperature protection
- sensor fault handling
- power-stage fault protection
- human supervision

---

After downloading, start with `README.md:1` or `README_EN.md:1` depending on your language preference.
