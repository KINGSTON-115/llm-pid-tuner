# 原生桌面工作台

桌面版使用 PySide6 / Qt Widgets，直接打开独立窗口，不需要浏览器或 Node.js。
它复用原有 PID 引擎、参数护栏、最佳结果回退和稳定性判定。

## 启动

**Windows 免安装版**：从 [v2.5.0 Release](https://github.com/KINGSTON-115/llm-pid-tuner/releases/tag/v2.5.0)
下载 `llm-pid-tuner-v2.5.0-windows-x64.zip`，完整解压后双击 `llm-pid-tuner-desktop.exe`。
不需要安装 Python，必须保留同目录的 `_internal`。同包的 `llm-pid-tuner.exe` 提供终端命令。
升级时将原来的 `config.json` 复制到新目录即可保留配置；发布包不包含个人配置或密钥。

源码方式需要 **Python 3.10 或更新版本**：
使用 `v2.5.0` 标签或更新的 `dev` 分支；`main` 的源码更新可能晚于发布标签。

```powershell
python -m pip install -r requirements-desktop.txt
python launcher.py
```

也可以执行 `python desktop_main.py` 或 `python launcher.py gui`。
Windows 上可以用安装过依赖的 Python 打开 `desktop_main.pyw`，不显示命令行窗口。
电脑装了多个 Python 时，请用同一个解释器安装依赖、启动程序。

打包后，双击 `dist/llm-pid-tuner-desktop/llm-pid-tuner-desktop.exe`。
分发时需要保留整个 `llm-pid-tuner-desktop` 文件夹，包括 `_internal`。
桌面 EXE 使用其所在目录中的 `config.json`；源码运行使用当前工作目录。
原生桌面界面从 v2.5.0 起包含在正式 Release 中。

## 外观与语言

左侧“外观”可选择 **白昼模式、暗黑模式、跟随系统**。默认跟随系统；切换立即覆盖
主窗口、曲线、菜单、设置和文件对话框，支持的 Windows 版本还会更新原生标题栏颜色。
跟随系统时，系统的深浅色变化会同步到应用。

“语言 / Language”可全局切换 **简体中文 / English**，无需重新启动。界面、提示、
表头、已有内置日志、离线规则说明和错误提示会同步更新。运行中或暂停时切换，
不会清空曲线、轮次记录或修改当前 PID。模型已有分析与外部诊断保留原文；
下一次 AI 请求会要求使用所选语言，进行中的请求仍按发起时的语言返回。

选择会写入本机 `config.json` 的 `DESKTOP_THEME`（`light` / `dark` / `system`）和
`DESKTOP_LANGUAGE`（`zh` / `en`）字段，下次启动恢复。保存失败会提示并保留原设置。
未选择语言时使用系统语言；临时覆盖可执行 `python launcher.py gui --lang en`，
此参数不覆盖已保存的语言偏好。CSV / JSON 导出的字段名保持固定，方便后续程序读取。

## 第一次体验

1. 保持“本地热系统仿真”和“离线规则体验”。无需填写 API Key。
2. 设置初始 Kp、Ki、Kd 和目标值，点击“开始调参”。
3. 在曲线中观察实际响应、目标值和控制输出；悬停可查看对应数值。
4. 在“调参分析”查看本轮解释，在“轮次记录”比较平均误差、稳态误差和超调。
5. “高级参数”可设置最大轮数、每轮采样数、仿真速度。默认 12 轮、每轮 100 点、10 倍速度。

离线模式使用项目内置的保守规则，**不会调用 AI**。这是真实热系统模型的仿真结果，
不是预制曲线。目标值默认保持不变，也可在运行时输入新值并点击“应用运行时目标值”。
仿真速度改变等待时间，不改变模型的采样步长。

## AI、硬件和 Simulink

打开左侧“连接设置”，填写接口协议、API 地址、模型名称、API Key 和超时，保存后选择“AI 自动调参”。
支持项目原有的 OpenAI 兼容接口、Claude 原生和 Claude 中转协议。
API Key 在窗口中遮掩显示，保存位置仍是本机 `config.json`；它不是加密密钥库。
保存只更新编辑过的字段，保留其他配置，包括代理和高级 Simulink 设置。
环境变量仍按原有配置规则在下次启动时覆盖文件配置。
已有的 OrcaRouter 登录凭据与模型配置可直接用于桌面 AI 调参；登录和模型目录选择继续使用
`python tuner.py --orcarouter-settings` 入口，桌面设置中不会把它改成自由填写模型名。

- **串口硬件**：选择具体端口或 `DEMO` 虚拟串口，可用“刷新”重新扫描。
  波特率和硬件协议在连接设置中配置。连接时读取设备当前 PID 和目标值，不写入界面初始值。
  开始后，AI 调参引擎会按现有护栏写回参数。MSPM0 DataVision 为只读协议，桌面调参暂不支持。
  双控制器遥测会显示第二组 PID，并保存在 CSV 中。
- **Simulink**：需要本机安装 MATLAB Engine。在连接设置中选择模型文件，填写 PID 模块路径、
  输出信号及可选控制信号、目标值模块。读取模型当前 PID，按仿真步返回曲线。
  双控制器和独立增益块等高级设置继续使用 `config.json`，参见 [MATLAB 指南](MATLAB_GUIDE.md)。
  MATLAB 初始化或单次仿真调用期间，停止操作会等待该调用返回。

## 暂停、停止和导出

- **暂停**冻结采样和参数应用；正在进行的模型请求可以完成，结果会等待继续后再应用。
- **停止**结束上位机调参并释放连接，不向真实设备发送急停或关闭执行器指令。
  网络请求或 MATLAB 调用可能需要等待超时/返回。关闭窗口也会先等待任务退出。
- **导出结果 → CSV**导出本次全部采样数据；**JSON**导出各轮指标和最终参数。
  图中最多保留最近 6,000 点，完整数据暂存磁盘，导出不受图表上限影响。
  重新开始实验或关闭窗口会清除本次临时记录，需要保留时请先导出。

原有入口继续可用：

```powershell
python launcher.py --tui       # 旧版终端模式选择
python launcher.py sim         # 仿真 TUI
python launcher.py tune COM5   # 硬件模式
python launcher.py --plain     # 仿真文本输出
```

Windows 发布包中把 `python launcher.py` 换成 `.\llm-pid-tuner.exe` 即可运行上述终端命令。
例如 OrcaRouter 登录和模型目录可使用 `.\llm-pid-tuner.exe tune --orcarouter-settings`。

## 打包与验证

```powershell
python -m pip install -r requirements-desktop.txt pyinstaller pytest
python -m pytest
python -m PyInstaller --noconfirm llm-pid-tuner-desktop.spec
```

桌面包关闭控制台窗口，Qt 原生绘图不依赖 WebEngine。协议适配可使用 DEMO 和测试替身验证，
真实硬件和本机 Simulink 模型仍需在实际环境中联调。

桌面回归测试覆盖运行中切换语言和主题、暂停后的数据保持、继续采样与更新目标值、
停止与连接释放、导出完整数据、设置保存失败恢复、关闭窗口后的信号清理，以及异常采样、
图表窗口和 6,000 点内存上限。两种主题还检查文字与曲线对比度，四种主题/语言组合检查紧凑窗口布局。
下拉列表测试实际打开主题、语言、运行模式和接口协议选项，检查普通与选中项的文字渲染，
并覆盖深浅色反复切换、鼠标与键盘选择及 Windows DPI 缩放。
