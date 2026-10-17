"""Controller graphs, guarded writes, isolated previews and measured tuning."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from core.adapters import PythonSimEnv
from core.config import CONFIG, DEFAULT_CONFIG
from core.controller_formula import ControllerFormula
from core.formula_environment import BoundFormulaEnvironment
from core.local_search import rank_candidates
from core.tuning_engine import run_tuning_engine
from desktop.formula_editor import EXAMPLE
from desktop.session import DesktopSession, RunOptions
from sim.formula_model import FormulaHeatingSimulator
from sim.model import HeatingSimulator
from sim.runtime import drain_event_queue


def specification(equations="u = gain * error", **changes):
    return dict(equations=equations, parameters={"gain": dict(value=1., min=-10., max=10., max_step=1.)},
                output_limits=[0., 255.], **changes)


@pytest.fixture(autouse=True)
def config():
    with patch.dict(CONFIG, {**DEFAULT_CONFIG, "BUFFER_SIZE": 50, "MAX_TUNING_ROUNDS": 4}, clear=True):
        yield


def test_multilayer_graph_infers_parameters_signals_and_orders_forward_references():
    formula = ControllerFormula(specification("u = layer2 + gain * derivative(derivative(input))\nlayer2 = lowpass(layer1, 0.2)\nlayer1 = integral(integral(error))"))
    info = formula.describe()
    assert info["required_signals"] == ["error", "input"]
    assert info["evaluation_order"] == ["layer1", "layer2", "u"]
    assert info["layers"]["u"] == 3
    assert info["parameter_dependencies"]["u"] == ["gain"]
    assert info["stateful_operators"] == ["derivative", "integral", "lowpass"]
    assert info["state_operator_depths"]["u"]["integral"] == 2
    assert info["state_operator_depths"]["u"]["derivative"] == 2


def test_second_order_difference_equation_supports_delayed_feedback_and_cloned_state():
    formula = ControllerFormula(specification("u = 0.5 * delay(u) + 0.2 * delay(delay(u)) + gain * error"))
    runtime = formula.runtime()
    assert formula.describe()["delayed_feedback"]
    assert runtime.step({"error": 1.}, {"gain": 1.}, 1.) == 1.
    assert runtime.step({"error": 1.}, {"gain": 1.}, 1.) == 1.5
    assert runtime.step({"error": 1.}, {"gain": 1.}, 1.) == 1.95
    clone = deepcopy(runtime)
    assert clone.step({"error": 1.}, {"gain": 1.}, 1.) == runtime.step({"error": 1.}, {"gain": 1.}, 1.)


def test_delayed_feedback_reads_saturated_output_and_delayed_only_layers():
    formula = ControllerFormula(specification("u = delay(layer) + gain * error\nlayer = 0.5 * u"))
    runtime = formula.runtime()
    assert runtime.step({"error": 1000.}, {"gain": 1.}, 1.) == 255.
    assert runtime.step({"error": 0.}, {"gain": 1.}, 1.) == 127.5


@pytest.mark.parametrize("equations", [
    "import os", "u = __import__('os')", "u = error.real", "u = error[0]", "u = [error]",
    "u = (lambda: 1)()", "u = sum(error)", "u = integral(error, 1)", "u = gain\nu = gain",
    "u = a + gain\na = u", "error = gain\nu = error", "u = gain + True",
    "u = gain + (10 if error else 0)", "u = abs(x=error)", "u = gain + 'text'",
])
def test_unsafe_or_ambiguous_equations_are_rejected(equations):
    with pytest.raises(ValueError):
        ControllerFormula(specification(equations))


def test_unknown_signal_remains_explicit_and_is_not_guessed_as_a_parameter():
    formula = ControllerFormula(specification("u = gain * outer_error"))
    assert formula.required_signals == ["outer_error"]
    with pytest.raises(ValueError, match="cannot supply"):
        FormulaHeatingSimulator(formula)
    with pytest.raises(ValueError, match="Missing required signal"):
        formula.runtime().step({}, formula.initial_parameters, .1)


def test_nested_state_is_independent_and_clone_preserves_state_without_mutating_original():
    formula = ControllerFormula(specification("u = gain * integral(integral(error))"))
    runtime = formula.runtime()
    assert runtime.step({"error": 1.}, {"gain": 1.}, 1.) == 1.
    assert runtime.step({"error": 1.}, {"gain": 1.}, 1.) == 3.
    clone = deepcopy(runtime)
    assert clone.step({"error": 1.}, {"gain": 1.}, 1.) == 6.
    assert runtime.step({"error": 1.}, {"gain": 1.}, 1.) == 6.


def test_runtime_failure_does_not_commit_partial_integrator_state():
    formula = ControllerFormula(specification("u = gain * integral(error) / input"))
    runtime = formula.runtime()
    with pytest.raises(ValueError):
        runtime.step({"error": 2., "input": 0.}, {"gain": 1.}, 1.)
    assert not runtime.state
    assert runtime.step({"error": 2., "input": 1.}, {"gain": 1.}, 1.) == 2.


def test_delay_derivative_lowpass_and_negative_coefficients():
    formula = ControllerFormula(specification("u = gain * derivative(error) + delay(input, 2)", output="u"))
    runtime = formula.runtime()
    assert runtime.step({"error": 1., "input": 5.}, {"gain": -1.}, .5) == 2.
    assert runtime.step({"error": 2., "input": 7.}, {"gain": -1.}, .5) == 3.
    filtered = ControllerFormula(specification("u = gain * lowpass(input, 1)"))
    runtime = filtered.runtime()
    assert runtime.step({"input": 10.}, {"gain": 1.}, 1.) == 10.
    assert runtime.step({"input": 20.}, {"gain": 1.}, 1.) == 15.


def test_projection_keeps_bounds_step_and_unknown_names_are_rejected():
    formula = ControllerFormula(specification())
    projected, notes = formula.project({"gain": 0.}, {"gain": -100.})
    assert projected == {"gain": -1.} and notes
    with pytest.raises(ValueError, match="Unknown"):
        formula.project({"gain": 0.}, {"made_up": 1.})
    with pytest.raises(ValueError):
        formula.project({"gain": 0.}, {"gain": float("nan")})


def test_preview_clones_plant_rng_integrator_and_formula_state():
    sim = FormulaHeatingSimulator(ControllerFormula(deepcopy(EXAMPLE)))
    sim.set_setpoint(100.)
    env = PythonSimEnv(sim, 100.)
    env.collect_samples()
    state = (sim.temp, sim.timestamp, sim.rng.getstate(), dict(sim.formula_runtime.state), dict(sim.parameters))
    first = env.preview_parameters({**sim.parameters, "Kp": 2.})
    assert env.preview_parameters({**sim.parameters, "Kp": 2.}) == first
    assert state == (sim.temp, sim.timestamp, sim.rng.getstate(), dict(sim.formula_runtime.state), dict(sim.parameters))


def test_candidate_rank_rejects_overshoot_even_when_error_score_is_lower():
    def preview(params):
        x = params["gain"]
        return dict(avg_error=10 - x, steady_state_error=10 - x,
                    overshoot=2. if x > 1. else 0., status="STABLE")
    result = rank_candidates({"gain": 1.}, {"gain": dict(min=0., max=10., max_step=1.)}, preview)
    assert result["selected"] is None
    assert all(p["metrics"]["overshoot"] <= 1. for p in result["ranked"])


def test_candidate_forecasts_use_the_runtime_guardrailed_values():
    from core.local_search import pid_bounds
    from pid_safety import apply_pid_guardrails, get_pid_limits
    current = {"p": 1., "i": .1, "d": .05}
    captured = []
    def forecast(parameters):
        captured.append(parameters)
        return dict(avg_error=10., steady_state_error=10., overshoot=0., status="STABLE")
    with patch.dict(CONFIG, {"PID_MAX_INCREASE_RATIO": 1.1}):
        limits = get_pid_limits("python_sim")
        rank_candidates(current, pid_bounds(current, limits), forecast,
                        project=lambda candidate: apply_pid_guardrails(current, candidate, limits=limits)[0])
    assert len(captured) > 1
    assert all(p[key] <= current[key] * 1.1 + 1.e-10 for p in captured for key in current)


def formula_env():
    formula = ControllerFormula(specification())
    sim = FormulaHeatingSimulator(formula)
    sim.set_setpoint(100.)
    env = PythonSimEnv(sim, 100.)
    env.controller_formula = formula
    env.local_search_enabled = False
    return env


def test_model_named_parameters_are_measured_before_final_acceptance_and_usage_is_retained():
    env = formula_env()
    tuner = Mock()
    tuner.analyze.return_value = dict(parameters={"gain": 9.}, tuning_action="ADJUST_PARAMETERS", status="DONE")
    result = run_tuning_engine(env, tuner, "python_sim", max_rounds=3, emit_console=False, disable_early_exit=True)
    assert [r["parameters"]["gain"] for r in result["experiments"]] == [1., 2., 3.]
    assert result["final_parameters"] == {"gain": 3.}
    assert result["final_parameters_verified"] and not result["converged"]
    assert tuner.analyze.call_count == result["decision_requests"] == 2
    assert result["guardrail_count"] == 2
    context = tuner.analyze.call_args.kwargs["prompt_context"]
    assert context["controller_formula"]["parameter_dependencies"]["u"] == ["gain"]


def test_formula_observe_skips_one_call_and_does_not_apply_proposed_parameters():
    env = formula_env()
    tuner = Mock()
    tuner.analyze.return_value = dict(parameters={"gain": 8.}, tuning_action="OBSERVE")
    with patch.object(env, "apply_parameters", wraps=env.apply_parameters) as apply:
        result = run_tuning_engine(env, tuner, "python_sim", max_rounds=5, emit_console=False)
    assert tuner.analyze.call_count == 2
    apply.assert_not_called()
    assert result["final_parameters"] == {"gain": 1.}


def test_local_formula_tuning_executes_without_any_api_client_and_keeps_parameter_history():
    session = DesktopSession(RunOptions(controller_spec=deepcopy(EXAMPLE), rounds=3, samples=50, speed=50))
    with patch("desktop.session.LLMTuner", side_effect=AssertionError("No API")):
        session.start()
        session.thread.join(5)
    assert not session.running
    events = drain_event_queue(session.events)
    result = events[-1]["result"]
    assert result["completed_reason"] != "error", result
    assert result["final_parameters_verified"]
    assert result["rounds_completed"] == 3
    assert result["decision_requests"] == 0
    assert result["llm_usage"]["reported_tokens"]["input_tokens"] == 0
    samples = [e for e in events if e["type"] == "sample"]
    assert len(samples) == 150 and all("parameters" in sample for sample in samples)


def test_external_gain_binding_updates_the_intended_controller_only():
    env = Mock()
    env.get_current_pid.return_value = ({"p": 1., "i": .1, "d": .05}, {"p": 2., "i": .2, "d": .1})
    env.last_apply_issue = ""
    def apply(primary, secondary):
        env.get_current_pid.return_value = (primary, secondary)
    env.apply_pid.side_effect = apply
    bound = BoundFormulaEnvironment(env, ControllerFormula(specification(parameter_bindings={"gain": "p2"})))
    assert bound.get_parameters() == {"gain": 2.}
    bound.apply_parameters({"gain": 3.})
    primary, secondary = env.apply_pid.call_args.args
    assert primary["p"] == 1. and secondary["p"] == 3.


def test_simulink_block_binding_restores_previous_values_when_a_write_fails():
    values = {"model/A": "1", "model/B": "2"}
    def feval(method, block, field, *args, **kwargs):
        if method == "get_param": return values[block]
        if block == "model/B" and args[0] == "3": raise RuntimeError("write failed")
        values[block] = args[0]
    env = SimpleNamespace(bridge=SimpleNamespace(_eng=SimpleNamespace(feval=feval)))
    spec = specification("u = gain * error + other * input", parameter_bindings={
        "gain": {"block": "model/A", "field": "Gain"}, "other": {"block": "model/B", "field": "Gain"}})
    spec["parameters"]["other"] = dict(value=2., min=0., max=10., max_step=1.)
    bound = BoundFormulaEnvironment(env, ControllerFormula(spec))
    with pytest.raises(RuntimeError, match="restore failures=\\[\\]"):
        bound.apply_parameters({"gain": 2., "other": 3.})
    assert values == {"model/A": "1", "model/B": "2"}


def test_formula_prompt_replaces_pid_stage_assumptions():
    from llm.prompts import build_user_prompt, get_system_prompt
    formula = ControllerFormula(specification())
    context = dict(controller_formula=formula.describe(), current_parameters={"gain": 1.})
    system = get_system_prompt("simulink", context)
    user = build_user_prompt("samples", "history", "simulink", context)
    assert "do not assume PID stages" in system
    assert "parameters" in system and "ADJUST_PARAMETERS" in system
    assert "required_signals" in system and "gain" in user
    assert "required_signals" not in user


class MeasuredFormulaEnv:
    def __init__(self, outputs, *, setpoints=None):
        self.controller_formula = ControllerFormula(specification())
        self.parameters = {"gain": 1.}
        self.outputs = iter(outputs)
        self.setpoints = iter(setpoints or [100.] * len(outputs))
        self.applied = []
        self.local_search_enabled = False
        self.sample_callback = None

    def get_parameters(self): return dict(self.parameters)
    def apply_parameters(self, parameters):
        self.applied.append(dict(parameters))
        self.parameters = dict(parameters)
    def reset_buffer_state(self): pass
    def get_prompt_context(self): return {}
    def collect_samples(self):
        output, setpoint = next(self.outputs), next(self.setpoints)
        return [dict(timestamp=float(i), input=output, setpoint=setpoint, error=setpoint-output,
                     parameters=dict(self.parameters)) for i in range(50)]


def test_formula_regression_rolls_back_and_final_report_does_not_claim_unmeasured_gains():
    env = MeasuredFormulaEnv([95., 20.])
    tuner = Mock()
    tuner.analyze.return_value = dict(parameters={"gain": 2.}, tuning_action="ADJUST_PARAMETERS")
    result = run_tuning_engine(env, tuner, "python_sim", max_rounds=2, emit_console=False)
    assert env.applied == [{"gain": 2.}, {"gain": 1.}]
    assert result["final_parameters"] == {"gain": 1.}
    assert result["evaluated_parameters"] == {"gain": 2.}
    assert not result["final_parameters_verified"] and not result["final_metrics"]
    assert result["rollback_count"] == 1 and not result["converged"]


def test_formula_target_change_resets_verification_and_best_history():
    env = MeasuredFormulaEnv([100., 200., 200.], setpoints=[100., 200., 200.])
    tuner = Mock()
    with patch.dict(CONFIG, {"REQUIRED_STABLE_ROUNDS": 3}):
        result = run_tuning_engine(env, tuner, "python_sim", max_rounds=3, emit_console=False)
    assert not result["converged"]
    assert result["best_result"]["metrics"]["setpoint"] == 200.
    tuner.analyze.assert_not_called()


@pytest.mark.parametrize("suggestion", [
    {"parameters": {"unknown": 1.}, "tuning_action": "ADJUST_PARAMETERS"},
    {"parameters": {"gain": float("inf")}}, {"parameters": []},
    {"parameters": {"gain": 2.}, "tuning_action": "DO_SOMETHING"},
])
def test_bad_formula_suggestions_hold_instead_of_writing(suggestion):
    env = MeasuredFormulaEnv([60., 65.])
    tuner = Mock()
    tuner.analyze.return_value = suggestion
    result = run_tuning_engine(env, tuner, "python_sim", max_rounds=2, emit_console=False)
    assert not env.applied and result["final_parameters"] == {"gain": 1.}
    assert result["guardrail_count"] == 1


def test_formula_cancel_during_request_never_applies_a_parameter_update():
    from sim.runtime import SimulationController
    env, controller, tuner = MeasuredFormulaEnv([60., 65.]), SimulationController(), Mock()
    def analyze(*args, **kwargs):
        controller.request_stop()
        return dict(parameters={"gain": 2.})
    tuner.analyze.side_effect = analyze
    result = run_tuning_engine(env, tuner, "python_sim", controller=controller, max_rounds=2, emit_console=False)
    assert result["stopped"] and not env.applied
    assert result["rounds_completed"] == 1


def test_standard_pid_local_search_consults_model_after_two_local_steps_and_keeps_last_measurement():
    env = PythonSimEnv(HeatingSimulator(), 200.)
    env.sim.set_setpoint(200.)
    env.local_search_enabled = True
    def forecast(parameters):
        # A deterministic stand-in for a known preview model; increasing P improves it.
        return dict(avg_error=100. / parameters["p"], steady_state_error=100. / parameters["p"],
                    overshoot=0., status="STABLE")
    env.preview_parameters = forecast
    tuner = Mock()
    tuner.analyze.return_value = None
    result = run_tuning_engine(env, tuner, "python_sim", max_rounds=5, disable_early_exit=True, emit_console=False)
    assert result["local_decisions"] == 3
    assert tuner.analyze.call_count == result["decision_requests"] == 1
    assert result["final_pid_verified"]


def test_formula_usage_survives_real_response_parser_and_negative_named_parameters():
    from llm.response_parser import parse_json_response
    parsed = parse_json_response('{"parameters":{"gain":-2},"tuning_action":"ADJUST_PARAMETERS"}')
    assert parsed["parameters"] == {"gain": -2}


def test_final_parameter_read_failure_preserves_completed_experiments():
    env = MeasuredFormulaEnv([60.])
    reads = 0
    def get_parameters():
        nonlocal reads
        reads += 1
        if reads > 1:
            raise RuntimeError("lost device connection")
        return {"gain": 1.}
    env.get_parameters = get_parameters
    result = run_tuning_engine(env, Mock(), "python_sim", max_rounds=1, emit_console=False)
    assert result["completed_reason"] == "error"
    assert result["final_parameters"] is None
    assert result["rounds_completed"] == 1 and result["evaluated_parameters"] == {"gain": 1.}
    assert not result["final_parameters_verified"] and not result["converged"]


def test_named_coefficient_readback_mismatch_restores_successful_prior_writes():
    values = {"model/A": "1", "model/B": "2"}
    def feval(method, block, field, *args, **kwargs):
        if method == "get_param": return values[block]
        if block != "model/B": values[block] = args[0]  # B ignores the write.
    env = SimpleNamespace(bridge=SimpleNamespace(_eng=SimpleNamespace(feval=feval)))
    spec = specification("u = gain * error + other * input", parameter_bindings={
        "gain": {"block": "model/A", "field": "Gain"}, "other": {"block": "model/B", "field": "Gain"}})
    spec["parameters"]["other"] = dict(value=2., min=0., max=10., max_step=1.)
    bound = BoundFormulaEnvironment(env, ControllerFormula(spec))
    with pytest.raises(RuntimeError, match="read-back"):
        bound.apply_parameters({"gain": 2., "other": 3.})
    assert values == {"model/A": "1", "model/B": "2"}


def test_external_signal_alias_cannot_overwrite_measured_setpoint():
    env = Mock()
    spec = specification(parameter_bindings={"gain": "p"}, signal_bindings={"setpoint": "error"})
    with pytest.raises(ValueError, match="overwrite"):
        BoundFormulaEnvironment(env, ControllerFormula(spec))


def test_formula_path_uses_production_llm_prompt_parser_and_session_usage():
    from llm.client import LLMTuner
    from llm.providers import BaseLLMProvider
    from desktop.session import _LocalizedTuner
    captured = []
    class Provider(BaseLLMProvider):
        def execute_request(self, **kwargs):
            captured.append(kwargs["openai_msgs"])
            self.last_usage = {"input_tokens": 123, "output_tokens": 14}
            self.usage_final = True
            kwargs["on_chunk"]('{"parameters":{"gain":1.2},"tuning_action":"ADJUST_PARAMETERS","status":"DONE","analysis_summary":"Test measured evidence."}')
    provider = Provider("test", "https://test.invalid", "test", 5)
    with patch.object(LLMTuner, "_initialize_provider", return_value=provider):
        client = LLMTuner("test", "https://test.invalid", "test", emit_console=False)
    env = MeasuredFormulaEnv([60., 65.])
    result = run_tuning_engine(env, _LocalizedTuner(client), "python_sim", max_rounds=2, emit_console=False)
    assert result["final_parameters"] == {"gain": 1.2} and result["final_parameters_verified"]
    assert result["llm_usage"]["reported_tokens"]["input_tokens"] == 123
    assert result["llm_usage"]["usage_complete"]
    assert "Controller schema" in captured[0][0]["content"]
    assert "当前 PID" not in captured[0][1]["content"]
    assert not result["converged"]
