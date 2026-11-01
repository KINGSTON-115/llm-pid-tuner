# 番外：PID Tuning Skill DLC

**v1.0.0 · 把调参经验交给你正在使用的 agent。**

[下载独立 DLC](https://github.com/KINGSTON-115/llm-pid-tuner/releases/tag/skill-pid-tuning-v1.0.0) · [English](../en-US/SKILL_DLC.md) · [查看 skill 源码](https://github.com/KINGSTON-115/llm-pid-tuner/tree/skill-pid-tuning-v1.0.0/skills/pid-tuning)

主项目提供完整桌面工作台，这个番外则把最有价值的方法提炼成一个可复制的
`pid-tuning/` 文件夹：理解控制器公式 → 测量基线 → 有界调整 → 验证 → 保留或回退。
agent 负责推理，随包脚本负责可重复的计算。无需安装主程序、申请额外模型 API Key，
也不绑定某个模型供应商。说明可直接阅读，脚本只需要 Python 3.10+ 标准库。

## 保留与取舍

| 保留的精华 | 在 DLC 中的形式 |
| --- | --- |
| 循证调参、避免盲目重置 I/D | skill 工作流与实验记录 |
| 参数限幅、单轮步长与回退 | 拒绝不合规建议的检查器，以及验证/回退流程 |
| 实际公式、离散化和多回路语义 | 按需加载的参考文档 |
| 逐轮测量、预测与实测分离 | 分段 CSV 指标与明确的结果来源 |
| 达标后保持、预算耗尽停止 | 可迁移的验收方法 |

桌面/TUI、模型 API 客户端、串口驱动、MATLAB Engine 和 PLC 协议继续由主项目或
用户现有工具负责。DLC 不复制整套应用，不硬编码本机路径、端口或某个设备的参数上限。
脚本只做离线计算；连接设备时由 agent 使用现有且已授权的适配器。

## 安装和迁移

1. 下载 `pid-tuning-skill-v1.0.0.zip` 并解压，得到 `pid-tuning/`。
2. 将**整个文件夹**放入你的 agent 配置的 skills 目录，确保目录内直接包含 `SKILL.md`。
   例如本项目开发环境的 Codex 使用 `~/.codex/skills/`；实际扫描目录以宿主配置及
   [Codex 官方说明](https://developers.openai.com/codex/skills/)为准。
3. 在宿主中重新加载 skills 或新建会话，确认能发现 `pid-tuning`。
   支持 `$skill-name` 调用的宿主可输入 `$pid-tuning`。
4. 对于尚不支持 skills 自动发现的 agent，直接让它读取
   `你的路径/pid-tuning/SKILL.md` 并按需读取引用文档，也可使用同一套工具。

`.skill` 下载项与 ZIP 内容相同，供支持该格式导入的宿主使用，不保证每个产品都能直接导入。
迁移时保留相对目录结构即可。`agents/openai.yaml` 是可选的宿主展示元数据；其他 agent
可忽略它。更新已有安装前先保存自己的修改。`SHA256SUMS` 提供下载校验值。

## 三种直接可用的请求

```text
使用 $pid-tuning 分析这份温控 CSV。先确认参数定义和数据质量，
比较各轮超调和尾部误差，给出下一轮的小步实验，不连接硬件。

使用 $pid-tuning 检查这个 Simulink 控制器的公式、采样周期和参数单位，
用我的模型对候选参数做仿真，保留已验证的最佳结果。

读取 ./pid-tuning/SKILL.md，用随包示例演示一次基线、参数修改和复测，
明确区分演示模型成绩和真实设备效果。
```

## 两分钟离线体验

在解压目录运行（Windows 可将 `python` 换成实际 Python 路径）：

```sh
python pid-tuning/scripts/pid_lab.py simulate --config pid-tuning/assets/thermal-demo.json --csv baseline.csv
python pid-tuning/scripts/pid_lab.py analyze --csv baseline.csv --control-limits 0 100
```

输出为 JSON；CSV 不会覆盖已有文件。把示例配置复制到当前工作目录，修改一个增益，再用
新的 CSV 文件名仿真即可比较。示例是可重复的一阶模型，不代表你的设备。
主程序导出的稀疏 CSV（轮次、目标值只在变化时填写）也可直接分析；不同轮次、参数、
目标值和时间重置会分段统计。具体指标、数据要求及 `check-update` 文件格式见
[工具说明](https://github.com/KINGSTON-115/llm-pid-tuner/blob/skill-pid-tuning-v1.0.0/skills/pid-tuning/references/telemetry.md)。

验收标准由实际任务决定。窗口内误差小、参数在限幅内，都不能单独证明系统安全或收敛。
控制器的积分/微分单位、采样周期和输出缩放需要先确认；设备本身的保护机制仍然必需。

## 独立发布与维护

DLC 采用 `skill-pid-tuning-v*` 标签，独立于桌面版 `v2.*`，也不会取代桌面版的 Latest 下载。
当前版本包含 skill、三份参考文档、一个离线工具、演示配置、宿主元数据与 MIT License。
本体工作区的配置和密钥不进入发布包。MIT 授权与主项目一致，保留作者声明。

维护者先检出 `skill-pid-tuning-v1.0.0` 标签或包含 DLC 源码的 `dev` 分支，
再在仓库根目录使用项目指定解释器构建：

```powershell
& 'C:/Users/Administrator/conda-envs/matlab310/python.exe' -m pytest -q tests/test_skill_dlc.py
& 'C:/Users/Administrator/conda-envs/matlab310/python.exe' scripts/build_skill_dlc.py
```

打包器读取 `dlc/pid-tuning.json` 白名单，检查 skill 版本一致性，生成确定性 ZIP、`.skill`
及 SHA-256 校验文件到 `dist/skill-dlc/`。修改 skill 时同时更新版本和白名单；
发布前运行完整项目测试，并把解压后的文件夹放到仓库外验证。
