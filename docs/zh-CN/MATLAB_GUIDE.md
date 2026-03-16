# MATLAB/Simulink 仿真调参指南

本文档说明如何使用 llm-pid-tuner 对 MATLAB/Simulink 仿真模型进行 LLM 辅助 PID 调参。

---

## 这个模式适合什么场景

- 你已经在 Simulink 里搭好了被控对象模型，想快速找一组能用的 PID 初值
- 你的系统模型比较复杂（非线性、多环节、有时延），不好用 Ziegler-Nichols 这类经验公式直接估
- 你想在上真实硬件之前，先在仿真里把参数收敛到一个合理范围
- 你的硬件还没到货，但想提前跑通调参流程

## 它是怎么工作的

```text
Simulink 模型  ──运行一段时间──>  输出数据（To Workspace）
                                        │
                              AdvancedDataBuffer 计算指标
                                        │
                              LLM 分析 + 给出新 PID
                                        │
                         写回 Simulink PID Controller 模块
                                        │
                              继续下一轮仿真 ──> 收敛
```

护栏、回退、最佳参数记录这些逻辑与硬件模式完全一致，LLM 也看不出来数据来自仿真还是真实设备。

---

## 第 1 步：安装 MATLAB Engine API for Python

这一步只需要做一次。MATLAB R2021b 及以上版本自带这个包，去 MATLAB 安装目录下执行：

```bash
cd <MATLAB_ROOT>/extern/engines/python
python setup.py install
```

Windows 示例路径：
```
C:\Program Files\MATLAB\R2024a\extern\engines\python
```

安装完成后可以验证一下：
```bash
python -c "import matlab.engine; print('OK')"
```

---

## 第 2 步：准备你的 Simulink 模型

你的模型里需要有两个模块，其余结构随意：

**1. PID Controller 模块**

使用 Simulink 自带的标准 PID Controller 块即可。程序会通过模块路径直接写入 P / I / D 参数。

**2. To Workspace 模块**

把被控量（系统输出）连到一个 To Workspace 块：
- **变量名**：随意，比如 `y_out`，后面填到配置里
- **保存格式（Save format）**：必须设为 `Array`
- **采样时间**：建议和模型步长一致，或者填 `-1`（继承）

模型保存为 `.slx` 格式，记下文件完整路径备用。

---

## 第 3 步：配置 `config.json`

在原有 LLM 配置基础上，新增以下字段：

```json
{
  "LLM_API_KEY": "你的key",
  "LLM_API_BASE_URL": "https://api.openai.com/v1",
  "LLM_MODEL_NAME": "gpt-4",
  "LLM_PROVIDER": "openai",

  "MATLAB_MODEL_PATH"     : "C:/models/my_pid_model.slx",
  "MATLAB_PID_BLOCK_PATH" : "my_pid_model/PID Controller",
  "MATLAB_OUTPUT_SIGNAL"  : "y_out",
  "MATLAB_SIM_STEP_TIME"  : 10.0,
  "MATLAB_SETPOINT"       : 200.0
}
```

| 字段 | 说明 | 填写示例 |
| :--- | :--- | :--- |
| `MATLAB_MODEL_PATH` | Simulink `.slx` 文件完整路径 | `C:/models/my_model.slx` |
| `MATLAB_PID_BLOCK_PATH` | PID 模块在模型中的完整路径 | `my_model/PID Controller` |
| `MATLAB_OUTPUT_SIGNAL` | To Workspace 变量名 | `y_out` |
| `MATLAB_SIM_STEP_TIME` | 每轮调参运行的仿真时长（仿真秒数） | `10.0` |
| `MATLAB_SETPOINT` | 调参目标值，需与模型中 Setpoint 一致 | `200.0` |

`MATLAB_PID_BLOCK_PATH` 的格式是**模型名/模块名**，模型名就是 `.slx` 文件名去掉扩展名。如果 PID 块在子系统里，路径写成 `my_model/子系统名/PID Controller`。

---

## 第 4 步：运行

```bash
python simulator.py
```

`MATLAB_MODEL_PATH` 填了值，程序会自动切换到 MATLAB 模式，无需额外参数。没填则走原来的 Python 热系统仿真。

---

## 常见问题

**Q：提示 `No module named matlab.engine`**

说明 MATLAB Engine API 还没装，按第 1 步操作。注意要用安装项目依赖的**同一个 Python 环境**执行 `setup.py install`。

**Q：提示 `MATLAB 连接失败`**

- 检查 MATLAB 是否已激活（License 有效）
- 检查 `MATLAB_MODEL_PATH` 路径是否正确，用正斜杠 `/` 或双反斜杠 `\\`
- 检查 `MATLAB_PID_BLOCK_PATH` 是否与模型里的模块路径完全一致（大小写敏感）

**Q：To Workspace 读不到数据**

- 确认 To Workspace 模块的 Save format 设为 `Array` 而不是 `Structure` 或 `Timeseries`
- 确认变量名与 `MATLAB_OUTPUT_SIGNAL` 完全一致
- 确认模型运行后工作区里确实有这个变量（在 MATLAB 里手动跑一次验证）

**Q：每轮调参很慢**

`MATLAB_SIM_STEP_TIME` 控制每轮仿真时长。适当减小可以加快迭代速度，但要保证每轮采集的数据点数（`BUFFER_SIZE`）足够反映系统响应。
