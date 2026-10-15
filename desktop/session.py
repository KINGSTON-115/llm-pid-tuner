"""Desktop session orchestration; all device and model work stays off the UI thread."""
from __future__ import annotations

import csv
import json
import math
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Queue

from core.adapters import HardwareEnv, PythonSimEnv, SimulinkEnv
from core.buffer import AdvancedDataBuffer
from core.config import CONFIG
from core.tuning_engine import run_tuning_engine
from hw.bridge import SerialBridge
from llm.client import LLMTuner
from pid_safety import build_fallback_suggestion, get_pid_limits
from sim.model import CONTROL_INTERVAL, HeatingSimulator
from sim.prompt_context import build_hardware_prompt_context, build_python_sim_prompt_context
from sim.runtime import QueueEventSink, SimulationController, make_llm_tuner_callbacks, publish_event
from sim.simulink_setup import (
    build_simulink_initial_prompt_context, create_simulink_bridge,
    load_simulink_runtime_config, validate_simulink_runtime_config,
)


@dataclass(frozen=True)
class RunOptions:
    mode: str = "python"
    strategy: str = "offline"
    setpoint: float = 200.0
    p: float = 1.0
    i: float = 0.1
    d: float = 0.05
    rounds: int = 12
    samples: int = 100
    speed: float = 10.0
    port: str = "DEMO"

    @property
    def pid(self) -> dict[str, float]:
        return {key: getattr(self, key) for key in ("p", "i", "d")}

    def validate(self) -> None:
        if self.mode not in {"python", "hardware", "simulink"}:
            raise ValueError("请选择有效的运行环境。")
        if self.strategy not in {"offline", "ai"}:
            raise ValueError("请选择有效的调参策略。")
        if not all(math.isfinite(v) for v in (*self.pid.values(), self.setpoint, self.speed)):
            raise ValueError("参数必须是有限数值。")
        limits = get_pid_limits("python_sim" if self.mode == "python" else self.mode)
        if self.mode == "python" and any(not limits[k]["min"] <= v <= limits[k]["max"] for k, v in self.pid.items()):
            raise ValueError("初始 PID 超出当前环境的安全范围，请检查参数。")
        if not 1 <= self.rounds <= 500 or not 50 <= self.samples <= 5000:
            raise ValueError("轮数应为 1–500，每轮采样点应为 50–5000。")
        if not 1 <= self.speed <= 50:
            raise ValueError("仿真速度应为 1–50 倍。")
        if self.mode == "hardware" and (not self.port.strip() or self.port.upper() == "AUTO"):
            raise ValueError("请选择具体串口，或使用 DEMO 虚拟串口。")
        if self.mode != "python" and self.strategy == "offline":
            raise ValueError("离线规则体验仅用于本地热系统仿真；设备和 Simulink 请使用 AI 调参。")
        if self.mode == "hardware" and CONFIG.get("HARDWARE_PROFILE") == "mspm0_datavision":
            raise ValueError("MSPM0 DataVision 是只读协议，桌面调参暂不支持；请使用原有遥测入口。")
        if self.strategy == "ai":
            if not str(CONFIG.get("LLM_MODEL_NAME", "")).strip():
                raise ValueError("请在连接设置中填写模型名称。")
            from llm.orcarouter import is_orcarouter_provider
            url = str(CONFIG.get("LLM_API_BASE_URL", ""))
            if not is_orcarouter_provider(CONFIG.get("LLM_PROVIDER", "")) and not url.startswith(("http://", "https://")):
                raise ValueError("请填写有效的模型接口地址（http:// 或 https://）。")


class OfflineTuner:
    """Use the existing conservative rules without creating an HTTP client."""
    def __init__(self, buffer: AdvancedDataBuffer):
        self.buffer = buffer

    def analyze(self, *_args, **_kwargs):
        result = build_fallback_suggestion(
            self.buffer.current_pid, self.buffer.calculate_advanced_metrics(),
            limits=get_pid_limits("python_sim"),
        )
        result["analysis_summary"] = result["analysis_summary"].replace(
            "LLM 不可用，已启用保守兜底：", "离线规则 · "
        )
        result["thought_process"] = "本次使用项目内置的保守控制规则，未调用 AI。"
        return result


class DesktopSession:
    def __init__(self, options: RunOptions):
        options.validate()
        self.options = options
        self.controller = SimulationController()
        self.events: Queue = Queue()
        self.sink = QueueEventSink(self.events)
        self.thread = threading.Thread(target=self._run, name="desktop-tuning", daemon=False)

    @property
    def running(self) -> bool:
        return self.thread.is_alive()

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        old_config = {key: CONFIG[key] for key in ("MAX_TUNING_ROUNDS", "BUFFER_SIZE")}
        env = None
        result = None
        cleanup = None
        try:
            CONFIG.update(MAX_TUNING_ROUNDS=self.options.rounds, BUFFER_SIZE=self.options.samples)
            options = self.options
            self.sink.publish("lifecycle", phase="connecting", message="正在准备调参环境…")
            if options.mode == "python":
                sim = HeatingSimulator(**dict(zip(("kp", "ki", "kd"), options.pid.values())), setpoint=options.setpoint)
                sim.set_setpoint(options.setpoint)
                env = PythonSimEnv(sim, options.setpoint, self.controller)
                env.prompt_context = build_python_sim_prompt_context()
                llm_mode = "python_sim"
            elif options.mode == "hardware":
                bridge = SerialBridge(options.port, int(CONFIG["BAUD_RATE"]), emit_console=False)
                bridge.hardware_profile = CONFIG.get("HARDWARE_PROFILE", "generic_serial_csv")
                cleanup = bridge.disconnect
                if not bridge.connect():
                    raise RuntimeError(f"无法连接 {options.port}：{bridge.last_error}")
                # Hardware PID and setpoint are read from telemetry, never overwritten at connect.
                env = HardwareEnv(bridge, {"p": 0., "i": 0., "d": 0.}, self.controller)
                bridge.send_profile_command("STATUS")
                env.prompt_context = build_hardware_prompt_context(options.port, hardware_profile=bridge.hardware_profile)
                llm_mode = "generic"
            else:
                settings = load_simulink_runtime_config({**CONFIG, "MATLAB_SETPOINT": options.setpoint})
                issue = validate_simulink_runtime_config(settings)
                if issue or not settings.model_path:
                    raise ValueError(issue or "请先在连接设置中选择 Simulink 模型。")
                sim = create_simulink_bridge(settings)
                cleanup = sim.disconnect
                sim.connect()
                env = SimulinkEnv(sim, options.setpoint, self.controller)
                env.prompt_context = build_simulink_initial_prompt_context(sim, settings)
                llm_mode = "simulink"

            buffer = AdvancedDataBuffer(options.samples)

            def on_sample(sample):
                buffer.add(sample)
                publish_event(self.sink, "sample", **sample)
                if options.mode == "python":
                    self.controller.stop_event.wait(CONTROL_INTERVAL / options.speed)

            env.sample_callback = on_sample
            start = time.time()
            stream_round = [0]
            if options.strategy == "offline":
                tuner = OfflineTuner(buffer)
            else:
                log_cb, stream_cb = make_llm_tuner_callbacks(self.sink, start, stream_round)
                tuner = LLMTuner(
                    CONFIG["LLM_API_KEY"], CONFIG["LLM_API_BASE_URL"],
                    CONFIG["LLM_MODEL_NAME"], CONFIG["LLM_PROVIDER"],
                    log_callback=log_cb, stream_callback=stream_cb, emit_console=False,
                    abort_check=lambda: self.controller.should_stop,
                    timeout=CONFIG.get("LLM_REQUEST_TIMEOUT", 60),
                )
            result = run_tuning_engine(
                env, tuner, llm_mode, self.sink, self.controller,
                emit_console=False, start_time=start, current_stream_round=stream_round,
            )
            # Report the device's actual PID, including early-DONE paths in the shared engine.
            result["final_pid"], result["secondary_pid"] = env.get_current_pid()
            result["strategy"] = options.strategy
            if options.strategy == "offline":
                result.update(provider="offline", model="conservative_rules")
        except Exception as exc:
            message = str(exc)
            for field in ("LLM_API_KEY", "ORCAROUTER_API_KEY"):
                key = str(CONFIG.get(field, ""))
                if key:
                    message = message.replace(key, "••••")
            self.sink.publish("lifecycle", phase="error", message=message)
            result = {"completed_reason": "error", "error": message}
        finally:
            try:
                if env is not None:
                    env.shutdown()
                elif cleanup is not None:
                    cleanup()
            except Exception as exc:
                self.sink.publish("lifecycle", phase="error", message=f"释放连接失败：{type(exc).__name__}")
                result = {**(result or {}), "completed_reason": "error", "error": "连接清理失败，请检查设备状态。"}
            CONFIG.update(old_config)
            self.sink.publish("session_finished", result=result or {})


class SessionArchive:
    """Spool complete numeric telemetry to disk; plots can keep a bounded window."""
    fields = ("round", "timestamp", "setpoint", "input", "pwm", "error", "p", "i", "d", "p2", "i2", "d2")

    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pid-session-")
        self.path = Path(self.directory.name) / "samples.csv"
        self.handle = self.path.open("w", encoding="utf-8-sig", newline="")
        self.writer = csv.DictWriter(self.handle, self.fields, extrasaction="ignore")
        self.writer.writeheader()
        self.count = 0
        self.round = 1
        self.metrics: list[dict] = []
        self.result: dict = {}

    def sample(self, event: dict) -> None:
        self.writer.writerow({**event, "round": self.round})
        self.count += 1

    def export_csv(self, path: str) -> None:
        self.handle.flush()
        shutil.copyfile(self.path, path)

    def export_json(self, path: str) -> None:
        Path(path).write_text(json.dumps({"sample_count": self.count, "rounds": self.metrics,
                                        "result": self.result}, ensure_ascii=False, indent=2), encoding="utf-8")

    def close(self) -> None:
        self.handle.close()
        self.directory.cleanup()
