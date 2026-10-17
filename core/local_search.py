"""Bounded coordinate search against an environment's explicit preview model.

Predictions never enter measured history/best results. The caller must measure
each accepted candidate and may use only a bounded number before consulting AI.
"""
from __future__ import annotations

import math
from pid_safety import score_metrics


def parameter_signature(parameters):
    return tuple(sorted(parameters.items()))


def rank_candidates(current, bounds, preview, *, max_candidates=33, overshoot_limit=1., coordinate_offset=0, project=None, rejected=()):
    candidates = [dict(current)]
    keys = list(current)
    if keys:
        coordinate_offset %= len(keys)
        keys = keys[coordinate_offset:] + keys[:coordinate_offset]
    for key in keys:
        value = current[key]
        spec = bounds[key]
        step = min(spec["max_step"], max(abs(value) * .5, (spec["max"] - spec["min"]) * .005))
        for delta in (step, -step, step * .25, -step * .25):
            proposal = dict(current)
            proposal[key] = max(spec["min"], min(spec["max"], value + max(-spec["max_step"], min(spec["max_step"], delta))))
            if project is not None:
                proposal = project(proposal)
            if proposal not in candidates and parameter_signature(proposal) not in rejected:
                candidates.append(proposal)
            if len(candidates) >= max_candidates:
                break
        if len(candidates) >= max_candidates:
            break
    predictions = []
    for parameters in candidates:
        try:
            metrics = preview(parameters)
            score = score_metrics(metrics)
            if math.isfinite(score):
                predictions.append(dict(parameters=parameters, metrics=metrics, score=score))
        except (ValueError, ArithmeticError):
            continue
    baseline = next((p for p in predictions if p["parameters"] == current), None)
    ranked = sorted((p for p in predictions if p["metrics"]["overshoot"] <= overshoot_limit
                     and p["metrics"]["status"] != "OSCILLATING"), key=lambda p: p["score"])
    chosen = None
    if baseline and ranked and ranked[0]["parameters"] != current:
        if baseline["score"] - ranked[0]["score"] > max(.05, abs(baseline["score"]) * .05):
            chosen = ranked[0]
    return dict(source="cloned_environment_rollout", evaluated_candidates=len(candidates),
                baseline=baseline, ranked=ranked[:4], selected=chosen)


def pid_bounds(current, limits):
    return {key: {**spec, "max_step": max(abs(current[key]) * .75, .01)} for key, spec in limits.items()}


def compact_predictions(plan, current):
    """Only sparse changes and forecast metrics go back into model context."""
    def row(prediction):
        if prediction is None:
            return None
        return dict(changes={k: v for k, v in prediction["parameters"].items() if v != current[k]},
                    metrics=prediction["metrics"], score=prediction["score"])
    return dict(source=plan["source"], evaluated_candidates=plan["evaluated_candidates"],
                baseline=row(plan["baseline"]), ranked=[row(p) for p in plan["ranked"]], selected=row(plan["selected"]))
