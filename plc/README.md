# Experimental PLC PID Adapter

该目录是独立的 PLC PID 调参原型。PLC 始终负责实时闭环；上位机只读取
PV/SP/CV/PID，并在明确授权后通过候选寄存器和 sequence/ack 握手写回参数。

当前范围：

- Modbus TCP 单回路；
- `parallel`（Kp/Ki/Kd）和 `ideal`（Kp/Ti/Td）参数转换；
- profile 驱动的寄存器类型、缩放、字节序和字序；
- `disabled`、`confirm`、`auto` 三种 PID 写回策略；
- Auto、TuneEnable、Interlock 三道 PLC 状态检查；
- PLC 数据质量为坏时拒绝把样本交给调参引擎；
- PLC ack、状态码和实际参数回读；
- 默认禁止写设定值；
- 暂未接入主启动器和正式 Release。

## 无硬件演示

演示使用内存 Modbus 客户端，不需要安装依赖或连接 PLC：

```powershell
C:\Users\Administrator\conda-envs\matlab310\python.exe -m plc.demo
```

演示会先证明未解锁写回会被拒绝，然后调用 `arm_writes_once()`，完成一次
候选 PID 写入、PLC ack 和实际值回读。

## 连接真实 Modbus TCP PLC

单独安装可选依赖，不影响项目现有运行环境：

```powershell
python -m pip install -r plc/requirements.txt
python -m plc --profile plc/example_profile.json --samples 5
```

`python -m plc` 当前只读，不会写任何 PLC 参数。复制
`example_profile.json` 后，按实际 PLC 地址、数据类型、缩放和字节序修改。
所有地址均使用 Modbus PDU 的零基偏移，不是文档中常见的 `40001` 表示法。

## PLC 侧握手约定

写回模式不是直接覆盖 PID 功能块，而是使用：

```text
candidate_kp / candidate_integral / candidate_derivative
apply_sequence
ack_sequence
apply_status
```

上位机先写三个候选参数，最后修改 `apply_sequence`。PLC 程序必须在
Auto、TuneEnable 和安全联锁均成立时校验范围、原子更新原生 PID 块，并把
状态码和实际 PID 更新完成，最后再把 `ack_sequence` 更新为相同序号。
`apply_status=0` 表示成功，非零由 PLC 项目自行定义。上位机收到 ack 后还会
重新读取实际 PID，数值不一致即判定失败。

## PID 参数语义

内部统一使用并联形式：

```text
u = Kp*e + Ki*integral(e) + Kd*de/dt
```

对于 PLC 常见的理想形式 `Kp/Ti/Td`：

```text
Ki = Kp / Ti
Kd = Kp * Td
```

必须在 profile 中明确秒或分钟。不能在未确认 PLC 功能块定义时直接写参数。

## 安全限制

该原型不能直接用于安全 PLC、SIL 回路、燃烧保护或毫秒级伺服控制。Modbus
TCP 本身没有认证和加密，应只在隔离的 OT 网络中使用。首个真实设备测试应
保持 `write_policy.mode=disabled`，确认读取、单位、方向和 PID 语义后，再切换
到需要人工一次性解锁的 `confirm`。
