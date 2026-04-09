# LLM-PID-Tuner

一个用大语言模型辅助 PID 调参的实用工具。

> 💬 QQ 群：1082281492 - 欢迎加入交流讨论
> 📺 [B站教程视频](https://b23.tv/WVUuIFb) - 详细视频教程手把手教你使用
> 📺 [YouTube 教程视频](https://youtu.be/Giruc9kN53Y)

中文 | [English](./docs/en-US/README.md)

[![Star History Chart](https://api.star-history.com/svg?repos=KINGSTON-115/llm-pid-tuner&type=Date)](https://star-history.com/#KINGSTON-115/llm-pid-tuner)

> 如果你是第一次接触这个项目，**不要先折腾 Python**。
> **最省事的用法是直接下载 Release 里的 `llm-pid-tuner.exe`。**

## 系统结构图

```text
┌──────────────────────── 本地仿真模式 (Simulator) ────────────────────────┐
│                                                                         │
│  simulator.py  ───────────── API(JSON) ─────────────>  LLM / AI Tuner   │
│  (热系统仿真)                                           (生成 PID 建议)   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘

┌──────────────────────── 真实硬件模式 (Hardware) ─────────────────────────┐
│                                                                         │
│  MCU / firmware.cpp ── Serial(CSV) ──> tuner.py ── API ──> LLM          │
│  (Arduino / ESP32)                    (上位机 / .exe)                    │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## 先看你该怎么用

- **只想调硬件，不想配开发环境**：走 `exe` 路线，见下方“3 分钟上手”。
- **想先看看这个项目到底有没有用**：运行 `simulator.py` 做本地热系统仿真。
- **想接自己的 Arduino / ESP32 / 其他控制板**：使用 `firmware.cpp` + `tuner.py` / `llm-pid-tuner.exe`。
- **想二次开发或看内部设计**：看 [PROJECT_DOC.md](docs/zh-CN/PROJECT_DOC.md)。

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

打开 [Release](https://github.com/KINGSTON-115/llm-pid-tuner/releases/latest) 页面，并下载资产里的 `llm-pid-tuner.exe`。

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

启动器会先让你选择模式：`[1]` 真实硬件调参，`[2]` 本地仿真 TUI（默认）。
如果你现在是准备接串口硬件，请先选 `[1]`。

第一次运行时，如果当前目录下没有 `config.json`，程序会自动生成一份默认配置。

### 第 4 步：填写 `config.json`

打开 `config.json`，至少先把“硬件模式最小必填项”改掉：

```json
{
  "SERIAL_PORT": "AUTO",
  "BAUD_RATE": 115200,
  "LLM_API_KEY": "sk-your-key",
  "LLM_API_BASE_URL": "https://api.openai.com/v1",
  "LLM_MODEL_NAME": "gpt-4o",
  "LLM_PROVIDER": "openai"
}
```

如果你只是想先跑 `simulator.py` 看效果，没有接真实串口，也可以先不填 `SERIAL_PORT`，只把 LLM 相关字段填好。

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

如果你用的是 **Claude 中转站 / OneAPI / New API 这类 OpenAI 兼容接口**，现在也可以更明确地写成：

```json
{
  "LLM_API_BASE_URL": "https://你的中转站/v1",
  "LLM_MODEL_NAME": "claude-3-7-sonnet",
  "LLM_PROVIDER": "openai_claude"
}
```

`openai_claude` 和 `openai` 都会走 OpenAI 兼容协议；前者只是专门给 Claude 中转站准备的显式选项。

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

## `config.json` 怎么填

第一次运行 `tuner.py`、`simulator.py` 或 `llm-pid-tuner.exe` 时，如果当前目录没有 `config.json`，程序会自动生成一份默认配置。

如果你喜欢先看模板，也可以直接参考仓库里的 `config.example.json`。

### 先改这些就能跑起来

**1. 真实硬件模式最小必填**

```json
{
  "SERIAL_PORT": "AUTO",
  "BAUD_RATE": 115200,
  "LLM_API_KEY": "sk-your-key",
  "LLM_API_BASE_URL": "https://api.openai.com/v1",
  "LLM_MODEL_NAME": "gpt-4o",
  "LLM_PROVIDER": "openai"
}
```

**2. 本地 Python 仿真最小必填**

```json
{
  "LLM_API_KEY": "sk-your-key",
  "LLM_API_BASE_URL": "https://api.openai.com/v1",
  "LLM_MODEL_NAME": "gpt-4o",
  "LLM_PROVIDER": "openai"
}
```

**3. Simulink 模式额外补这几项**

```json
{
  "MATLAB_MODEL_PATH": "C:/models/my_pid_model.slx",
  "MATLAB_PID_BLOCK_PATH": "my_pid_model/PID Controller",
  "MATLAB_ROOT": "D:/Program Files/MATLAB/R2025b",
  "MATLAB_OUTPUT_SIGNAL": "y_out",
  "MATLAB_SIM_STEP_TIME": 15.0,
  "MATLAB_SETPOINT": 200.0
}
```

上面这 6 项是 **最小必填**。

- 第一轮只想跑通时，先填这 6 项就够了
- `MATLAB_CONTROL_SIGNAL` 和 `MATLAB_SETPOINT_BLOCK` 属于**推荐增强项**，不是第一次配置的硬性必填
- 如果你已经知道准确块路径，优先填显式路径，不必一开始就把所有高级字段都填上

如果你的 Simulink 模型不是“一个标准 PID Controller 块 + 一个输出信号”这种最简单结构，现在还支持这些兼容字段：

- `MATLAB_OUTPUT_SIGNAL_CANDIDATES`：输出变量名不统一时给一组候选名
- `MATLAB_CONTROL_SIGNAL`：显式提供控制输出信号，例如 `u_out`
- `MATLAB_SETPOINT_BLOCK`：显式指定设定值块
- `MATLAB_PID_BLOCK_PATHS`：多个候选控制器块路径
- `MATLAB_PID_BLOCK_PATH_2`：第二组控制器块（主副环 / 双控制器）
- `MATLAB_P_BLOCK_PATH` `MATLAB_I_BLOCK_PATH` `MATLAB_D_BLOCK_PATH`：分离式 P/I/D 增益块
- `MATLAB_P_BLOCK_PATH_2` `MATLAB_I_BLOCK_PATH_2` `MATLAB_D_BLOCK_PATH_2`：第二组分离式增益块

命名规则可以这样理解：

- 不带后缀的字段就是第一组 / 主控制器，例如 `MATLAB_PID_BLOCK_PATH`
- 带 `_2` 的字段就是第二组控制器，例如 `MATLAB_PID_BLOCK_PATH_2`

这些路径怎么找：

- 在 Simulink 里选中你要填写的那个块
- 在 MATLAB Command Window 输入 `gcb`
- 返回字符串就是完整块路径，可以直接填到 `MATLAB_PID_BLOCK_PATH`、`MATLAB_PID_BLOCK_PATH_2`、`MATLAB_SETPOINT_BLOCK`、`MATLAB_P/I/D_BLOCK_PATH(_2)` 里

这些字段怎么理解最不容易乱：

- 已知准确块路径：优先填 `MATLAB_PID_BLOCK_PATH`、`MATLAB_PID_BLOCK_PATH_2`、`MATLAB_SETPOINT_BLOCK`、`MATLAB_P/I/D_BLOCK_PATH`
- 有多个怀疑对象但还没确定：再用 `MATLAB_PID_BLOCK_PATHS`
- 自动识别：只当兜底，不建议当主要配置方式

更详细的多控制器 / 双控制器 / 分离增益块配置方法，见 [MATLAB/Simulink 调参指南](docs/zh-CN/MATLAB_GUIDE.md)。

### 按场景看配置项

| 分类 | 什么时候需要 | 字段 | 说明 |
| :--- | :--- | :--- | :--- |
| 硬件串口 | 真实硬件调参 | `SERIAL_PORT` `BAUD_RATE` | `SERIAL_PORT` 不确定先填 `AUTO`，`BAUD_RATE` 要和固件一致 |
| LLM 基础 | 所有模式都需要 | `LLM_API_KEY` `LLM_API_BASE_URL` `LLM_MODEL_NAME` `LLM_PROVIDER` | 这是最核心的一组配置，不填就无法调参 |
| 调参行为 | 想微调策略时再改 | `BUFFER_SIZE` `MIN_ERROR_THRESHOLD` `MAX_TUNING_ROUNDS` `LLM_REQUEST_TIMEOUT` `LLM_DEBUG_OUTPUT` | 新手建议先保持默认，只有在采样不够、网络慢或需要排查日志时再动 |
| Simulink | 只在 MATLAB/Simulink 模式下需要 | `MATLAB_MODEL_PATH` `MATLAB_PID_BLOCK_PATH` `MATLAB_ROOT` `MATLAB_OUTPUT_SIGNAL` `MATLAB_SIM_STEP_TIME` `MATLAB_SETPOINT`，以及按需填写 `MATLAB_CONTROL_SIGNAL` `MATLAB_SETPOINT_BLOCK` `MATLAB_PID_BLOCK_PATHS` `MATLAB_PID_BLOCK_PATH_2` `MATLAB_P/I/D_BLOCK_PATH(_2)` | 最小 6 项先跑通，复杂模型再逐步补充兼容字段 |
| 代理 | 只有需要代理时才填 | `HTTP_PROXY` `HTTPS_PROXY` `ALL_PROXY` `NO_PROXY` | 留空就是不启用 |

### `MATLAB_ROOT` 什么时候要填

- 用打包版 `exe` 跑 Simulink 时，建议直接填 `MATLAB_ROOT`，例如 `D:/Program Files/MATLAB/R2025b`
- 源码方式运行时，如果你当前这个 Python 环境已经能正常 `import matlab.engine`，`MATLAB_ROOT` 可以留空
- 如果源码运行也报 `No module named matlab.engine`，或者 MATLAB Engine 路径找不到，就把 `MATLAB_ROOT` 填上，同时按 [MATLAB/Simulink 调参指南](docs/zh-CN/MATLAB_GUIDE.md) 安装 Engine

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


### 关于代理（可选）

如果你需要走 VPN/代理，可以在 `config.json` 里新增以下字段：

```json
{
  "HTTP_PROXY": "http://127.0.0.1:7890",
  "HTTPS_PROXY": "http://127.0.0.1:7890",
  "ALL_PROXY": "http://127.0.0.1:7890",
  "NO_PROXY": ""
}
```

留空则**不启用代理**，对不需要代理的用户没有影响。
---

## 推荐模型与接口填写方式

| 方案                             | `LLM_API_BASE_URL` 示例     | `LLM_PROVIDER`  | 说明                                        |
| :------------------------------- | :-------------------------- | :-------------- | :------------------------------------------ |
| OpenAI                           | `https://api.openai.com/v1` | `openai`        | 最省心                                      |
| DeepSeek 兼容接口                | 对应服务商的 `/v1` 地址     | `openai`        | 常见且便宜                                  |
| MiniMax 兼容接口                 | 对应服务商的 `/v1` 地址     | `openai`        | 推理能力适合调参                            |
| Claude 中转站 / OneAPI / New API | 对应服务商的 `/v1` 地址     | `openai_claude` | Claude 模型走 OpenAI 兼容协议时用这个最直观 |
| Ollama                           | `http://localhost:11434/v1` | `openai`        | 本地免费部署                                |
| LM Studio                        | `http://localhost:1234/v1`  | `openai`        | 本地可视化较友好                            |
| Anthropic Claude                 | `https://api.anthropic.com` | `anthropic`     | 原生接口用这个                              |

这个项目现在做了更稳的解析和回退处理，**对 OpenAI 兼容接口更友好**。如果 SDK 路径不顺，它也会尽量走更直接的 HTTP 路径，减少“能调通 API 但程序不工作”的情况。

---

## MATLAB/Simulink 仿真模式

如果你已经有 MATLAB/Simulink 仿真模型，可以直接让 LLM 对你的模型进行 PID 调参，无需真实硬件。

在 `config.json` 里至少填写下面这些字段，再运行 `python simulator.py` 或打包版启动器里的 Simulink 模式：

- `MATLAB_MODEL_PATH`：Simulink `.slx` 文件路径
- `MATLAB_PID_BLOCK_PATH`：模型里的 PID 模块完整路径
- `MATLAB_OUTPUT_SIGNAL`：To Workspace 输出变量名
- `MATLAB_ROOT`：MATLAB 安装根目录；打包版建议填写，源码运行如果当前 Python 已装好 MATLAB Engine 可以留空

详细配置步骤、模型准备方法和常见问题，见 [MATLAB/Simulink 调参指南](docs/zh-CN/MATLAB_GUIDE.md)。

---


## 如果你还没有硬件，先跑仿真

如果你只是想确认这个项目到底在干什么，先跑这个：

```bash
pip install -r requirements.txt
python simulator.py
```

`simulator.py` 会在本地模拟一个热系统，然后让 LLM 自动调参。
这条路线最适合先理解项目，而不是直接上真实硬件。

在调参循环开始前，程序现在会自动进行两个对新手非常友好的操作：
- 运行环境诊断（`doctor.py`），检查配置、API 连接性、串口及代理设置。
- 进行简短的系统辨识（热启动），给出比默认值更合理的初始 PID 建议。

此外，交互模式下支持**预调参对话**（Pre-Tuning Dialog）。你可以用自然语言直接输入调参偏好或限制（例如：“超调不能超过 5%” 或 “响应可以慢点但绝不能震荡”），LLM 会自动提取为调参的硬性约束。

如果你只想手动运行环境检查而不启动仿真，可以使用：

```bash
python doctor.py
```

---

## 进阶：源码方式运行

如果你不想用 exe，也可以直接跑源码。

### 分支说明（重要）

- `dev` 分支：当前最新开发代码，功能修复、TUI 改动、打包前验证都会先进入这里
- `main` 分支：更偏稳定展示和正式 release，同步会慢一些，不保证是当前最新行为
- 如果你想源码运行、跟进最近修复，或者准备提 PR，建议直接拉 `dev`

### 新拉仓库：直接拉 `dev`

```bash
git clone -b dev https://github.com/KINGSTON-115/llm-pid-tuner.git
cd llm-pid-tuner
```

### 你已经拉过仓库：切到 `dev` 并更新

```bash
git fetch origin
git checkout dev
git pull --ff-only origin dev
```

### 安装依赖

```bash
pip install -r requirements.txt
```

如果你要跑 Simulink 源码模式，还需要让当前这个 Python 环境能导入 `matlab.engine`。不会配的话，直接看 [MATLAB/Simulink 调参指南](docs/zh-CN/MATLAB_GUIDE.md)。

### 运行前先确认配置

- 跑 `python tuner.py`：至少填好 LLM 配置和串口配置
- 跑 `python simulator.py`：至少填好 LLM 配置
- 跑 Simulink：再额外补 `MATLAB_*` 相关字段

第一次运行时如果没有 `config.json`，程序会自动生成一份默认配置；你也可以直接从 `config.example.json` 开始改。

### 运行仿真

```bash
python simulator.py
```

如果你想用旧式纯日志输出，而不是 TUI，或者需要强制切换显示语言：

```bash
python simulator.py --plain
python simulator.py --lang en  # 强制使用英文界面，默认支持根据系统自动检测语言
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

| 文件                        | 用途                                       |
| :-------------------------- | :----------------------------------------- |
| `launcher.py`               | 启动器，可选择硬件或仿真模式并支持语言切换 |
| `tuner.py`                  | 真实硬件调参主程序，也是 exe 的核心入口    |
| `simulator.py`              | 本地热系统仿真，适合演示和验证策略         |
| `pid_safety.py`             | 参数保护、保底策略、最佳结果记录、回退逻辑 |
| `firmware.cpp`              | 单片机侧示例固件，负责串口上报与执行 PID   |
| `system_id.py`              | 利用阶跃响应做系统辨识，给出初始 PID 建议  |
| `doctor.py`                 | 环境诊断检查工具，快速排查配置与连接问题   |
| `benchmark.py`              | 固定随机种子的对比工具，更偏开发验证用途   |
| `config.json`               | 运行配置文件                               |
| `docs/zh-CN/PROJECT_DOC.md` | 面向开发者的内部说明文档                   |

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
- 你用的是 Claude 中转站，但把它当成了 Anthropic 原生接口；这时应该用 `openai_claude` 或 `openai`
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

- 最新打包版请看 [Release](https://github.com/KINGSTON-115/llm-pid-tuner/releases/latest)
- 打包方法见 [Issue #11](https://github.com/KINGSTON-115/llm-pid-tuner/issues/11)
- 当前打包使用 Python `3.8`（见 `llm-pid-tuner.spec` 中 `matlabengineforpython3_8.pyd`）
- 历史测试环境包含 `R2022b`；文档里的 `R2025b` 只是示例路径版本，请替换成你本机实际安装的 MATLAB 版本
- 想看项目内部设计，请看 [PROJECT_DOC.md](docs/zh-CN/PROJECT_DOC.md)

## License

`CC BY-NC-SA 4.0`
