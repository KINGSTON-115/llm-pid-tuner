# 自定义控制公式与本地候选筛选（dev）

本功能属于开发分支，尚未进入稳定版。测试与验证统一使用
`C:/Users/Administrator/conda-envs/matlab310/python.exe`。

桌面工作台左侧的“自定义控制公式”可以导入 JSON、填入示例、识别参数依赖、输入信号、状态算子嵌套深度和计算层级。清空后保存会恢复标准 PID。
也可以先粘贴纯公式检查自由符号，再用 JSON 补全每个可调参数的初值、上下界和单轮最大变化量。程序不会猜测一个未声明的符号到底是传感器还是增益。

## 输入格式

完整示例见 [cascaded_thermal.json](../examples/controllers/cascaded_thermal.json)。

```json
{
  "equations": "outer = Kp * error + Ki * integral(error)\nu = lowpass(outer, tau)",
  "output": "u",
  "parameters": {
    "Kp": {"value": 1, "min": 0, "max": 100, "max_step": 1},
    "Ki": {"value": 0.1, "min": 0, "max": 10, "max_step": 0.1},
    "tau": {"value": 0.5, "min": 0.01, "max": 5, "max_step": 0.2}
  },
  "output_limits": [0, 255]
}
```

每行是一次命名赋值，可以前向引用其他计算层。未指定 `output` 时优先使用 `u`，否则使用最后一个赋值名。算术支持 `+ - * / **`，也接受 `^` 表示乘方。参数可以为负，但必须处于声明范围内。只调整声明的参数，未输出的参数保持原值；未知参数、非有限数值、非法动作会被拒绝，保持原参数。

支持 `abs/min/max/clip/sin/cos/tanh/sqrt`，以及以下独立状态算子：

| 算子 | 离散含义 |
| --- | --- |
| `integral(x)` | 矩形积分，每步累加 `x * dt`，初值 0 |
| `derivative(x)` | 后向差分，首次输出 0 |
| `delay(x)` / `delay(x, initial)` | 前一步的 x，默认初值 0 |
| `lowpass(x, tau)` | 一阶滤波，`alpha=dt/(tau+dt)`，首次输出 x，tau 必须大于 0 |

可嵌套状态算子实现高阶控制，也支持差分递推，例如：

```text
u = a1 * delay(u) + a2 * delay(delay(u)) + b0 * error + b1 * delay(error)
```

`delay` 将反馈边变为上一拍依赖，纯代数环会被拒绝。实际输出先限幅，再保存其反馈状态。积分器没有隐含的抗饱和机制，需要在公式中明确设计；状态在调参时保留，重新启动会话时重置。

当前执行语言为实数、离散时间、标量输出公式，不直接执行 Python/MATLAB 代码，也不直接解释 LaTeX、`s/z` 传递函数、矩阵或隐式微分方程。图中最多 2048 个语法节点，表达式和瞬时依赖深度最多 48，乘方指数绝对值最多 16；这些限制避免输入导致失控计算，并非固定三阶或固定两层。

## 环境与绑定

Python 热力仿真提供 `error/setpoint/input/dt/time`，直接运行公式，执行器范围限定在 `[0,255]`。其他未提供的信号会在启动前报错。

外部设备使用已实现的控制器，公式描述其结构；程序不会把公式生成并烧录成固件，也不会自动重建 Simulink 模型。`value` 是参数声明初值，外部调参从实际读回值开始，不在连接时覆盖设备参数。

串口协议当前只能写回 `p/i/d/p2/i2/d2`，例如：

```json
"parameter_bindings": {"Kp": "p", "Ki": "i", "Kd": "d"}
```

需要固件提供相应参数遥测。任意命名参数能参与结构分析；超出现有写回协议的参数不会被假装成已支持。

Simulink 支持将任意命名系数绑定到现有块的可读写标量数字字段，例如：

```json
"parameter_bindings": {"Kp": {"block": "your_model/OuterGain", "field": "Gain"}},
"signal_bindings": {"velocity": "velocity_out"}
```

见 [simulink_coefficients.json](../examples/controllers/simulink_coefficients.json)。信号需由模型记录在 SimulationOutput 或工作区；附加信号与主输出时间轴必须对齐，否则停止并报告缺失证据。系数为工作区表达式、矩阵或传递函数数组时，先在模型中暴露一个标量系数块。一次会话使用增益绑定或块字段绑定，不能混合。块写入会读回核对，写入失败会尝试恢复本次写入的字段并停止。

当前验收指标针对一条主要输出；多层公式中的其他信号作为调参证据，不代表已具备多输出联合验收。Simulink 运行与保存沿用现有桥接器的模型保存语义。

## 本地搜索与 AI

勾选“先在本地仿真筛选参数”后，内置热力模型会复制当前被控对象、控制器状态与随机数状态，用统一噪声序列比较候选。每轮最多 33 个候选，保留基准并过滤超过超调目标的结果。候选需比基准预测至少改善 5% 且绝对评分改善大于 0.05；预测永远不计入实测最佳记录。

AI 模式最多连续接受两次本地建议，之后恢复模型分析。被实测判定明显恶化的候选会被记住，并沿用实测回滚与收敛机制。自定义公式的离线模式只使用本地搜索，找不到可验证的改善时保持参数。硬件与 Simulink 没有自动启用热力模型预测。

标准 PID 提示词改为依据误差、超调、振荡和实际控制量选择参数，不再机械重置 I/D 或强制每个系统从 P 开始。公式模式发送实际依赖图、所需信号摘要、任意参数历史和边界，不套用 P/I/D 阶段。

API 返回的 usage 与本地预测分开统计。字符减少、减少调用、离线模型效果与真实 API 成本收益是不同指标；不能由一次内置模型实验推断所有控制器都更快收敛。

## 命令行

```powershell
$python310 = 'C:/Users/Administrator/conda-envs/matlab310/python.exe'
& $python310 formula_tuner.py examples/controllers/cascaded_thermal.json --inspect
& $python310 formula_tuner.py examples/controllers/cascaded_thermal.json --rounds 12 --json-out artifacts/formula-run.json
& $python310 formula_tuner.py my-controller.json --mode simulink --strategy ai --rounds 12
& $python310 benchmark.py --cases fallback local --rounds 24 --seed 42 --json-out artifacts/local-comparison.json
& $python310 -m pytest -q
# 需要本机 MATLAB/Simulink；创建并删除临时模型，不调用付费 AI 接口：
& $python310 tests/manual_formula_matlab.py
```

桌面“导出结果”里的 JSON 包含公式、最终参数、最后实测参数、验证标志、实测历史、决策来源与 API 用量；CSV 的 `parameters` 列保留每个采样对应的任意命名参数，`signals` 列保留其他数值信号（均为 JSON 对象）。公式会话以 `final_parameters_verified` 为准，兼容字段 `final_pid` 留空，不把默认 P/I/D 当作公式系数。
