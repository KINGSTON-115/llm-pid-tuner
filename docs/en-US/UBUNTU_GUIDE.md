# Ubuntu Experimental Package Guide

Supported targets:

- Ubuntu `22.04+`
- `amd64 / x86_64`
- Release assets packaged as `.deb` or `.tar.gz`

> This is the current **experimental Ubuntu package** for users who want to run the tool directly without setting up Python first.

## Which file should you download

- Want a normal installer: download `llm-pid-tuner_<version>_amd64.deb`
- Want a portable unpack-and-run bundle: download `llm-pid-tuner-ubuntu-<version>.tar.gz`

If you are unsure, start with the `.deb`.

## Option A: install the `.deb`

### 1. Download the `.deb`

Get this asset from the Release page:

```text
llm-pid-tuner_<version>_amd64.deb
```

### 2. Install it

```bash
sudo dpkg -i llm-pid-tuner_<version>_amd64.deb
```

If Ubuntu asks you to fix dependencies, run:

```bash
sudo apt-get install -f
```

### 3. Launch the app

```bash
llm-pid-tuner
```

You can also launch it from the desktop application menu.

### 4. Where the config file lives

On first launch, the `.deb` build creates:

```text
~/.local/share/llm-pid-tuner/config.json
```

The installed runtime itself lives under:

```text
/opt/llm-pid-tuner
```

## Option B: run the `.tar.gz` bundle

### 1. Extract it

```bash
tar -xzf llm-pid-tuner-ubuntu-<version>.tar.gz
cd llm-pid-tuner-ubuntu-<version>
```

### 2. Start it

```bash
./run.sh
```

### 3. Where the config file lives

The `.tar.gz` bundle creates `config.json` in the extracted folder itself.

Use this variant if you want the app and config to stay together in one directory.

## Recommended first run

Before connecting real hardware, run one local simulation round to verify the API path:

`.deb` build:

```bash
llm-pid-tuner sim --plain
```

`.tar.gz` build:

```bash
./run.sh sim --plain
```

If the program starts sampling, analyzing, and printing PID suggestions, your endpoint is working.

## Edit `config.json`

At minimum, set:

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

For MiniMax, DeepSeek, Ollama, LM Studio, or similar OpenAI-compatible endpoints:

```json
{
  "LLM_API_KEY": "your-key",
  "LLM_API_BASE_URL": "your-/v1-endpoint",
  "LLM_MODEL_NAME": "MiniMax-M2.7",
  "LLM_PROVIDER": "openai"
}
```

For real serial hardware, keep:

```json
{
  "SERIAL_PORT": "AUTO",
  "BAUD_RATE": 115200
}
```

If you already know the device path, you can set:

```json
{
  "SERIAL_PORT": "/dev/ttyUSB0"
}
```

or:

```json
{
  "SERIAL_PORT": "/dev/ttyACM0"
}
```

## Launch real hardware tuning

`.deb` build:

```bash
llm-pid-tuner tune
```

`.tar.gz` build:

```bash
./run.sh tune
```

You can also pass the serial device directly:

```bash
llm-pid-tuner /dev/ttyUSB0
```

or:

```bash
./run.sh /dev/ttyUSB0
```

## FAQ

### 1. The command is not found after installing the `.deb`

Open a new terminal and try:

```bash
llm-pid-tuner --help
```

If needed, run the installed launcher directly:

```bash
/usr/bin/llm-pid-tuner --help
```

### 2. I want the config file next to the program

Use the `.tar.gz` bundle, not the `.deb`.

### 3. Does this support ARM

Not yet. The current experimental package is `amd64 / x86_64` only.

### 4. Can it run fully offline

Yes, if you already run a local OpenAI-compatible model service such as Ollama.

## Safety reminder

When tuning real hardware, keep human supervision and hardware protections in place:

- over-temperature cutoffs
- sensor fault handling
- power-stage fault protection
- physical power disconnect if needed
