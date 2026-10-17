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
from copy import deepcopy
from pathlib import Path
from queue import Queue

from core.adapters import HardwareEnv, PythonSimEnv, SimulinkEnv
from core.buffer import AdvancedDataBuffer
from core.config import CONFIG
from core.i18n import get_language
from desktop.i18n import t
from core.tuning_engine import run_tuning_engine
from hw.bridge import SerialBridge
from llm.client import LLMTuner
from llm.usage import usage_summary
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
    controller_spec: dict | None = None
    local_search: bool = False

    @property
    def pid(self) -> dict[str, float]:
        return {key: getattr(self, key) for key in ("p", "i", "d")}

    def validate(self) -> None:
        if self.mode not in {"python", "hardware", "simulink"}:
            raise ValueError(t("invalid_mode"))
        if self.strategy not in {"offline", "ai"}:
            raise ValueError(t("invalid_strategy"))
        if self.controller_spec:
            from core.controller_formula import ControllerFormula
            from sim.formula_model import FormulaHeatingSimulator
            formula = ControllerFormula(self.controller_spec)
            if self.mode == "python":
                FormulaHeatingSimulator(formula)
            elif set(self.controller_spec.get("parameter_bindings", {})) != set(formula.parameters):
                raise ValueError("External controllers require parameter_bindings for every parameter")
        if not all(math.isfinite(v) for v in (*self.pid.values(), self.setpoint, self.speed)):
            raise ValueError(t("finite_values"))
        limits = get_pid_limits("python_sim" if self.mode == "python" else self.mode)
        if self.mode == "python" and any(not limits[k]["min"] <= v <= limits[k]["max"] for k, v in self.pid.items()):
            raise ValueError(t("unsafe_pid"))
        if not 1 <= self.rounds <= 500 or not 50 <= self.samples <= 5000:
            raise ValueError(t("invalid_counts"))
        if not 1 <= self.speed <= 50:
            raise ValueError(t("invalid_speed"))
        if self.mode == "hardware" and (not self.port.strip() or self.port.upper() == "AUTO"):
            raise ValueError(t("invalid_port"))
        if self.mode != "python" and self.strategy == "offline":
            raise ValueError(t("offline_only"))
        if self.mode == "hardware" and CONFIG.get("HARDWARE_PROFILE") == "mspm0_datavision":
            raise ValueError(t("readonly_profile"))
        if self.strategy == "ai":
            if not str(CONFIG.get("LLM_MODEL_NAME", "")).strip():
                raise ValueError(t("missing_model"))
            from llm.orcarouter import is_orcarouter_provider
            url = str(CONFIG.get("LLM_API_BASE_URL", ""))
            if not is_orcarouter_provider(CONFIG.get("LLM_PROVIDER", "")) and not url.startswith(("http://", "https://")):
                raise ValueError(t("missing_url"))


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

    def get_usage_summary(self, start_attempt=0):
        return usage_summary([])


class DesktopSession:
    def __init__(self, options: RunOptions):
        options.validate()
        self.options = options
        self.controller_spec = deepcopy(options.controller_spec)
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
                if self.controller_spec:
                    from core.controller_formula import ControllerFormula
                    from sim.formula_model import FormulaHeatingSimulator
                    sim = FormulaHeatingSimulator(ControllerFormula(self.controller_spec), setpoint=options.setpoint)
                else:
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
                    raise ValueError(issue or t("missing_simulink"))
                sim = create_simulink_bridge(settings)
                cleanup = sim.disconnect
                if self.controller_spec and all(isinstance(binding, dict) for binding in self.controller_spec.get("parameter_bindings", {}).values()):
                    sim.allow_custom_controller = True
                if self.controller_spec:
                    sim.extra_output_signals = {field: field for field in self.controller_spec.get("signal_bindings", {}).values()
                                                if field not in {"input", "setpoint", "error", "pwm", "timestamp", "p", "i", "d", "p2", "i2", "d2"}}
                sim.connect()
                env = SimulinkEnv(sim, options.setpoint, self.controller)
                if self.controller_spec:
                    env.single_run_per_round = True
                env.prompt_context = build_simulink_initial_prompt_context(sim, settings)
                llm_mode = "simulink"

            if self.controller_spec:
                from core.controller_formula import ControllerFormula
                if options.mode == "python":
                    env.controller_formula = sim.controller_formula
                else:
                    from core.formula_environment import BoundFormulaEnvironment
                    env = BoundFormulaEnvironment(env, ControllerFormula(self.controller_spec))
                env.formula_offline = options.strategy == "offline"
            env.local_search_enabled = options.mode == "python" and (options.local_search or bool(self.controller_spec) and options.strategy == "offline")

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
                tuner = _LocalizedTuner(tuner)
            result = run_tuning_engine(
                env, tuner, llm_mode, self.sink, self.controller,
                emit_console=False, start_time=start, current_stream_round=stream_round,
            )
            # Keep the legacy alias aligned with the engine's verified snapshot.
            result["secondary_pid"] = result["final_secondary_pid"]
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


class _LocalizedTuner:
    """Take the current language at each request, including after a live switch."""
    def __init__(self, tuner):
        self.tuner = tuner

    def analyze(self, *args, prompt_context=None, **kwargs):
        return self.tuner.analyze(*args, prompt_context={**(prompt_context or {}), "response_language": get_language()}, **kwargs)

    def get_usage_summary(self, start_attempt=0):
        return self.tuner.get_usage_summary(start_attempt=start_attempt)


class SessionArchive:
    """Spool complete numeric telemetry to disk; plots can keep a bounded window."""
    fields = ("round", "timestamp", "setpoint", "input", "pwm", "error", "p", "i", "d", "p2", "i2", "d2", "parameters", "signals")

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
        row = {**event, "round": self.round}
        if isinstance(row.get("parameters"), dict):
            row["parameters"] = json.dumps(row["parameters"], separators=(",", ":"))
        extra = {key: value for key, value in event.items() if key not in self.fields and key not in {"type", "seq"}
                 and isinstance(value, (int, float)) and not isinstance(value, bool)}
        if extra:
            row["signals"] = json.dumps(extra, separators=(",", ":"))
        self.writer.writerow(row)
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
