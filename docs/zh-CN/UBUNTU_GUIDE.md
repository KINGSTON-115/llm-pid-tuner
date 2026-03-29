# Ubuntu 实验打包版教程

适用范围：

- Ubuntu `22.04+`
- `amd64 / x86_64`
- Release 资产中的 `.deb` 或 `.tar.gz` 打包版

> 这是当前提供给 Ubuntu 用户的**实验打包版**。
> 如果你只是想直接上手，又不想先配 Python，这条路线最省事。

## 你应该下载哪个文件

- 想像普通软件一样安装：下载 `llm-pid-tuner_<版本>_amd64.deb`
- 想免安装、放哪跑哪：下载 `llm-pid-tuner-ubuntu-<版本>.tar.gz`

如果你不确定，**优先选 `.deb`**。

## 方式 A：安装 `.deb`

### 1. 下载 `.deb`

从项目的 Release 页面下载：

```text
llm-pid-tuner_<版本>_amd64.deb
```

### 2. 安装

在下载目录执行：

```bash
sudo dpkg -i llm-pid-tuner_<版本>_amd64.deb
```

如果系统提示依赖还没补齐，再执行：

```bash
sudo apt-get install -f
```

### 3. 启动程序

安装完成后可以直接执行：

```bash
llm-pid-tuner
```

也可以从桌面应用菜单里启动它。

### 4. 第一次运行后配置文件在哪里

`.deb` 版本第一次运行时，会自动在下面的位置生成 `config.json`：

```text
~/.local/share/llm-pid-tuner/config.json
```

程序主体会安装到：

```text
/opt/llm-pid-tuner
```

## 方式 B：直接运行 `.tar.gz`

### 1. 下载并解压

```bash
tar -xzf llm-pid-tuner-ubuntu-<版本>.tar.gz
cd llm-pid-tuner-ubuntu-<版本>
```

### 2. 启动程序

```bash
./run.sh
```

### 3. 配置文件在哪里

`.tar.gz` 版本第一次运行时，会在**当前解压目录**自动生成：

```text
config.json
```

如果你希望配置和程序放在同一个目录，`tar.gz` 版本更直观。

## 第一次运行建议先做什么

建议先跑一轮本地仿真，确认 API 能通、配置也没写错：

`.deb` 版本：

```bash
llm-pid-tuner sim --plain
```

`.tar.gz` 版本：

```bash
./run.sh sim --plain
```

如果你看到程序开始采样、分析并输出新的 PID 建议，说明接口链路已经通了。

## 配置 `config.json`

至少把下面这些字段改掉：

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

如果你使用 MiniMax / DeepSeek / Ollama / LM Studio 这类 OpenAI 兼容接口，通常这样写：

```json
{
  "LLM_API_KEY": "你的 key",
  "LLM_API_BASE_URL": "你的 /v1 地址",
  "LLM_MODEL_NAME": "MiniMax-M2.7",
  "LLM_PROVIDER": "openai"
}
```

如果你要接自己的硬件，保持：

```json
{
  "SERIAL_PORT": "AUTO",
  "BAUD_RATE": 115200
}
```

如果你已经知道设备串口，也可以直接写成：

```json
{
  "SERIAL_PORT": "/dev/ttyUSB0"
}
```

或：

```json
{
  "SERIAL_PORT": "/dev/ttyACM0"
}
```

## 连接真实硬件时怎么启动

`.deb` 版本：

```bash
llm-pid-tuner tune
```

`.tar.gz` 版本：

```bash
./run.sh tune
```

如果你想直接指定串口，也可以：

```bash
llm-pid-tuner /dev/ttyUSB0
```

或：

```bash
./run.sh /dev/ttyUSB0
```

## 常见问题

### 1. 安装完 `.deb` 后提示找不到命令

先重新打开一个终端，再试：

```bash
llm-pid-tuner --help
```

如果还是不行，可以直接执行：

```bash
/usr/bin/llm-pid-tuner --help
```

### 2. 我希望配置文件跟程序放一起

用 `.tar.gz` 版本，不要用 `.deb`。

### 3. 这个 Ubuntu 包支持 ARM 吗

当前这套实验包只针对 `amd64 / x86_64`。

### 4. 可以完全离线吗

可以，但前提是你本地已经起了一个 OpenAI 兼容接口服务，比如 Ollama。

## 安全提醒

真实硬件调参时，请务必有人值守，并提前做好：

- 硬件级限温
- 传感器异常保护
- 功率级失控保护
- 必要时的物理断电手段
