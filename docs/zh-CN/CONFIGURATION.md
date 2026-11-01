# 配置与故障排查

[返回首页](../../README.md) · [English](../en-US/CONFIGURATION.md)

本页适用于桌面版 v2.5.0 和后续 `dev` 版本。源码请使用发布标签或 `dev`，
不要假设 `main` 的旧代码具备所有已发布功能。Agent Skill 的安装与数据协议见 [DLC 指南](SKILL_DLC.md)。

## 配置位置与优先级

桌面 EXE 使用所在目录的 `config.json`；源码使用当前工作目录，首次运行会生成默认配置。
桌面“连接设置”可编辑常用字段；高级字段参考 [v2.5.0 配置模板](https://github.com/KINGSTON-115/llm-pid-tuner/blob/v2.5.0/config.example.json)。
同名环境变量优先于文件配置，修改后重新启动。配置文件保存 API 凭据，分享日志或配置时请移除密钥。

桌面“离线规则体验”不需要模型服务；以下接口字段用于 **AI 调参**。

## 模型接口

以 OpenAI 兼容接口为例，填写自己的地址、可用模型 ID 和凭据：

```json
{
  "LLM_PROVIDER": "openai",
  "LLM_API_BASE_URL": "https://your-provider.example/v1",
  "LLM_MODEL_NAME": "your-model-id",
  "LLM_API_KEY": "your-api-key"
}
```

| 服务 | `LLM_PROVIDER` | `LLM_API_BASE_URL` |
| --- | --- | --- |
| OpenAI | `openai` | `https://api.openai.com/v1` |
| DeepSeek / MiniMax 等兼容接口 | `openai` | 服务商提供的兼容地址 |
| Claude 中转 / OneAPI / New API | `openai_claude` 或 `openai` | 中转服务的兼容地址 |
| Claude 原生 | `anthropic` | `https://api.anthropic.com` |
| Ollama | `openai` | `http://localhost:11434/v1` |
| LM Studio | `openai` | `http://localhost:1234/v1` |
| OrcaRouter | `orcarouter` / `orcarouter_oauth` | 通过下方设置入口配置 |

模型 ID 以服务商或本地服务实际提供的列表为准。使用 Ollama / LM Studio 时先启动模型服务，
认证按本地服务要求设置。Claude 中转走兼容协议时，不要选原生 `anthropic`。

### OrcaRouter

Windows 发布包运行：

```powershell
.\llm-pid-tuner.exe tune --orcarouter-settings
```

源码使用 `python tuner.py --orcarouter-settings`；TUI 内也可按 `o` 打开设置。
可粘贴已有 Key 或进行浏览器登录，并从模型目录选择模型。保存后在下一次调参会话生效。

| 操作 | 源码命令 |
| --- | --- |
| 使用已有 Key | `python tuner.py --orcarouter-key sk-orca-your-key` |
| 浏览器登录 | `python tuner.py --orcarouter-login` |
| 无浏览器，手动粘贴授权码 | `python tuner.py --orcarouter-login --orcarouter-flow B` |
| 查看凭据状态 | `python tuner.py --orcarouter-status` |
| 清除本地凭据 | `python tuner.py --orcarouter-logout` |

打包版把 `python tuner.py` 换成 `.\llm-pid-tuner.exe tune` 即可。
已有 Key 对应 `orcarouter`，登录签发 Key 对应 `orcarouter_oauth`；凭据保存在
`config.json`，也可通过 `ORCAROUTER_API_KEY` 环境变量提供。
登录使用 PKCE，默认 Flow A 自动接收本机回调，Flow B 用于手动授权码。
签发的是账号 API Key，费用计入该账号；可在[已授权应用](https://www.orcarouter.ai/console/authorized-apps)撤销。
本地 logout 不等同于服务端撤销；遇到 `401` / `needs_reauth` 时检查凭据并重新登录。

模型列表来自 `https://api.orcarouter.ai/v1/models`；目录不可用时，设置页会标明回退状态。
自建部署可使用 `ORCAROUTER_BASE_URL`，或优先级更高的 `ORCAROUTER_AUTH_BASE_URL`
和 `ORCAROUTER_API_BASE_URL` 分别设置认证与推理地址。默认认证地址为
`https://www.orcarouter.ai`，推理地址为 `https://api.orcarouter.ai/v1`；非回环地址要求 HTTPS。

## 串口硬件

桌面中选择具体端口，波特率与固件一致。源码 / TUI 可设置自动扫描：

```json
{
  "SERIAL_PORT": "AUTO",
  "BAUD_RATE": 115200,
  "HARDWARE_PROFILE": "generic_serial_csv"
}
```

默认协议参考 [`firmware.cpp`](https://github.com/KINGSTON-115/llm-pid-tuner/blob/v2.5.0/firmware.cpp)，遥测字段为：

```text
timestamp_ms,setpoint,input,pwm,error,p,i,d
```

| `HARDWARE_PROFILE` | 支持范围 |
| --- | --- |
| `generic_serial_csv` | 默认 CSV 固件与演示模式 |
| `stm32f407_openmv` | STM32F407 + OpenMV 的 `Status:`、`T:x,y`、`N` 遥测 |
| `mspm0_datavision` | 只读遥测，不写回 PID；桌面调参暂不支持 |

TUI 按 `t` 可修改运行时目标值；默认固件路径发送 `SETPOINT:<value>`，只读适配器保持只读。
数据格式、参数写入协议及多控制器细节见 [项目说明](PROJECT_DOC.md)。

## 调参偏好与护栏

支持预调参对话的交互入口可接收“超调不超过 5%”“优先稳定”等自然语言偏好。
这些内容用于指导模型建议；实际验收阈值、参数边界仍应按对象设置。
源码提示词位于 [`llm/prompts.py`](https://github.com/KINGSTON-115/llm-pid-tuner/blob/dev/llm/prompts.py)，
其中 `_BASE_SYSTEM_PROMPT` 定义通用策略，`_MODE_NOTES` 定义各模式说明。

| 字段 | 用途 |
| --- | --- |
| `BUFFER_SIZE` / `MAX_TUNING_ROUNDS` | 每轮采样数 / 调参轮数预算 |
| `LLM_REQUEST_TIMEOUT` / `LLM_DEBUG_OUTPUT` | 请求超时 / 调试输出 |
| `PID_LIMITS` | 各模式 P/I/D 的绝对上下限和单轮增幅；程序在写入前执行限幅 |
| `PID_MAX_INCREASE_RATIO` | 额外的全局单轮增幅上限，默认 `0` 表示不增加此额外限制 |
| `GOOD_ENOUGH_AVG_ERROR` | 平均误差验收阈值 |
| `GOOD_ENOUGH_STEADY_STATE_ERROR` | 稳态误差验收阈值 |
| `GOOD_ENOUGH_OVERSHOOT` | 超调验收阈值 |
| `REQUIRED_STABLE_ROUNDS` | 连续达标观察轮数，默认 `3` |

达标后保持当前参数继续观察；连续达标后停止，观察中变差则重置稳定计数并恢复调参。
不要把示例参数上限套用到所有设备。采样超时与最少有效样本数另有内建保护，不是上述配置项。

## 代理与导出

仅在需要代理时填写 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY`，留空表示不启用。
例如在 `config.json` 中添加：

```json
{
  "HTTP_PROXY": "http://127.0.0.1:7890",
  "HTTPS_PROXY": "http://127.0.0.1:7890",
  "CSV_EXPORT_PATH": "logs/tuning_data.csv"
}
```

`CSV_EXPORT_PATH` 可为相对或绝对路径，持续追加遥测并记录 `session_id` 与 `round`；
空字符串关闭此导出。桌面也可手动导出 CSV / JSON，操作见 [桌面指南](DESKTOP_GUIDE.md)。
导出的 CSV 可交给 [PID Tuning Skill](SKILL_DLC.md) 继续分析。

## 常见问题

| 现象 | 先检查 |
| --- | --- |
| EXE 无法启动 | 完整解压并保留 `_internal`，确认目录可写；在 PowerShell 运行终端入口查看报错 |
| 找不到串口 | 连接、驱动、其他程序占用；波特率与固件是否一致 |
| 已连接但无数据 | 设备遥测是否符合所选 `HARDWARE_PROFILE` |
| API 能聊天但不能调参 | 地址、模型 ID、协议选择和返回格式；可用 `python doctor.py` 检查连接 |
| 找不到 `matlab.engine` 或模型块 | 按 [MATLAB 指南](MATLAB_GUIDE.md) 配置同一 Python 环境与准确块路径 |
| 调参后变差 | 查看轮次记录与回退结果，核对控制器单位、采样周期和验收条件 |
| 想离线使用 | 桌面选离线规则；需要 AI 时先启动 Ollama / LM Studio 等本地模型服务 |
