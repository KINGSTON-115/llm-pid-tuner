"""Real temporary Simulink model: named coefficient write, extra signal, measured engine."""
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.adapters import SimulinkEnv
from core.config import CONFIG, initialize_runtime_config
from core.controller_formula import ControllerFormula
from core.formula_environment import BoundFormulaEnvironment
from core.tuning_engine import run_tuning_engine
from sim.simulink_bridge import SimulinkBridge, _load_matlab_engine

initialize_runtime_config(verbose=False)
CONFIG.update(BUFFER_SIZE=50, CSV_EXPORT_PATH="")
print("Starting real MATLAB Engine with", sys.executable, flush=True)
engine = _load_matlab_engine(CONFIG.get("MATLAB_ROOT", "")).start_matlab()
model = "codex_formula_smoke_" + str(int(time.time()))
bridge = None
try:
    with tempfile.TemporaryDirectory(prefix="formula-matlab-") as directory:
        path = str(Path(directory) / (model + ".slx"))
        engine.new_system(model, nargout=0)
        for block, library, props in [
            ("Reference", "simulink/Sources/Constant", {"Value": "1"}),
            ("Error", "simulink/Math Operations/Sum", {"Inputs": "+-"}),
            ("OuterGain", "simulink/Math Operations/Gain", {"Gain": "1"}),
            ("Plant", "simulink/Continuous/Transfer Fcn", {"Numerator": "[1]", "Denominator": "[1 2 1]"}),
            ("Output", "simulink/Sinks/To Workspace", {"VariableName": "y_out", "SaveFormat": "Timeseries"}),
            ("Control", "simulink/Sinks/To Workspace", {"VariableName": "u_out", "SaveFormat": "Timeseries"}),
            ("Velocity", "simulink/Continuous/Derivative", {}),
            ("VelocityLog", "simulink/Sinks/To Workspace", {"VariableName": "velocity_out", "SaveFormat": "Timeseries"}),
        ]:
            arguments = [item for pair in props.items() for item in pair]
            engine.add_block(library, model + "/" + block, *arguments, nargout=0)
        for source, destination in [("Reference/1", "Error/1"), ("Error/1", "OuterGain/1"),
            ("OuterGain/1", "Plant/1"), ("OuterGain/1", "Control/1"),
            ("Plant/1", "Error/2"), ("Plant/1", "Output/1"),
            ("Plant/1", "Velocity/1"), ("Velocity/1", "VelocityLog/1")]:
            engine.add_line(model, source, destination, "autorouting", "on", nargout=0)
        engine.set_param(model, "SolverType", "Fixed-step", "Solver", "ode4", "FixedStep", "0.02", nargout=0)
        engine.save_system(model, path, nargout=0)
        engine.close_system(model, 0, nargout=0)
        bridge = SimulinkBridge(path, 1., "", "y_out", matlab_root=CONFIG.get("MATLAB_ROOT", ""),
                                sim_step_time=2., control_signal="u_out", setpoint_block=model + "/Reference")
        bridge.allow_custom_controller = True
        bridge.extra_output_signals = {"velocity_out": "velocity_out"}
        bridge._matlab_engine = type("BorrowEngine", (), {"start_matlab": lambda _: engine})()
        bridge.connect()
        spec = dict(equations="outer = gain * error\nu = outer + 0 * velocity", parameters={"gain": dict(value=1., min=.1, max=5., max_step=.2)},
                    parameter_bindings={"gain": dict(block=model + "/OuterGain", field="Gain")},
                    signal_bindings={"velocity": "velocity_out"}, output_limits=[-10., 10.])
        env = BoundFormulaEnvironment(SimulinkEnv(bridge, 1.), ControllerFormula(spec))
        tuner = Mock()
        tuner.analyze.return_value = dict(parameters={"gain": 1.2}, tuning_action="ADJUST_PARAMETERS")
        result = run_tuning_engine(env, tuner, "simulink", max_rounds=2, disable_early_exit=True, emit_console=False)
        assert result["completed_reason"] != "error", result
        assert result["final_parameters"] == {"gain": 1.2}
        assert result["evaluated_parameters"] == {"gain": 1.2}
        assert result["final_parameters_verified"]
        assert len(result["experiments"]) == 2
        assert "velocity" in tuner.analyze.call_args.kwargs["prompt_context"]["signal_evidence"]
        result["validation_interpreter"] = sys.executable
        (ROOT / "artifacts/tuning-formula-real-matlab.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        bridge.disconnect()
        bridge = None
        print("Real Simulink formula binding and measured tuning passed", flush=True)
finally:
    if bridge is not None:
        bridge.disconnect()
    else:
        try:
            engine.quit()
        except Exception:
            pass
