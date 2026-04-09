# MATLAB/Simulink 仿真调参指南

本文档说明如何使用 llm-pid-tuner 对 MATLAB/Simulink 模型进行 **AI 调参**。

这份指南重点覆盖三类情况：

1. **标准单个 PID/PI/PD/P 控制器块**
2. **双控制器 / 主副环（例如 cascade）**
3. **分离式 P/I/D 增益块**

如果你只是想快速跑通，先按“标准单个控制器块”配置即可；如果你的模型更复杂，再逐步切到双控制器或分离增益块配置。

---

## 快速开始（下载 exe，零代码运行）

如果你不想配置 Python 环境，直接下载打包好的 exe 即可：

1. 从 Releases 页面下载最新的 `llm-pid-tuner.exe`
2. 将 `llm-pid-tuner.exe` 和同目录的 `config.json` 放在同一个文件夹里
3. 编辑 `config.json`，填入你的 LLM API Key 和 Simulink 相关配置（详见下文）
4. 双击运行 `llm-pid-tuner.exe`，或在终端中执行：

   ```bash
   llm-pid-tuner.exe
   ```

5. 在菜单中选择「Simulink 仿真调参」
6. 调参完成后按 Enter 退出，结果会保存回 `.slx` 文件

> **前提**：你的电脑上需要已安装并激活 MATLAB。打包版不会替代本机 MATLAB 许可证。

---

## 这个模式适合什么场景

- 你已经在 Simulink 里搭好了控制对象，想快速找一组能用的参数
- 你的系统不是简单单环，有主副环、双控制器或分离式增益块
- 你想在上真实硬件之前，先在仿真里把参数收敛到合理范围
- 你的模型里控制器参数命名不是标准 `P/I/D`，而是 `Kp/Ki/Kd` 或 `ProportionalGain/IntegralGain/DerivativeGain`
- 你希望程序尽量少改模型结构，而是直接适配现有 Simulink 工程

---

## 它是怎么工作的

```text
Simulink 模型  ──同步运行一轮仿真──>  输出时间序列（To Workspace）
                                              │
                                  提取 Time / Data 数组，计算误差、超调、稳态误差
                                              │
                                  LLM 分析本轮数据 + 历史记录，给出新参数建议
                                              │
                     写回一个或多个控制器块（或分离式 P/I/D 增益块）
                                              │
                                  继续下一轮仿真 ──> 收敛
```

每轮仿真都从初始状态重新运行，LLM 看到的时间序列单位是 **仿真毫秒（SimTime ms）**，不是真实世界时间。

---

## 第 1 步：安装 MATLAB Engine API for Python

这一步只需要做一次。MATLAB R2021b 及以上版本自带这个包，去 MATLAB 安装目录下执行：

```bash
cd <MATLAB_ROOT>/extern/engines/python
python setup.py install
```

Windows 示例路径（根据你的 MATLAB 版本替换）：

```text
D:/Program Files/MATLAB/R2025b/extern/engines/python
```

安装完成后可以验证一下：

```bash
python -c "import matlab.engine; print('OK')"
```

---

## 第 2 步：准备你的 Simulink 模型

### 先记住一个通用技巧：怎么找块路径

`MATLAB_PID_BLOCK_PATH`、`MATLAB_PID_BLOCK_PATH_2`、`MATLAB_SETPOINT_BLOCK`、`MATLAB_P/I/D_BLOCK_PATH(_2)` 本质上都在填 **Simulink 块的完整路径**。

找法统一：

1. 在 Simulink 里点击你要填写的那个块
2. 在 MATLAB Command Window 输入：

   ```matlab
   gcb
   ```

3. 把返回的字符串原样复制到 `config.json`

例如，如果 `gcb` 返回：

```text
pid_tuner/Inner Loop/PID Controller
```

那就直接填到对应字段里，不需要自己手动拼路径。

如果块在子系统里，返回路径会自动包含层级。

### 必需项 1：输出信号

把你想调的被控量接到一个 To Workspace 块，然后按以下步骤配置：

1. 双击 To Workspace 块
2. **Variable name**：填变量名，例如 `y_out`
3. **Save format**：推荐 `Timeseries`
4. **Sample time**：填 `-1`（继承模型步长）或与模型步长一致
5. 点击 OK 保存

手动验证：

```matlab
sim('your_model_name');
whos y_out
```

如果你模型里输出变量名不稳定，也可以后面在 `config.json` 里用 `MATLAB_OUTPUT_SIGNAL_CANDIDATES` 提供一组候选名。

### 必需项 2：控制器参数必须能被写回

程序当前支持三种常见结构：

#### 方案 A：标准单个控制器块

最推荐。比如标准 `PID Controller`、`PI Controller`、`PD Controller`、`P Controller`。

常见可兼容参数名：

- `P` / `I` / `D`
- `Kp` / `Ki` / `Kd`
- `P_Gain` / `I_Gain` / `D_Gain`
- `ProportionalGain` / `IntegralGain` / `DerivativeGain`

#### 方案 B：双控制器 / 主副环

例如：

- `Outer PID`
- `Inner PID`

程序会分别向两组控制器写参数。这个支持目前属于**最小可用**：推荐你显式填写两组路径，不要完全依赖自动识别。

#### 方案 C：分离式 P/I/D 增益块

如果你的控制器不是单个 PID block，而是自己拼出来的结构，也可以：

- 单独给 P 块路径
- 单独给 I 块路径
- 单独给 D 块路径

程序会分别写这些块的 `Gain / Value / K / Coefficient` 之类参数。

### 建议项：设定值块

如果你的设定值来源很明确，建议直接记下它的块路径，后面填到 `MATLAB_SETPOINT_BLOCK`。

这样比完全依赖自动识别更稳。

### 建议项：控制输出信号

如果你愿意把控制器输出（例如 `u_out`）也导到 To Workspace，建议后面填到 `MATLAB_CONTROL_SIGNAL`。

这样程序就不只能看到占位 `PWM=0.0`，而是能看到真实控制输出，对调参更有帮助。

---

## 第 3 步：配置 `config.json`

### 最小单控制器配置

```json
{
  "LLM_API_KEY": "你的key",
  "LLM_API_BASE_URL": "https://api.openai.com/v1",
  "LLM_MODEL_NAME": "gpt-4o",
  "LLM_PROVIDER": "openai",

  "MATLAB_MODEL_PATH": "C:/models/my_pid_model.slx",
  "MATLAB_PID_BLOCK_PATH": "my_pid_model/PID Controller",
  "MATLAB_ROOT": "D:/Program Files/MATLAB/R2025b",
  "MATLAB_OUTPUT_SIGNAL": "y_out",
  "MATLAB_SIM_STEP_TIME": 10.0,
  "MATLAB_SETPOINT": 200.0
}
```

### 字段说明

| 字段 | 说明 | 填写示例 |
| :--- | :--- | :--- |
| `MATLAB_MODEL_PATH` | Simulink `.slx` 文件完整路径 | `C:/models/my_model.slx` |
| `MATLAB_PID_BLOCK_PATH` | 主控制器块完整路径 | `my_model/PID Controller` |
| `MATLAB_ROOT` | MATLAB 安装根目录 | `D:/Program Files/MATLAB/R2025b` |
| `MATLAB_OUTPUT_SIGNAL` | To Workspace 输出变量名 | `y_out` |
| `MATLAB_SIM_STEP_TIME` | 每轮调参运行的仿真时长（仿真秒数） | `10.0` |
| `MATLAB_SETPOINT` | 调参目标值 | `200.0` |

### 新增兼容字段

| 字段 | 什么时候用 | 说明 |
| :--- | :--- | :--- |
| `MATLAB_OUTPUT_SIGNAL_CANDIDATES` | 输出变量名不统一时 | 给一组候选输出名，程序按顺序尝试 |
| `MATLAB_CONTROL_SIGNAL` | 想让程序看到真实控制输出时 | 例如 `u_out` |
| `MATLAB_SETPOINT_BLOCK` | 自动识别设定值块不稳定时 | 显式指定 setpoint 源块 |
| `MATLAB_PID_BLOCK_PATHS` | 你有多个候选控制器块时 | 作为候选控制器路径列表 |
| `MATLAB_PID_BLOCK_PATH_2` | 双控制器 / 主副环时 | 第二组控制器块 |
| `MATLAB_P_BLOCK_PATH` `MATLAB_I_BLOCK_PATH` `MATLAB_D_BLOCK_PATH` | 控制器不是单块 PID，而是分离式增益块时 | 分别指定 P/I/D 块 |
| `MATLAB_P_BLOCK_PATH_2` `MATLAB_I_BLOCK_PATH_2` `MATLAB_D_BLOCK_PATH_2` | 第二组分离式增益块时 | 用于双控制器 |

---

### 这些字段怎么选最不容易乱

- 不带后缀的字段就是第一组 / 主控制器；带 `_2` 的字段就是第二组控制器
- 你已经知道准确块路径时，优先填 `MATLAB_PID_BLOCK_PATH`、`MATLAB_PID_BLOCK_PATH_2`、`MATLAB_SETPOINT_BLOCK`、`MATLAB_P/I/D_BLOCK_PATH`
- 你还不确定主控制器到底是哪一个，但能列出几条候选路径时，再填 `MATLAB_PID_BLOCK_PATHS`
- 自动识别是兜底方案，不建议把它当主要配置方式
- `MATLAB_CONTROL_SIGNAL` 和 `MATLAB_SETPOINT_BLOCK` 是推荐增强项，不是第一次跑通时的硬性必填

---

## 推荐填写方式（从最小配置逐步增强）

### 方案 A：标准单个 PID Controller（最简单）

下面这个示例是在“最小单控制器配置”的基础上，再加上推荐但可选的 `MATLAB_CONTROL_SIGNAL` 和 `MATLAB_SETPOINT_BLOCK`。

```json
{
  "MATLAB_MODEL_PATH": "C:/models/pid_tuner.slx",
  "MATLAB_PID_BLOCK_PATH": "pid_tuner/PID Controller",
  "MATLAB_ROOT": "D:/Program Files/MATLAB/R2025b",
  "MATLAB_OUTPUT_SIGNAL": "y_out",
  "MATLAB_CONTROL_SIGNAL": "u_out",
  "MATLAB_SETPOINT_BLOCK": "pid_tuner/Constant",
  "MATLAB_SIM_STEP_TIME": 15.0,
  "MATLAB_SETPOINT": 100.0
}
```

### 方案 B：双控制器 / 主副环

```json
{
  "MATLAB_MODEL_PATH": "C:/models/cascade_pid.slx",
  "MATLAB_PID_BLOCK_PATH": "cascade_pid/Outer PID",
  "MATLAB_PID_BLOCK_PATH_2": "cascade_pid/Inner PID",
  "MATLAB_ROOT": "D:/Program Files/MATLAB/R2025b",
  "MATLAB_OUTPUT_SIGNAL": "y_out",
  "MATLAB_CONTROL_SIGNAL": "u_out",
  "MATLAB_SETPOINT_BLOCK": "cascade_pid/Setpoint",
  "MATLAB_SIM_STEP_TIME": 10.0,
  "MATLAB_SETPOINT": 100.0
}
```

### 方案 C：分离式 P/I/D 增益块

```json
{
  "MATLAB_MODEL_PATH": "C:/models/split_pid.slx",
  "MATLAB_P_BLOCK_PATH": "split_pid/P Gain",
  "MATLAB_I_BLOCK_PATH": "split_pid/I Gain",
  "MATLAB_D_BLOCK_PATH": "split_pid/D Gain",
  "MATLAB_ROOT": "D:/Program Files/MATLAB/R2025b",
  "MATLAB_OUTPUT_SIGNAL": "y_out",
  "MATLAB_CONTROL_SIGNAL": "u_out",
  "MATLAB_SETPOINT_BLOCK": "split_pid/Setpoint",
  "MATLAB_SIM_STEP_TIME": 10.0,
  "MATLAB_SETPOINT": 100.0
}
```

### 方案 D：不想精确找块，先给候选路径

```json
{
  "MATLAB_MODEL_PATH": "C:/models/multi_loop.slx",
  "MATLAB_PID_BLOCK_PATHS": [
    "multi_loop/Outer PID",
    "multi_loop/Inner PID",
    "multi_loop/Backup Controller"
  ],
  "MATLAB_ROOT": "D:/Program Files/MATLAB/R2025b",
  "MATLAB_OUTPUT_SIGNAL": "y_out",
  "MATLAB_OUTPUT_SIGNAL_CANDIDATES": ["y_out", "yout", "plant_y"],
  "MATLAB_SIM_STEP_TIME": 10.0,
  "MATLAB_SETPOINT": 100.0
}
```

如果方案 D 能跑通，建议下一步把程序最终识别到的主控制器路径改回显式的 `MATLAB_PID_BLOCK_PATH`，这样后续会更稳。

---

## 自动识别现在能做什么

新版程序启动时会尽量打印一份识别摘要，例如：

- 主控制器块路径
- 第二控制器块路径（如果有）
- setpoint 块
- 输出信号
- 控制信号

它的作用是：

1. 帮你确认程序到底拿到了哪个控制器块
2. 出问题时更容易判断是“路径填错”还是“模型结构不兼容”

> **重要**：自动识别是 best-effort，不是万能识别。复杂模型下，推荐你显式填好主控制器路径、第二控制器路径和 setpoint 块路径。

---

## 使用复杂配置时的建议

如果你是第一次接 Simulink 调参，建议按下面顺序来：

1. **先用方案 A 跑通单控制器**
2. 再加 `MATLAB_CONTROL_SIGNAL`，让程序看到真实控制输出
3. 如果是多环，再补 `MATLAB_PID_BLOCK_PATH_2`
4. 如果不是标准 PID Controller，而是自己拼的结构，再改成分离式 `MATLAB_P/I/D_BLOCK_PATH`

不要一开始把所有高级字段全填上。最稳的思路是：

**先最小可用，再逐步增加兼容字段。**

---

## 第 4 步：运行

### 方式一：通过 launcher（推荐）

```bash
llm-pid-tuner.exe
```

在菜单中选择「Simulink 仿真调参」，程序会：
1. **执行环境诊断**：自动调用内部的 `doctor.py` 逻辑，检查 MATLAB Engine 是否可用、配置字段是否完整。
2. **预调参对话**（交互模式下）：你可以用自然语言输入调参偏好（如“超调不能超过 5%”）。
3. **启动仿真调参**：自动启动 MATLAB Engine、加载模型并开始调参。调参完成后按 Enter 退出，最终 PID 参数会自动保存回 `.slx` 文件。

### 方式二：直接调用 simulator

```bash
python simulator.py
```

`MATLAB_MODEL_PATH` 填了值，程序会自动切换到 Simulink 模式，无需额外参数。
如果你想强制使用英文界面，可以带上语言参数：
```bash
python simulator.py --lang en
```

---

## 调参策略说明

LLM 仍然采用标准的三阶段调参顺序：

### 阶段一：单独整定 P
- 保持 I 接近 0、D = 0，只调整 P
- 大步提升 P，快速找到响应速度满意且超调 < 5% 的 P 区间

### 阶段二：引入 I 消除稳态误差
- P 稳定后才开始加 I
- I 从小到大，直到稳态误差趋近于零
- 若加 I 后出现超调或振荡，说明 I 过大，需减小

### 阶段三：必要时引入 D 抑制超调
- 仅当 P+I 组合仍有明显超调或振荡时才引入 D
- D 从小值开始，D 过大会导致响应变慢（过度阻尼）
- 若 P+I 已满足要求，D 保持 0

> **注意**：每轮仿真时间序列中的 `SimTime(ms)` 是仿真时间（毫秒），不是真实世界时间。

---

## 常见问题

### Q：提示 `No module named matlab.engine`

说明 MATLAB Engine API 还没装，按第 1 步操作。注意要用安装项目依赖的**同一个 Python 环境**执行 `setup.py install`。

### Q：提示 `MATLAB 连接失败` 或启动超时

- 检查 MATLAB 是否已激活（License 有效）
- 检查 `MATLAB_MODEL_PATH` 路径是否正确，用正斜杠 `/` 或双反斜杠 `\\`
- 检查 `MATLAB_ROOT` 是否填写正确
- MATLAB Engine 首次启动较慢（30～60 秒），属正常现象

### Q：To Workspace 读不到数据

- 确认 To Workspace 模块的 Save format 设为 **`Timeseries`**
- 确认变量名与 `MATLAB_OUTPUT_SIGNAL` 完全一致
- 如果模型输出变量名不统一，可以用 `MATLAB_OUTPUT_SIGNAL_CANDIDATES` 提供候选名列表
- 确认模型在 MATLAB 里手动运行一次后工作区里有这个变量

### Q：我的模型不是单个标准 PID Controller，能调吗？

可以，按复杂度从低到高有三种方式：

1. **标准单控制器块**：只填 `MATLAB_PID_BLOCK_PATH`
2. **双控制器 / 多环**：再补 `MATLAB_PID_BLOCK_PATH_2`
3. **分离式 P/I/D 块**：改填 `MATLAB_P/I/D_BLOCK_PATH`（以及第二组 `_2` 字段）

如果你还不确定到底该填哪个，先从单控制器路径开始，跑通后再加复杂字段。

### Q：程序自动识别到了奇怪的块，怎么办？

自动识别只是兜底方案。遇到复杂模型时，建议：

- 先在 Simulink 里选中正确的块，然后用 `gcb` 拿到完整路径
- 显式填写 `MATLAB_PID_BLOCK_PATH`
- 显式填写 `MATLAB_SETPOINT_BLOCK`
- 如果是双控制器，再显式填写 `MATLAB_PID_BLOCK_PATH_2`

这样最稳。

### Q：每轮调参很慢

`MATLAB_SIM_STEP_TIME` 控制每轮仿真时长（仿真秒数）。适当减小可加快迭代速度，但需保证每轮能采集到足够反映系统响应（上升、稳态）的完整数据。一般建议设为系统响应时间常数的 3～5 倍。

### Q：调参结束后 PID 参数保存到哪里了？

程序在调参完成（或用户中断）时，会把当前最优 PID 参数写回 `.slx` 文件并保存。下次在 MATLAB 中打开该模型，控制器块中的参数即为调参结果。
