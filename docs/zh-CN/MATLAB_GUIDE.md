# MATLAB/Simulink 仿真调参指南

本文档说明如何使用 llm-pid-tuner 对 MATLAB/Simulink 仿真模型进行 LLM 辅助 PID 调参。

如果你打算源码运行、自己改模型适配逻辑，或做针对性开发，建议统一拉取 `dev` 分支代码，而不是直接使用 `main`。

---

## 快速开始（下载 exe，零代码运行）

如果你不想配置 Python 环境，直接下载打包好的 exe 即可：

1. 从 Releases 页面下载最新的 `llm-pid-tuner.exe`
2. 将 `llm-pid-tuner.exe` 和同目录的 `config.json` 放在同一个文件夹里
3. 编辑 `config.json`，填入你的 LLM API Key 和 Simulink 相关配置（详见第 3 步）
4. 双击运行 `llm-pid-tuner.exe`，或在终端中执行：
   ```
   llm-pid-tuner.exe
   ```
5. 在菜单中选择「Simulink 仿真调参」，程序会自动启动 MATLAB 并开始调参
6. 调参完成后按 Enter 退出，结果已自动保存回 `.slx` 文件

> **前提**：你的电脑上需要已安装并激活 MATLAB（exe 内置了 MATLAB Engine，但仍需要本机有 MATLAB 许可证）。

---

## 快速开始（下载 exe，零代码运行）

如果你不想配置 Python 环境，直接下载打包好的 exe 即可：

1. 从 Releases 页面下载最新的 `llm-pid-tuner.exe`
2. 将 `llm-pid-tuner.exe` 和同目录的 `config.json` 放在同一个文件夹里
3. 编辑 `config.json`，填入你的 LLM API Key 和 Simulink 相关配置（详见第 3 步）
4. 双击运行 `llm-pid-tuner.exe`，或在终端中执行：
   ```
   llm-pid-tuner.exe
   ```
5. 在菜单中选择「Simulink 仿真调参」，程序会自动启动 MATLAB 并开始调参
6. 调参完成后按 Enter 退出，结果已自动保存回 `.slx` 文件

> **前提**：你的电脑上需要已安装并激活 MATLAB（exe 内置了 MATLAB Engine，但仍需要本机有 MATLAB 许可证）。

---

## 这个模式适合什么场景

- 你已经在 Simulink 里搭好了被控对象模型，想快速找一组能用的 PID 初值
- 你的系统模型比较复杂（非线性、多环节、有时延），不好用 Ziegler-Nichols 这类经验公式直接估
- 你想在上真实硬件之前，先在仿真里把参数收敛到一个合理范围
- 你的硬件还没到货，但想提前跑通调参流程

## 它是怎么工作的

```text
Simulink 模型  ──同步运行一轮仿真──>  输出时间序列（To Workspace，Timeseries 格式）
                                              │
                                  提取 Time / Data 数组，计算稳态误差、超调、上升时间
                                              │
                                    LLM 分析本轮数据 + 历史记录，给出新 PID
                                              │
                             写回 Simulink PID Controller 模块参数
                                              │
                                  继续下一轮仿真 ──> 收敛
```

每轮仿真都从初始状态重新运行，LLM 看到的时间序列单位是**仿真毫秒（SimTime ms）**，不是真实世界时间。

---

## 第 1 步：安装 MATLAB Engine API for Python

这一步只需要做一次。MATLAB R2021b 及以上版本自带这个包，去 MATLAB 安装目录下执行：

```bash
cd <MATLAB_ROOT>/extern/engines/python
python setup.py install
```

Windows 示例路径（根据你的 MATLAB 版本替换）：
```
D:\Program Files\MATLAB\R2022b\extern\engines\python
```

安装完成后可以验证一下：
```bash
python -c "import matlab.engine; print('OK')"
```

---

## 第 2 步：准备你的 Simulink 模型

你的模型里需要有两个模块，其余结构随意：

**1. PID Controller 模块**

使用 Simulink 自带的标准 PID Controller 块即可。程序会通过模块路径直接写入 P / I / D 参数，并在调参结束后将最终参数保存回 `.slx` 文件。

如何查找模块的完整路径：
- 在 Simulink 模型窗口中，点击 PID Controller 块将其选中
- 在 MATLAB 命令行输入：
  ```matlab
  gcb
  ```
  返回的字符串就是该模块的完整路径，例如 `pid_tuner/PID Controller`
- 将这个路径填入 `config.json` 的 `MATLAB_PID_BLOCK_PATH` 字段

**2. To Workspace 模块**

把被控量（系统输出）连到一个 To Workspace 块，然后按以下步骤配置：

1. 双击 To Workspace 块，打开参数对话框
2. **Variable name**：填入变量名，例如 `y_out`（后面要填到 config 里）
3. **Save format**：从下拉菜单选择 `Timeseries`（必须是这个，不能用 Array 或 Structure）
4. **Sample time**：填 `-1`（继承模型步长）或与模型步长一致的数值
5. 点击 OK 保存

配置完成后，在 MATLAB 命令行手动跑一次仿真验证：
```matlab
sim('your_model_name');
whos y_out   % 应该能看到 y_out 是 timeseries 类型
```

模型保存为 `.slx` 格式，记下文件完整路径备用（建议用正斜杠，如 `C:/models/pid_tuner.slx`）。

---

## 第 3 步：配置 `config.json`

在原有 LLM 配置基础上，新增以下字段：

```json
{
  "LLM_API_KEY": "你的key",
  "LLM_API_BASE_URL": "https://api.openai.com/v1",
  "LLM_MODEL_NAME": "gpt-4o",
  "LLM_PROVIDER": "openai",

  "MATLAB_MODEL_PATH"     : "C:/models/my_pid_model.slx",
  "MATLAB_PID_BLOCK_PATH" : "my_pid_model/PID Controller",
  "MATLAB_ROOT"           : "C:/Program Files/MATLAB/R2022b",
  "MATLAB_OUTPUT_SIGNAL"  : "y_out",
  "MATLAB_SIM_STEP_TIME"  : 10.0,
  "MATLAB_SETPOINT"       : 200.0
}
```

| 字段 | 说明 | 填写示例 |
| :--- | :--- | :--- |
| `MATLAB_MODEL_PATH` | Simulink `.slx` 文件完整路径 | `C:/models/my_model.slx` |
| `MATLAB_PID_BLOCK_PATH` | PID 模块在模型中的完整路径 | `my_model/PID Controller` |
| `MATLAB_ROOT` | MATLAB 安装根目录 | `C:/Program Files/MATLAB/R2022b` |
| `MATLAB_OUTPUT_SIGNAL` | To Workspace 变量名 | `y_out` |
| `MATLAB_SIM_STEP_TIME` | 每轮调参运行的仿真时长（仿真秒数） | `10.0` |
| `MATLAB_SETPOINT` | 调参目标值，需与模型中 Setpoint 一致 | `200.0` |

`MATLAB_PID_BLOCK_PATH` 的格式是**模型名/模块名**，模型名就是 `.slx` 文件名去掉扩展名。如果 PID 块在子系统里，路径写成 `my_model/子系统名/PID Controller`。

`MATLAB_ROOT` 填 MATLAB 的安装根目录，不是 `extern/engines/python` 子目录。

- 如果你用的是打包版 `llm-pid-tuner.exe`，建议把 `MATLAB_ROOT` 一起填上，程序会根据这个目录补 MATLAB Engine 运行时路径
- 如果你是源码运行，而且当前 Python 环境已经能 `import matlab.engine`，`MATLAB_ROOT` 可以留空
- 如果源码运行仍然报 `No module named matlab.engine`，先检查第 1 步的 Engine 安装，再补 `MATLAB_ROOT`

---

## 第 4 步：运行

**方式一：通过 launcher（推荐）**

```bash
llm-pid-tuner.exe
```

在菜单中选择「Simulink 仿真调参」，程序会自动启动 MATLAB Engine、加载模型并开始调参。调参完成后按 Enter 退出，最终 PID 参数会自动保存回 `.slx` 文件。

**方式二：直接调用 simulator**

```bash
python simulator.py
```

`MATLAB_MODEL_PATH` 填了值，程序会自动切换到 Simulink 模式，无需额外参数。

---

## 调参策略说明

LLM 采用标准的三阶段调参顺序，与工程实践经验一致：

**阶段一：单独整定 P**
- 保持 I 接近 0、D = 0，只调整 P
- 大步提升 P，快速找到响应速度满意且超调 < 5% 的 P 区间

**阶段二：引入 I 消除稳态误差**
- P 稳定后才开始加 I
- I 从小到大，直到稳态误差趋近于零
- 若加 I 后出现超调或振荡，说明 I 过大，需减小

**阶段三：必要时引入 D 抑制超调**
- 仅当 P+I 组合仍有明显超调或振荡时才引入 D
- D 从小值开始，D 过大会导致响应变慢（过度阻尼）
- 若 P+I 已满足要求，D 保持 0

> **注意**：每轮仿真时间序列中的 `SimTime(ms)` 是仿真时间（毫秒），不是真实世界时间。例如仿真步长设为 15 秒时，数据范围是 0 ~ 15000ms，LLM 会以此评估上升时间。

---

## 常见问题

**Q：提示 `No module named matlab.engine`**

说明 MATLAB Engine API 还没装，按第 1 步操作。注意要用安装项目依赖的**同一个 Python 环境**执行 `setup.py install`。

**Q：提示 `MATLAB 连接失败` 或启动超时**

- 检查 MATLAB 是否已激活（License 有效）
- 检查 `MATLAB_MODEL_PATH` 路径是否正确，用正斜杠 `/` 或双反斜杠 `\\`
- 检查 `MATLAB_PID_BLOCK_PATH` 是否与模型里的模块路径完全一致（大小写敏感）
- MATLAB Engine 首次启动较慢（30～60 秒），属正常现象

**Q：To Workspace 读不到数据**

- 确认 To Workspace 模块的 Save format 设为 **`Timeseries`**（不能用 Array 或 Structure）
- 确认变量名与 `MATLAB_OUTPUT_SIGNAL` 完全一致
- 确认模型在 MATLAB 里手动运行一次后工作区里有这个变量

**Q：LLM 反映响应「极慢」，但实际仿真看起来还好**

这通常是因为 LLM 把仿真时间（毫秒）误当成真实秒数。本程序已在数据表头标注 `SimTime(ms)` 并在提示词中说明单位，如仍出现此问题，可适当减小 `MATLAB_SIM_STEP_TIME` 让单轮仿真时长更直观。

**Q：每轮调参很慢**

`MATLAB_SIM_STEP_TIME` 控制每轮仿真时长（仿真秒数）。适当减小可加快迭代速度，但需保证每轮能采集到足够反映系统响应（上升、稳态）的完整数据。一般建议设为系统响应时间常数的 3～5 倍。

**Q：调参结束后 PID 参数保存到哪里了**

程序在调参完成（或用户中断）时，会把当前最优 PID 参数写回 `.slx` 文件并保存。下次在 MATLAB 中打开该模型，PID Controller 模块中的参数即为调参结果。
