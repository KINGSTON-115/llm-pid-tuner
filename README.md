# LLM-PID-Tuner

用大语言模型辅助 PID 调参：从响应数据提出参数建议，复测效果，保留有效结果，变差时回退。
适用于温控、电机和其他已有 PID 闭环的控制对象。

**[下载桌面版 v2.5.0](https://github.com/KINGSTON-115/llm-pid-tuner/releases/tag/v2.5.0)** · **[下载 Agent Skill DLC v1.0.0](https://github.com/KINGSTON-115/llm-pid-tuner/releases/tag/skill-pid-tuning-v1.0.0)** · [English](docs/en-US/README.md)

## 选择你的用法

| 你想做什么 | 从这里开始 |
| --- | --- |
| 直接体验调参，无需硬件或 API Key | [Windows 桌面版](#桌面版快速开始)，选择离线规则仿真 |
| 让现有 agent 分析数据、规划调参实验 | [PID Tuning Skill DLC](#agent-skill-dlc) |
| 接入 Arduino / ESP32 等控制板 | [硬件与接口配置](docs/zh-CN/CONFIGURATION.md) |
| 调整已有 Simulink 模型 | [MATLAB / Simulink 指南](docs/zh-CN/MATLAB_GUIDE.md) |
| 修改代码或跟进开发 | [从源码运行](#从源码运行) |

## Agent Skill DLC

**把调参方法带到你正在使用的 agent。** 独立 `pid-tuning/` 文件夹保留了控制器公式与单位检查、
循证调参、参数边界、小步实验、复测和回退，附 CSV 分析、离线仿真与参数检查工具。
无需安装主程序或额外配置模型 API；随包脚本仅需 Python 3.10+ 标准库。

1. [下载 ZIP](https://github.com/KINGSTON-115/llm-pid-tuner/releases/download/skill-pid-tuning-v1.0.0/pid-tuning-skill-v1.0.0.zip)，解压得到 `pid-tuning/`。
2. 将整个文件夹放入 agent 配置的 skills 目录并重新加载；不支持自动发现时，让 agent 直接读取其中的 `SKILL.md`。
3. 在支持 `$skill-name` 的宿主中使用：

```text
使用 $pid-tuning 分析这份温控 CSV，比较各轮超调和尾部误差，给出下一轮的小步实验。
```

[完整安装与使用指南](docs/zh-CN/SKILL_DLC.md) · [Skill 源码](https://github.com/KINGSTON-115/llm-pid-tuner/tree/skill-pid-tuning-v1.0.0/skills/pid-tuning)

DLC 独立发布；随包脚本只做离线计算，实际设备连接由现有授权工具负责。

## 桌面版快速开始

1. 从 [v2.5.0 Release](https://github.com/KINGSTON-115/llm-pid-tuner/releases/tag/v2.5.0) 下载 `llm-pid-tuner-v2.5.0-windows-x64.zip`。
2. **完整解压到可写目录**，打开 `llm-pid-tuner-desktop.exe`，保留同目录的 `_internal`。
3. 保持“本地热系统仿真 / 离线规则体验”，点击“开始调参”。无需安装 Python、连接硬件或填写 API Key。

桌面版支持实时曲线、中英文切换、白昼 / 暗黑主题及 CSV / JSON 导出。
需要 AI 调参时，在“连接设置”填写模型接口，再选择“AI 自动调参”；连接设备或模型时选择串口或 Simulink 模式。
升级时可将原来的 `config.json` 复制到新目录。

详细操作见 [桌面工作台指南](docs/zh-CN/DESKTOP_GUIDE.md)。同包也提供终端入口：

```powershell
.\llm-pid-tuner.exe --tui       # 终端模式选择
.\llm-pid-tuner.exe sim         # 仿真 TUI
.\llm-pid-tuner.exe tune COM5   # 串口调参
```

## 配置与调参

桌面版通过“连接设置”填写接口和串口；源码运行可参考
[`config.example.json`](https://github.com/KINGSTON-115/llm-pid-tuner/blob/v2.5.0/config.example.json)。
AI 模式支持 OpenAI 兼容接口、Claude 原生接口、OrcaRouter，以及 Ollama / LM Studio 等本地服务。
**离线规则体验不需要模型服务。**

[配置指南](docs/zh-CN/CONFIGURATION.md)集中说明模型接口、硬件协议、代理、CSV 导出、参数护栏和常见问题。
Simulink 的模型准备、块路径、双控制器及 MATLAB Engine 安装见 [专门指南](docs/zh-CN/MATLAB_GUIDE.md)。

调参时可以表达“限制超调”或“优先响应速度”等偏好。程序结合响应数据给出建议，
通过限幅与复测决定是否保留；达到配置的验收标准后保持参数观察，避免继续过调。
最终参数需要在实际工况中验证，再写入固件或控制器配置。

## 从源码运行

使用 **Python 3.10+**；Simulink 模式还需匹配本机 MATLAB Engine 支持的 Python 版本。
`dev` 是默认开发分支；复现桌面发布版使用 `v2.5.0` 标签。`main` 同步用户文档，源码可能落后于发布标签。

```bash
git clone -b dev https://github.com/KINGSTON-115/llm-pid-tuner.git
cd llm-pid-tuner
python -m pip install -r requirements.txt
python launcher.py
```

其他入口：

```bash
python simulator.py              # 仿真 TUI，AI 调参需先配置模型接口
python tuner.py                  # 串口调参
python doctor.py                 # 检查配置与连接
```

纯文本输出可用 `python simulator.py --plain`；指定英文可用 `--lang en`。
控制器实现、串口协议与系统辨识入口见 [项目说明](docs/zh-CN/PROJECT_DOC.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [Skill DLC 指南](docs/zh-CN/SKILL_DLC.md) | 安装、迁移、离线示例与独立发布 |
| [桌面工作台](docs/zh-CN/DESKTOP_GUIDE.md) | 界面、运行控制、导出与打包 |
| [配置指南](docs/zh-CN/CONFIGURATION.md) | 接口、硬件、参数与故障排查 |
| [MATLAB / Simulink](docs/zh-CN/MATLAB_GUIDE.md) | 模型准备、Engine 安装与多控制器 |
| [项目说明](docs/zh-CN/PROJECT_DOC.md) | 核心模块、协议和调参流程 |
| [版本发布](https://github.com/KINGSTON-115/llm-pid-tuner/releases) | 下载、更新说明和历史版本 |

真实设备调参需有人值守，并保留限温、传感器异常保护和物理断电手段。
软件中的“停止”结束上位机调参，不等同于设备急停。

## 交流与教程

QQ 1 群：**1082281492** · [B 站教程](https://b23.tv/WVUuIFb) · [YouTube 教程](https://youtu.be/Giruc9kN53Y)

[MIT License](LICENSE)

<details>
<summary>Star History</summary>

<a href="https://www.star-history.com/?repos=KINGSTON-115%2Fllm-pid-tuner&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=KINGSTON-115/llm-pid-tuner&type=date&theme=dark&legend=top-left&sealed_token=-jtUW5cYPUGqtvzDDldf5jRjkZ9FU85x0zaizpza3zjIAlZIoIUNaY6zI2Y-P_qec8vzQV1SHDj4IrEjoEy5AXn45k_NshJEaS54lWWwWs0cH82DHkARXQ" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=KINGSTON-115/llm-pid-tuner&type=date&legend=top-left&sealed_token=-jtUW5cYPUGqtvzDDldf5jRjkZ9FU85x0zaizpza3zjIAlZIoIUNaY6zI2Y-P_qec8vzQV1SHDj4IrEjoEy5AXn45k_NshJEaS54lWWwWs0cH82DHkARXQ" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=KINGSTON-115/llm-pid-tuner&type=date&legend=top-left&sealed_token=-jtUW5cYPUGqtvzDDldf5jRjkZ9FU85x0zaizpza3zjIAlZIoIUNaY6zI2Y-P_qec8vzQV1SHDj4IrEjoEy5AXn45k_NshJEaS54lWWwWs0cH82DHkARXQ" />
 </picture>
</a>

</details>
