# LLM-PID-Tuner

一个用大语言模型辅助 PID 调参的实用工具。

它的目标很直接：**把反复试错、靠感觉拧 PID 参数的痛苦过程，尽量变成一个可观察、可回退、可复现的自动流程。**

[中文](README.md) | [English](README_EN.md)

> 如果你是第一次接触这个项目，**不要先折腾 Python**。
> **最省事的用法是直接下载 Release 里的 `llm-pid-tuner.exe`。**

## 先看你该怎么用

- **只想调硬件，不想配开发环境**：走 `exe` 路线，见下方“3 分钟上手”。
- **想先看看这个项目到底有没有用**：运行 `simulator.py` 做本地热系统仿真。
- **想接自己的 Arduino / ESP32 / 其他控制板**：使用 `firmware.cpp` + `tuner.py` / `llm-pid-tuner.exe`。
- **想二次开发或看内部设计**：看 `PROJECT_DOC.md`。

## 这个项目适合什么场景

- 恒温控制：加热板、热端、恒温箱、水浴、烘箱
- 电机 / 执行器：需要 PID 闭环调节的对象
- 已经能跑，但参数不好：响应慢、超调大、抖动、稳态误差明显
- 没有成熟整定经验，希望让 LLM 先帮你缩小试错范围

## 它不是做什么的

- 它**不是**“一键保证完美控制”的魔法程序
- 它**不是**替代硬件保护的安全系统
- 它**不是**为了漂亮 benchmark 分数而设计的玩具

这个项目更在意的是：**真实调参时少走弯路、少炸参数、调差了还能回退。**

---

## 3 分钟上手：Windows 打包版（推荐给小白）

### 第 1 步：下载打包版

打开 Release 页面：
`https://github.com/KINGSTON-115/llm-pid-tuner/releases/latest`

下载资产里的 `llm-pid-tuner.exe`。

### 第 2 步：准备硬件

你需要一个能通过串口持续上报控制数据的板子。

最简单的方式是直接参考仓库里的 `firmware.cpp`，它已经实现了这个项目默认使用的串口协议。

程序默认希望设备通过串口持续输出类似下面的 CSV 数据：

```text
timestamp_ms,setpoint,input,pwm,error,p,i,d
```

如果你不想自己适配协议，**最省事的办法就是直接从 `firmware.cpp` 开始。**

### 第 3 步：第一次运行 exe

双击运行 `llm-pid-tuner.exe`。

第一次运行时，如果当前目录下没有 `config.json`，程序会自动生成一份默认配置。

### 第 4 步：填写 `config.json`

打开 `config.json`，至少把这几个字段改掉：

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

如果你要用 **MiniMax / DeepSeek / Ollama / LM Studio** 这类 OpenAI 兼容接口，通常这样配就行：

```json
{
  "LLM_API_KEY": "你的 key",
  "LLM_API_BASE_URL": "你的 /v1 地址",
  "LLM_MODEL_NAME": "你的模型名",
  "LLM_PROVIDER": "openai"
}
```

比如 MiniMax 兼容接口可以是：

```json
{
  "LLM_API_BASE_URL": "http://你的地址/v1",
  "LLM_MODEL_NAME": "MiniMax-M2.5",
  "LLM_PROVIDER": "openai"
}
```

如果你使用 Claude 原生接口，再把 `LLM_PROVIDER` 改成 `anthropic`。

### 第 5 步：重新运行 exe

保存 `config.json` 后重新运行程序。

- 如果 `SERIAL_PORT` 填的是 `AUTO`，程序会扫描串口并让你选择
- 如果你已经知道端口号，比如 `COM5`，也可以直接写死，省掉选择步骤

### 第 6 步：观察调参结果

程序会持续：

- 读取串口数据
- 分析当前响应质量
- 请求 LLM 给出新的 PID 建议
- 必要时使用保底策略
- 如果后续结果变差，会回退到之前更稳的参数
- 当系统“已经够好”时，会尽量提前停止，避免过调

你最终需要做的事通常只有一件：**把收敛后的 PID 参数写回你的固件。**

---

## `config.json` 字段说明

下面是实际程序会读取的关键配置项。

| 字段 | 作用 | 新手建议 |
| :--- | :--- | :--- |
| `SERIAL_PORT` | 串口号，支持 `AUTO` 或具体端口 | 不确定就先用 `AUTO` |
| `BAUD_RATE` | 串口波特率 | 与你的单片机保持一致，默认 `115200` |
| `LLM_API_KEY` | 模型服务密钥 | 必填 |
| `LLM_API_BASE_URL` | 模型接口地址 | OpenAI 兼容接口一般都以 `/v1` 结尾 |
| `LLM_MODEL_NAME` | 具体模型名 | 例如 `gpt-4`、`MiniMax-M2.5` |
| `LLM_PROVIDER` | 提供商类型 | OpenAI 兼容接口填 `openai` |
| `BUFFER_SIZE` | 每轮分析采样点数 | 一般不要乱改，先用默认 |
| `MIN_ERROR_THRESHOLD` | 判定足够接近目标的阈值 | 先用默认 |
| `MAX_TUNING_ROUNDS` | 最大调参轮数 | 新手保持默认 |
| `LLM_REQUEST_TIMEOUT` | LLM 请求超时秒数 | 网络慢时可适当加大 |
| `LLM_DEBUG_OUTPUT` | 是否打印更详细的 LLM 输出 | 排查问题时再开 |

### 关于环境变量

程序也支持环境变量，并且**环境变量优先级高于 `config.json`**。

例如：

```powershell
$env:LLM_API_KEY="sk-xxx"
$env:LLM_API_BASE_URL="http://127.0.0.1:11434/v1"
$env:LLM_MODEL_NAME="qwen2.5:7b"
$env:LLM_PROVIDER="openai"
```

如果你是小白，**优先用 `config.json`，更直观。**

---

## 推荐模型与接口填写方式

| 方案 | `LLM_API_BASE_URL` 示例 | `LLM_PROVIDER` | 说明 |
| :--- | :--- | :--- | :--- |
| OpenAI | `https://api.openai.com/v1` | `openai` | 最省心 |
| DeepSeek 兼容接口 | 对应服务商的 `/v1` 地址 | `openai` | 常见且便宜 |
| MiniMax 兼容接口 | 对应服务商的 `/v1` 地址 | `openai` | 推理能力适合调参 |
| Ollama | `http://localhost:11434/v1` | `openai` | 本地免费部署 |
| LM Studio | `http://localhost:1234/v1` | `openai` | 本地可视化较友好 |
| Anthropic Claude | `https://api.anthropic.com` | `anthropic` | 原生接口用这个 |

这个项目现在做了更稳的解析和回退处理，**对 OpenAI 兼容接口更友好**。如果 SDK 路径不顺，它也会尽量走更直接的 HTTP 路径，减少“能调通 API 但程序不工作”的情况。

---

## 如果你还没有硬件，先跑仿真

如果你只是想确认这个项目到底在干什么，先跑这个：

```bash
pip install -r requirements.txt
python simulator.py
```

`simulator.py` 会在本地模拟一个热系统，然后让 LLM 自动调参。
这条路线最适合先理解项目，而不是直接上真实硬件。

---

## 进阶：源码方式运行

如果你不想用 exe，也可以直接跑源码。

### 安装依赖

```bash
git clone https://github.com/KINGSTON-115/llm-pid-tuner.git
cd llm-pid-tuner
pip install -r requirements.txt
```

### 运行仿真

```bash
python simulator.py
```

### 连接真实硬件

```bash
python tuner.py
```

### 做系统辨识（可选）

你可以先采一段阶跃响应数据，再用 `system_id.py` 给出一组初值建议：

```bash
python system_id.py --file sample_step.csv
```

这适合想先拿到一组“不会太离谱”的初始参数，再交给 LLM 继续细调的用户。

---

## 主要文件是干什么的

| 文件 | 用途 |
| :--- | :--- |
| `tuner.py` | 真实硬件调参主程序，也是 exe 的核心入口 |
| `simulator.py` | 本地热系统仿真，适合演示和验证策略 |
| `pid_safety.py` | 参数保护、保底策略、最佳结果记录、回退逻辑 |
| `firmware.cpp` | 单片机侧示例固件，负责串口上报与执行 PID |
| `system_id.py` | 利用阶跃响应做系统辨识，给出初始 PID 建议 |
| `benchmark.py` | 固定随机种子的对比工具，更偏开发验证用途 |
| `config.json` | 运行配置文件 |
| `PROJECT_DOC.md` | 面向开发者的内部说明文档 |

---

## 常见问题

### 1）双击 exe 后一闪而过

常见原因：

- `config.json` 还没填好
- API Key 无效
- 串口打不开
- 当前目录没有写入权限，导致配置文件生成失败

建议直接在 PowerShell 里运行，这样错误信息不会消失：

```powershell
.\llm-pid-tuner.exe
```

### 2）程序找不到串口

检查下面几件事：

- 设备是否真的连上电脑
- 驱动是否安装正确
- 其他上位机是否已经占用了串口
- `BAUD_RATE` 是否和单片机一致

### 3）程序连上了，但一直没有数据

大概率是你的固件输出格式和项目默认协议不一致。
最稳妥的办法是先按 `firmware.cpp` 的格式对齐。

### 4）LLM 能聊天，但程序调不动

多数时候是这几类问题：

- `LLM_API_BASE_URL` 写错
- 模型名写错
- 你用的是 OpenAI 兼容接口，但 `LLM_PROVIDER` 填成了 `anthropic`
- 服务端虽然可用，但返回 JSON 风格不稳定

### 5）调着调着结果变差怎么办

现在的程序会：

- 给 PID 做基本护栏限制
- LLM 异常时启用保底建议
- 记录历史最佳稳定参数
- 后续结果明显变差时自动回退

也就是说，它的设计目标不是“每一轮都更激进”，而是**优先把系统留在一个更稳、更可用的位置。**

### 6）能不能完全离线使用

可以，但前提是你本地起了兼容 OpenAI 接口的模型服务，比如：

- Ollama
- LM Studio

然后把 `LLM_API_BASE_URL` 指向本地地址即可。

---

## 安全提醒

**在真实硬件上调参时，请务必有人值守。**

尤其是加热类系统，一定要有：

- 硬件级限温
- 传感器异常保护
- 继电器 / MOS 管失控保护
- 必要时的物理断电手段

这个项目能帮你减少调参痛苦，但**不能替代硬件安全设计**。

---

## 补充说明

- 最新打包版请看 Release：`https://github.com/KINGSTON-115/llm-pid-tuner/releases/latest`
- 想看项目内部设计，请看 `PROJECT_DOC.md:1`
- 想看英文说明，请看 `README_EN.md:1`

## License

`MIT`
