"""Tune arbitrary named coefficients with measured acceptance and bounded search."""
from __future__ import annotations

from copy import deepcopy
import json
import math
import time
from types import SimpleNamespace

from core.buffer import AdvancedDataBuffer
from core.config import CONFIG
from core.env import controller_requests_abort
from core.local_search import rank_candidates, compact_predictions
from core.tuning_loop import publish_round_metrics
from pid_safety import is_good_enough, score_metrics, should_rollback_to_best
from sim.runtime import publish_event


def run_formula_tuning(env, tuner, mode, event_sink=None, controller=None,
                       max_rounds=None, disable_early_exit=False, start_time=0., current_stream_round=None):
    from core.tuning_engine import _tuner_usage
    formula = env.controller_formula
    max_rounds = int(CONFIG["MAX_TUNING_ROUNDS"] if max_rounds is None else max_rounds)
    if max_rounds < 0:
        raise ValueError("max_rounds must be non-negative")
    started = start_time or time.time()
    usage_start = _tuner_usage(tuner)["transport_attempts"]
    rules = dict(avg_error_threshold=CONFIG["GOOD_ENOUGH_AVG_ERROR"],
                 steady_state_error_threshold=CONFIG["GOOD_ENOUGH_STEADY_STATE_ERROR"],
                 overshoot_threshold=CONFIG["GOOD_ENOUGH_OVERSHOOT"],
                 require_avg_error_threshold=mode != "simulink")
    experiments, decisions, rejected = [], [], set()
    best, previous, observe = None, None, None
    stable, local_streak, local_count, calls, rollbacks, guards, failures = 0, 0, 0, 0, 0, 0, 0
    reason, error = "max_rounds_reached", ""
    last_parameters, last_metrics = None, {}

    def signature(parameters):
        return tuple(sorted(parameters.items()))

    def lifecycle(phase, message):
        publish_event(event_sink, "lifecycle", phase=phase, message=message)

    for round_index in range(1, max_rounds + 1):
        if controller_requests_abort(controller):
            reason = "stopped_by_user"
            break
        lifecycle("collecting", f"Collecting data for round {round_index}.")
        try:
            env.reset_buffer_state()
            samples = env.collect_samples()
            if controller_requests_abort(controller):
                reason = "stopped_by_user"
                break
            if not samples:
                raise ValueError(getattr(env, "last_collect_issue", "") or "No controller measurements received")
            parameters = env.get_parameters()
            for name, value in parameters.items():
                spec = formula.parameters[name]
                if not math.isfinite(value) or not spec.minimum <= value <= spec.maximum:
                    raise ValueError(f"Measured parameter {name} is outside declared bounds")
            buffer = AdvancedDataBuffer(len(samples))
            for index, sample in enumerate(samples):
                if any(key not in sample or not math.isfinite(float(sample[key])) for key in ("timestamp", "setpoint", "input")):
                    raise ValueError("Invalid controller measurement")
                if sample.get("parameters", parameters) != parameters:
                    raise ValueError("Controller parameters changed during measurement")
                if index and sample["timestamp"] <= samples[index - 1]["timestamp"]:
                    raise ValueError("A controller measurement must have a strictly increasing time axis")
                if "time" in formula.required_signals:
                    sample["time"] = sample["timestamp"] / 1000.
                if "dt" in formula.required_signals:
                    if len(samples) < 2:
                        raise ValueError("dt requires at least two time-aligned samples")
                    sample["dt"] = ((samples[index]["timestamp"] - samples[index - 1]["timestamp"]) if index else
                                    (samples[1]["timestamp"] - samples[0]["timestamp"])) / 1000.
                buffer.add(sample)
                if getattr(env, "sample_callback", None) is None:
                    publish_event(event_sink, "sample", **sample)
            metrics = buffer.calculate_advanced_metrics()
            if not math.isfinite(score_metrics(metrics)):
                raise ValueError("Non-finite controller metrics")
        except Exception as exc:
            reason, error = "error", str(exc)
            lifecycle("error", error)
            break

        # Changing targets invalidate old comparisons and any pending observation.
        target_changes = len({sample["setpoint"] for sample in samples}) > 1
        if (previous and metrics["setpoint"] != previous["metrics"]["setpoint"]) or target_changes:
            best, previous, observe, stable, local_streak = None, None, None, 0, 0
            rejected.clear()
        if previous and parameters != previous["parameters"]:
            stable = 0
        good = not target_changes and is_good_enough(metrics, rules)
        stable = stable + 1 if good else 0
        record = dict(round=round_index, parameters=deepcopy(parameters), metrics=metrics)
        experiments.append(record)
        last_parameters, last_metrics = dict(parameters), dict(metrics)
        publish_round_metrics(event_sink, SimpleNamespace(metrics=metrics, stable_rounds=stable), round_index)
        if not target_changes and metrics["status"] == "STABLE" and (best is None or score_metrics(metrics) < score_metrics(best["metrics"])):
            best = deepcopy(record)
        if previous and previous.get("source") == "local" and should_rollback_to_best(metrics, previous["metrics"]):
            rejected.add(signature(parameters))
        observation, observe = observe, None
        previous = record
        if stable >= CONFIG["REQUIRED_STABLE_ROUNDS"] and not disable_early_exit:
            reason = "stable_rounds_reached"
            break
        if best and parameters != best["parameters"] and should_rollback_to_best(metrics, best["metrics"]) and not disable_early_exit:
            rejected.add(signature(parameters))
            try:
                env.apply_parameters(best["parameters"])
            except Exception as exc:
                reason, error = "error", str(exc)
                lifecycle("error", error)
                break
            rollbacks += 1
            local_streak = 0
            record["action"] = "ROLLBACK"
            publish_event(event_sink, "rollback", round=round_index, target_round=best["round"],
                          parameters=best["parameters"], reason="Measured response regressed; restored the best measured parameters.")
            continue
        # Never buy a suggestion that cannot be measured within the round budget.
        if round_index == max_rounds:
            break
        if target_changes or (good and not disable_early_exit):
            record["action"] = "OBSERVE"
            continue
        if (observation and not disable_early_exit and parameters == observation["parameters"]
            and metrics["setpoint"] == observation["metrics"]["setpoint"]
            and metrics["status"] in {"STABLE", "SLOW_RESPONSE"}
            and metrics["overshoot"] <= rules["overshoot_threshold"]
            and not should_rollback_to_best(metrics, observation["metrics"])):
            record["action"] = "OBSERVE"
            continue

        bounds = {name: dict(min=spec.minimum, max=spec.maximum, max_step=spec.max_step) for name, spec in formula.parameters.items()}
        plan, suggestion, source = None, None, "model"
        if getattr(env, "local_search_enabled", False) and callable(getattr(env, "preview_parameters", None)):
            plan = rank_candidates(parameters, bounds, env.preview_parameters, overshoot_limit=rules["overshoot_threshold"], coordinate_offset=(round_index - 1) * 8)
            publish_event(event_sink, "local_search", round=round_index, prediction=plan)
            if plan["selected"] and (local_streak < 2 or getattr(env, "formula_offline", False)) and signature(plan["selected"]["parameters"]) not in rejected:
                suggestion = dict(parameters=plan["selected"]["parameters"], tuning_action="ADJUST_PARAMETERS",
                                  analysis_summary="Local model predicts lower error within the overshoot limit; verify with measurements.")
                source = "local"
        if controller_requests_abort(controller):
            reason = "stopped_by_user"
            break
        if suggestion is None and not getattr(env, "formula_offline", False):
            context = {key: value for key, value in env.get_prompt_context().items()
                       if key not in {"per_round_guardrail_hint", "discrete_tuning_hint", "pid_limits", "pid_limits_are_runtime_enforced"}}
            context.update(controller_formula=formula.describe(), current_parameters=parameters,
                           execution_mode=mode, response_state="restarts_from_initial_conditions_each_round" if mode == "simulink" else "continuous_state")
            context["signal_evidence"] = {
                name: dict(first=samples[0][name], last=samples[-1][name],
                           minimum=min(s[name] for s in samples), maximum=max(s[name] for s in samples))
                for name in formula.required_signals if all(name in s for s in samples)
            }
            if plan:
                context["local_predictions"] = compact_predictions(plan, parameters)
            anchors = ([best] if best and best["round"] not in {r["round"] for r in experiments[-5:]} else [])
            recent_rejected = next((r for r in reversed(experiments) if r.get("action") == "ROLLBACK"), None)
            if recent_rejected and recent_rejected not in experiments[-5:] and recent_rejected not in anchors:
                anchors.append(recent_rejected)
            if current_stream_round is not None:
                current_stream_round[0] = round_index
            lifecycle("llm_request", f"Requesting controller parameters for round {round_index}.")
            calls += 1
            suggestion = tuner.analyze(buffer.to_prompt_data(include_pid=False), json.dumps(anchors + experiments[-5:], separators=(",", ":")),
                                       tuning_mode=mode, prompt_context=context)
            usage = _tuner_usage(tuner, usage_start)
            publish_event(event_sink, "llm_usage", round=round_index, usage={k: v for k, v in usage.items() if k != "attempts"})
            local_streak = 0
        elif suggestion is None:
            source = "offline_hold"
        if controller_requests_abort(controller):
            reason = "stopped_by_user"
            break
        notes = []
        try:
            if not isinstance(suggestion, dict):
                suggestion = dict(tuning_action="OBSERVE", analysis_summary="No validated candidate; hold parameters.")
                failures += 1
            action = str(suggestion.get("tuning_action", "ADJUST_PARAMETERS")).upper()
            if action not in {"OBSERVE", "ADJUST_PARAMETERS"}:
                raise ValueError("Unsupported formula tuning action")
            candidate, notes = formula.project(parameters, suggestion.get("parameters", {})) if action != "OBSERVE" else (dict(parameters), [])
            if action != "OBSERVE" and not suggestion.get("parameters"):
                raise ValueError("No controller parameters supplied")
            if signature(candidate) in rejected:
                raise ValueError("Candidate previously regressed at this target; hold parameters")
        except (ValueError, TypeError) as exc:
            candidate, action, notes = dict(parameters), "OBSERVE", [str(exc)]
            failures += 1
        if notes:
            guards += 1
        if action == "OBSERVE":
            observe = deepcopy(record)
        elif candidate != parameters:
            try:
                env.apply_parameters(candidate)
            except Exception as exc:
                reason, error = "error", str(exc)
                lifecycle("error", error)
                break
        if source == "local":
            local_count += 1
            local_streak += 1
        record.update(action=action, source=source)
        decision = dict(round=round_index, action=action, source=source, parameters=candidate,
                        analysis_summary=str(suggestion.get("analysis_summary", ""))[:1000],
                        guardrail_notes=notes, fallback_used=source != "model")
        decisions.append(decision)
        publish_event(event_sink, "decision", **decision)

    try:
        actual = env.get_parameters()
    except Exception as exc:
        actual = None
        reason, error = "error", error or f"Final parameter read-back failed: {exc}"
        lifecycle("error", error)
    verified = actual is not None and last_parameters is not None and actual == last_parameters
    if reason not in {"error", "stopped_by_user"}:
        lifecycle("completed", "Measured controller verification completed." if reason == "stable_rounds_reached" else "Reached maximum tuning rounds.")
    return dict(elapsed_sec=time.time() - started, provider=CONFIG["LLM_PROVIDER"], model=CONFIG["LLM_MODEL_NAME"],
                controller_kind="formula", controller_formula=formula.describe(), final_parameters=actual, evaluated_parameters=last_parameters,
                final_parameters_verified=verified, final_pid=None, final_secondary_pid=None,
                final_pid_verified=False, final_metrics=last_metrics if verified else {}, evaluated_metrics=last_metrics,
                experiments=deepcopy(experiments), decisions=decisions, best_result=deepcopy(best),
                decision_requests=calls, local_decisions=local_count, fallback_count=failures, guardrail_count=guards,
                rollback_count=rollbacks, rounds_completed=len(experiments),
                converged=verified and reason == "stable_rounds_reached", completed_reason=reason,
                stopped=reason == "stopped_by_user", error=error,
                llm_usage=_tuner_usage(tuner, usage_start))
