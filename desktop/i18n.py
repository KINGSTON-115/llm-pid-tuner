"""Desktop copy, translated at render time so existing sessions can change language."""
from __future__ import annotations

import re
from core.i18n import get_language


# One entry owns both languages; identifiers and exported data remain language neutral.
TEXT = {
    "window": ("LLM PID Tuner · 桌面工作台", "LLM PID Tuner · Desktop workbench"),
    "eyebrow": ("控制实验室  /  实时工作台", "CONTROL LAB  /  LIVE WORKSPACE"),
    "title": ("让每一次调整，都清晰可见。", "See every adjustment unfold."),
    "subtitle": ("连接系统，观察响应，找到更好的 PID。", "Connect your system. Read the response. Refine your PID."),
    "workspace": ("工作空间", "WORKSPACE"),
    "overview": ("实时调参", "Live tuning"),
    "history": ("轮次记录", "Round history"),
    "preferences": ("偏好设置", "PREFERENCES"),
    "connections": ("连接设置", "Connections"),
    "appearance": ("外观", "Appearance"),
    "language": ("语言 / Language", "Language / 语言"),
    "light": ("白昼模式", "Light"), "dark": ("暗黑模式", "Dark"), "system": ("跟随系统", "System"),
    "local": ("本机运行", "RUNNING LOCALLY"),
    "native": ("原生桌面工作台", "Native desktop workbench"),
    "export": ("导出结果", "Export"), "csv": ("采样数据 · CSV", "Sample data · CSV"),
    "json": ("调参报告 · JSON", "Tuning report · JSON"),
    "pv": ("当前响应 / PV", "RESPONSE / PV"), "mae": ("平均绝对误差", "MEAN ABS. ERROR"),
    "overshoot": ("超调量", "OVERSHOOT"), "steady": ("稳态误差", "STEADY-STATE ERROR"),
    "waiting": ("等待采样", "Awaiting samples"), "round_hint": ("每轮完成后更新", "Updated after each round"),
    "overshoot_hint": ("相对当前目标值", "Relative to the setpoint"),
    "steady_hint": ("最后 20% 采样的平均误差", "Mean error of the final 20%"),
    "response": ("系统响应", "System response"), "actual": ("实际响应", "Response"), "target": ("目标值", "Setpoint"),
    "all_samples": ("全部采样", "All samples"), "last_60": ("最近 60 秒", "Last 60 s"), "last_120": ("最近 120 秒", "Last 120 s"),
    "samples": ("{count:,} 个采样点 · 已评估 {round} 轮", "{count:,} samples · {round} rounds evaluated"),
    "plot_limit": (" · 图中保留最近 6,000 点", " · Chart shows last 6,000"),
    "hover_hint": ("悬停查看数据 · 下方为控制输出", "Hover to inspect · Control output below"),
    "plot_accessible": ("实时响应与控制输出曲线", "Live response and control output chart"),
    "plot_empty": ("响应，即将在这里发生。\n开始一次实验，观察系统如何接近目标。", "Your response starts here.\nStart a run to see your system approach its target."),
    "analysis": ("调参分析", "Analysis"), "logs": ("运行日志", "Activity log"),
    "analysis_empty": ("从一次仿真实验开始\n\n设置初始 PID 与目标值，然后点击「开始调参」。\n离线规则无需 API Key；切换 AI 模式可查看模型分析。", "Start with a simulation\n\nSet your initial PID and setpoint, then select Start tuning.\nOffline rules need no API key. Choose AI tuning for model analysis."),
    "col_round": ("轮次", "Round"), "col_mae": ("平均误差", "Mean error"), "col_steady": ("稳态误差", "Steady error"),
    "col_overshoot": ("超调 %", "Overshoot %"), "col_status": ("系统状态", "System status"), "col_stable": ("连续稳定轮数", "Stable rounds"),
    "ready_hint": ("本地仿真已就绪。配置初始参数后即可开始。", "Simulation is ready. Set your initial parameters to begin."),
    "elapsed": ("本次实验 · {time}", "SESSION · {time}"),
    "config": ("实验配置", "Experiment"), "environment": ("运行环境", "Environment"),
    "python": ("本地热系统仿真", "Thermal simulation"), "hardware": ("串口硬件", "Serial hardware"),
    "strategy": ("调参策略", "Strategy"), "offline": ("离线规则体验", "Offline rules"), "ai": ("AI 自动调参", "AI tuning"),
    "port": ("串口", "Serial port"), "refresh": ("刷新", "Refresh"), "sp": ("目标值 SP", "Setpoint SP"),
    "apply_sp": ("应用运行时目标值", "Apply setpoint"), "gain_accessible": ("初始 K{gain}", "Initial K{gain}"),
    "current_pid": ("当前 PID · {pid}", "Current PID · {pid}"), "final_pid": ("最终 PID · {pid}", "Final PID · {pid}"),
    "controller2": ("控制器 2", "Controller 2"), "advanced": ("高级参数", "Advanced options"),
    "rounds": ("最大轮数", "Max. rounds"), "batch": ("每轮采样", "Samples / round"), "speed": ("仿真速度", "Sim. speed"),
    "start": ("开始调参", "Start tuning"), "pause": ("暂停", "Pause"), "resume": ("继续", "Resume"), "stop": ("停止", "Stop"),
    "offline_hint": ("本地热模型与保守规则。无需 API Key。", "Local thermal model and conservative rules. No API key needed."),
    "ai_hint": ("使用配置的 AI 模型：{model}", "Configured AI model: {model}"),
    "hardware_hint": ("读取设备当前 PID 和目标值。AI 会写回参数；停止仅结束上位机调参。", "Reads device PID and setpoint. AI writes updated gains; Stop ends host tuning only."),
    "simulink_hint": ("读取所选模型当前 PID，按仿真步返回曲线。", "Reads PID from the selected model. Samples arrive per simulation step."),
    "scan_error": ("串口扫描失败：{error}", "Serial scan failed: {error}"),
    "check_run": ("检查实验配置", "Check experiment settings"),
    "starting_offline": ("正在启动实验…\n离线规则体验 · 不调用 AI 模型。", "Starting experiment…\nOffline rules · No AI model calls."),
    "starting_ai": ("正在启动实验…\n等待采样完成后请求模型。", "Starting experiment…\nThe model will be requested after sampling."),
    "paused_hint": ("已暂停。模型请求可以完成，但参数将在继续后应用。", "Paused. An in-flight model request may finish; gains wait until you resume."),
    "resumed_hint": ("已继续运行。", "Run resumed."),
    "stopping_hint": ("正在释放连接；等待进行中的网络或 MATLAB 调用返回。", "Releasing connections; waiting for any in-flight network or MATLAB call."),
    "sp_requested": ("已请求目标值 {value:g}，在下一次采样时应用。", "Setpoint {value:g} requested; applies at the next sample."),
    "error_hint": ("SP {sp:g} · 误差 {error:.2f}", "SP {sp:g} · Error {error:.2f}"),
    "decision": ("第 {round} 轮 · {action}", "Round {round} · {action}"),
    "guardrails": ("安全护栏", "Safety guardrails"), "rollback_text": ("已回退到最佳参数。", "Reverted to the best PID."),
    "run_error": ("运行遇到问题\n\n{error}\n\n检查连接设置后可以重新开始。", "The run encountered a problem\n\n{error}\n\nCheck your connections, then start a new run."),
    "finished_hint": ("{reason}。可以导出数据，或开始新的实验。", "{reason}. Export your data or start a new experiment."),
    "export_title": ("导出本次实验", "Export this experiment"), "export_error": ("导出失败", "Export failed"),
    "exported": ("已导出：{path}", "Exported: {path}"),
    "save_error": ("无法保存", "Unable to save"), "preference_error": ("偏好设置未保存，已保留原设置。\n{error}", "Preferences could not be saved. Your previous settings were kept.\n{error}"),
    "ok": ("确定", "OK"), "save": ("保存设置", "Save settings"), "cancel": ("取消", "Cancel"), "browse": ("浏览…", "Browse…"),
    "settings_title": ("连接设置 · LLM PID Tuner", "Connections · LLM PID Tuner"),
    "settings_hint": ("配置保存在本机 config.json；API Key 遮掩显示。", "Saved locally in config.json. API keys are masked in the interface."),
    "ai_model": ("AI 模型", "AI model"), "protocol": ("接口协议", "Protocol"), "url": ("API 地址", "API URL"),
    "model": ("模型名称", "Model name"), "timeout": ("请求超时（秒）", "Timeout (s)"),
    "openai": ("OpenAI 兼容", "OpenAI compatible"), "anthropic": ("Claude 原生", "Claude native"),
    "openai_claude": ("Claude 中转", "Claude proxy"), "auto": ("自动识别", "Auto detect"),
    "provider_hint": ("支持 OpenAI 兼容服务、DeepSeek、Ollama 等。离线仿真无需配置模型。", "Supports OpenAI compatible services, DeepSeek, Ollama and more. Offline runs need no model."),
    "orca_hint": ("使用已有 OrcaRouter 配置。登录与模型选择沿用项目的 OrcaRouter 设置入口。", "Using existing OrcaRouter settings. Manage sign-in and models through the project's OrcaRouter settings entry."),
    "default_port": ("默认串口", "Default port"), "baud": ("波特率", "Baud rate"), "hw_protocol": ("硬件协议", "Hardware profile"),
    "generic_csv": ("通用串口 CSV", "Generic serial CSV"), "mspm0": ("MSPM0 DataVision（只读）", "MSPM0 DataVision (read only)"),
    "hw_settings_hint": ("连接时读取设备当前 PID。开始 AI 调参后，引擎会向设备写入调整后的参数。", "Reads current device PID on connection. Starting AI tuning allows the engine to write adjusted gains."),
    "model_file": ("模型文件", "Model file"), "pid_block": ("PID 模块路径", "PID block path"),
    "output_signal": ("输出信号", "Output signal"), "control_signal": ("控制信号", "Control signal"),
    "sp_block": ("目标值模块", "Setpoint block"), "matlab_root": ("MATLAB 安装路径", "MATLAB location"),
    "matlab_hint": ("需要本机 MATLAB Engine；双控制器等高级配置沿用 config.json。", "Requires MATLAB Engine. Advanced options, including dual controllers, use config.json."),
    "choose_model": ("选择 Simulink 模型", "Choose a Simulink model"),
    "check_settings": ("检查设置", "Check settings"), "invalid_url": ("API 地址需要以 http:// 或 https:// 开头。", "API URL must start with http:// or https://."),
    "invalid_config": ("配置文件必须是 JSON 对象。", "The configuration file must contain a JSON object."),
    "ready": ("准备就绪", "Ready"), "paused": ("已暂停", "Paused"), "stopping": ("正在停止", "Stopping"), "ended": ("已结束", "Finished"),
    "connecting": ("正在连接", "Connecting"), "connected": ("已连接", "Connected"), "collecting": ("正在采样", "Sampling"),
    "llm_request": ("分析响应", "Analyzing response"), "observing": ("稳定性观察", "Observing stability"),
    "observing_pending": ("继续优化", "Refining"), "fallback": ("使用保守规则", "Using fallback rules"),
    "rollback": ("回退到最佳参数", "Restoring best PID"), "completed": ("调参完成", "Tuning complete"),
    "stopped": ("已停止", "Stopped"), "error": ("运行出错", "Run failed"),
    "setpoint": ("目标值已更新", "Setpoint updated"), "setpoint_warning": ("目标值未生效", "Setpoint not applied"), "warning": ("运行提示", "Run notice"),
    "max_rounds_reached": ("已完成设定轮数", "Maximum rounds reached"), "stopped_by_user": ("已停止", "Stopped"),
    "stable_rounds_reached": ("已达到稳定标准", "Stability target reached"), "low_error_converged": ("低误差收敛", "Converged with low error"),
    "llm_marked_done": ("AI 已结束调参", "AI completed tuning"), "rollback_to_best": ("已回退到最佳参数", "Best PID restored"),
    "STABLE": ("稳定", "Stable"), "SLOW_RESPONSE": ("响应偏慢", "Slow response"), "OVERSHOOTING": ("存在超调", "Overshooting"), "OSCILLATING": ("存在振荡", "Oscillating"),
    "DAMP_OSCILLATION": ("抑制振荡", "Damp oscillation"), "REDUCE_OVERSHOOT": ("减少超调", "Reduce overshoot"),
    "BOOST_RESPONSE": ("加快响应", "Boost response"), "REDUCE_STEADY_ERROR": ("降低稳态误差", "Reduce steady error"),
    "FINE_TUNE": ("小步细调", "Fine tune"), "ADJUST_PID": ("调整 PID", "Adjust PID"), "UNKNOWN": ("未知", "Unknown"),
    "invalid_mode": ("请选择有效的运行环境。", "Choose a valid environment."),
    "invalid_strategy": ("请选择有效的调参策略。", "Choose a valid tuning strategy."),
    "finite_values": ("参数必须是有限数值。", "Parameters must be finite numbers."),
    "unsafe_pid": ("初始 PID 超出当前环境的安全范围，请检查参数。", "Initial PID exceeds this environment's safety limits. Check the gains."),
    "invalid_counts": ("轮数应为 1–500，每轮采样点应为 50–5000。", "Use 1–500 rounds and 50–5000 samples per round."),
    "invalid_speed": ("仿真速度应为 1–50 倍。", "Simulation speed must be between 1× and 50×."),
    "invalid_port": ("请选择具体串口，或使用 DEMO 虚拟串口。", "Choose a serial port, or use the DEMO virtual port."),
    "offline_only": ("离线规则体验仅用于本地热系统仿真；设备和 Simulink 请使用 AI 调参。", "Offline rules support thermal simulation only. Use AI tuning for hardware and Simulink."),
    "readonly_profile": ("MSPM0 DataVision 是只读协议，桌面调参暂不支持；请使用原有遥测入口。", "MSPM0 DataVision is read only and cannot be tuned here. Use the existing telemetry entry."),
    "missing_model": ("请在连接设置中填写模型名称。", "Enter a model name in Connections."),
    "missing_url": ("请填写有效的模型接口地址（http:// 或 https://）。", "Enter a valid model API URL (http:// or https://)."),
    "missing_simulink": ("请先在连接设置中选择 Simulink 模型。", "Choose a Simulink model in Connections first."),
}


def t(key: str, **values) -> str:
    pair = TEXT[key]
    return pair[0 if get_language() == "zh" else 1].format(**values)


def token(key: str) -> str:
    return t(key) if key in TEXT else key


class Bindings:
    """Small explicit binding registry; changing copy never resets widget state."""
    def __init__(self):
        self.items = []

    def bind(self, setter, key, **values):
        self.items.append((setter, key, values))
        setter(t(key, **values))

    def refresh(self):
        for setter, key, values in self.items:
            setter(t(key, **values))


# Exact built-in runtime messages. Arbitrary provider/model text stays verbatim.
RUNTIME = [
    ("正在准备调参环境…", "Preparing the tuning environment…"),
    ("已停止调参。", "Tuning stopped by user."),
    ("低误差收敛。", "Converged with low error."),
    ("已完成设定轮数。", "Reached maximum tuning rounds."),
    ("AI 已结束调参。", "LLM marked the tuning run as done."),
    ("未提供分析摘要。", "No analysis summary was provided."),
    ("  AI 正在分析…", "  LLM is thinking..."),
    ("Simulink 模式需要填写 MATLAB_OUTPUT_SIGNAL。", "MATLAB_OUTPUT_SIGNAL is required for Simulink mode."),
    ("MSPM0 DataVision 协议只读：尚未确认命令协议，已禁用 PID 写回。", "MSPM0 DataVision profile is read-only: PID write-back is disabled until a command protocol is confirmed."),
    ("MSPM0 DataVision 协议只读：尚未确认命令协议，已禁用目标值写回。", "MSPM0 DataVision profile is read-only: setpoint write-back is disabled until a command protocol is confirmed."),
    ("检测到震荡，保守降低 P/I 并增加 D。", "Oscillation detected; conservatively reduce P/I and increase D."),
    ("检测到超调，优先降低 P/I 并增加阻尼。", "Overshoot detected; reduce P/I and increase damping."),
    ("响应偏慢，适度增加 P，并在稳态误差偏大时增加 I。", "Slow response; moderately increase P and raise I if steady-state error is high."),
    ("系统基本稳定但仍有稳态误差，微增 I。", "Response is mostly stable with residual error; slightly increase I."),
    ("进入细调阶段，做小步修正。", "Fine-tuning with small adjustments."),
    ("连接清理失败，请检查设备状态。", "Connection cleanup failed. Check the device state."),
]
RUNTIME.extend(TEXT[key] for key in (
    "invalid_mode", "invalid_strategy", "finite_values", "unsafe_pid", "invalid_counts",
    "invalid_speed", "invalid_port", "offline_only", "readonly_profile", "missing_model",
    "missing_url", "missing_simulink", "invalid_config", "invalid_url",
))

# Templates match in either language, allowing already displayed history to retranslate.
RUNTIME_TEMPLATES = [
    ("第 {n} 轮", "round {n}"),
    ("Simulink 数值配置无效：{error}", "Invalid Simulink numeric configuration: {error}"),
    ("第 {n} 轮正在采样。", "Collecting data for round {n}."),
    ("正在分析第 {n} 轮响应。", "Requesting PID for round {n}."),
    ("已达到连续 {n} 轮稳定。", "Reached {n} stable rounds."),
    ("第 {n} 轮 AI 不可用；使用保守规则。", "LLM unavailable at round {n}; using fallback."),
    ("目标值已更改为 {value}。", "Setpoint changed to {value}."),
    ("第 {n} 轮记录了更好的 PID：{pid}", "Round {n} captured a new best PID: {pid}"),
    ("第 {n} 轮已达标（连续稳定 {stable}/{required} 轮）；保持 PID，继续观察。", "Round {n} met good-enough criteria ({stable}/{required} stable rounds); holding PID and observing the next round."),
    ("系统稳定但尚未达到目标，继续调参：{detail}。", "Stable but still tuning because good-enough criteria were not met: {detail}."),
    ("本轮响应较 {target} 退化，已自动回退至 {pid}。", "Automatic rollback triggered because this round regressed against {target}. Reverted to {pid}."),
    ("{gain} 已达上限 {value}，将不会继续升高", "{gain} reached its upper limit {value}; no further increase"),
    ("{gain} 增幅过大，已从 {before} 限制到 {after}", "{gain} increase limited from {before} to {after}"),
    ("{gain} 超出上限，已裁剪到 {value}", "{gain} clipped to its upper limit {value}"),
    ("{gain} 已从 {before} 调整到 {after}", "{gain} adjusted from {before} to {after}"),
    ("无法连接 {port}：{error}", "Cannot connect to {port}: {error}"),
    ("释放连接失败：{error}", "Connection cleanup failed: {error}"),
    ("硬件 PID 写入失败：{error}", "Failed to apply hardware PID: {error}"),
    ("主 PID 已应用，但控制器 2 更新失败：{error}", "Primary PID was applied, but controller 2 update failed: {error}"),
    ("硬件目标值写入失败：{error}", "Failed to apply hardware setpoint: {error}"),
    ("Simulink 目标值更新失败：{error}", "Failed to update Simulink setpoint: {error}"),
    ("{seconds} 秒内未收到串口数据。请检查串口、波特率和固件输出。", "No serial data was received within {seconds}s. Check serial port selection, baud rate, and firmware output."),
    ("{seconds} 秒内未解析到有效的硬件采样。期望格式：{detail}", "No valid hardware samples were parsed within {seconds}s. Expected {detail}"),
    ("硬件采样在 {seconds} 秒后超时：收到 {count}/{total} 个有效采样，少于最低要求 {minimum} 个。", "Hardware sampling timed out after {seconds}s: collected {count}/{total} valid samples, below minimum {minimum}."),
    ("硬件采样在 {seconds} 秒后超时：收到 {count}/{total} 个有效采样，已达到最低要求 {minimum} 个，继续运行。", "Hardware sampling timed out after {seconds}s: collected {count}/{total} valid samples; reached minimum {minimum}, proceeding."),
    ("[警告] 模型返回内容无法解析为 JSON；忽略本轮。", "[WARN] LLM response could not be parsed as JSON; ignoring this round."),
    ("[错误] 模型请求重试后仍失败：{error}", "[ERROR] LLM request failed after retries: {error}"),
    ("\n[警告] AI 请求失败：{error}。将在 {seconds} 秒后重试…", "\n[WARN] LLM call failed: {error}. Retrying in {seconds}s..."),
    ("\n[错误] AI 请求在尝试 {count} 次后仍失败：{error}", "\n[ERROR] LLM call failed after {count} attempts: {error}"),
    ("\n[警告] SDK 请求失败，改用 HTTP：{error}", "\n[WARN] SDK request failed, falling back to HTTP: {error}"),
    ("\n[AI 原始响应预览]\n{content}...\n", "\n[LLM raw response preview]\n{content}...\n"),
]


def _pattern(template):
    return re.compile("^" + re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>.+?)", re.escape(template)) + "$", re.DOTALL)


_MATCHERS = [(tuple(_pattern(v) for v in pair), pair) for pair in RUNTIME_TEMPLATES]


def runtime(message: str) -> str:
    """Translate only known application copy, never guess at external diagnostics."""
    index = 0 if get_language() == "zh" else 1
    for pair in RUNTIME:
        if message in pair:
            return pair[index]
    for translated in (("离线规则 · ", "Offline rules · "),
                       ("LLM 不可用，已启用保守兜底：", "AI unavailable; conservative fallback: ")):
        for source in translated:
            if message.startswith(source):
                return translated[index] + runtime(message[len(source):])
    for patterns, pair in _MATCHERS:
        for pattern in patterns:
            match = pattern.fullmatch(message)
            if match:
                values = match.groupdict()
                if "target" in values:
                    values["target"] = runtime(values["target"])
                return pair[index].format(**values)
    return message
