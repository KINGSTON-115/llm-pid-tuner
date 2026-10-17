"""Bind a declared controller graph to an existing device/model implementation.

Python executes the graph. External environments already implement it; explicit
bindings identify writable coefficients. No formula is uploaded as device code.
"""
from __future__ import annotations

import math
from core.controller_formula import BUILTIN_SIGNALS, ControllerFormula, finite, valid_name


class BoundFormulaEnvironment:
    def __init__(self, environment, formula: ControllerFormula):
        self.environment = environment
        self.controller_formula = formula
        self.local_search_enabled = False
        self.parameter_bindings = formula.specification.get("parameter_bindings", {})
        self.signal_bindings = formula.specification.get("signal_bindings", {})
        if not isinstance(self.parameter_bindings, dict) or set(self.parameter_bindings) != set(formula.parameters):
            raise ValueError("External controllers require parameter_bindings for every tunable parameter")
        if not isinstance(self.signal_bindings, dict):
            raise ValueError("signal_bindings must map formula signal names to telemetry fields")
        if any(not valid_name(name) or not valid_name(field) for name, field in self.signal_bindings.items()):
            raise ValueError("Signal bindings must contain plain signal identifiers")
        reserved = BUILTIN_SIGNALS | {"timestamp", "pwm", "p", "i", "d", "p2", "i2", "d2", "parameters"}
        if any(name in reserved and name != field for name, field in self.signal_bindings.items()):
            raise ValueError("Signal aliases cannot overwrite measured controller telemetry")
        missing = set(formula.required_signals) - BUILTIN_SIGNALS - self.signal_bindings.keys()
        if missing:
            raise ValueError("Missing signal_bindings: " + ", ".join(sorted(missing)))
        seen = []
        for binding in self.parameter_bindings.values():
            if isinstance(binding, str):
                if binding not in {"p", "i", "d", "p2", "i2", "d2"}:
                    raise ValueError(f"Unsupported telemetry gain binding: {binding}")
            elif isinstance(binding, dict):
                if not getattr(environment, "bridge", None) or not getattr(environment.bridge, "_eng", None):
                    raise ValueError("Block bindings require a connected MATLAB Engine")
                if not isinstance(binding.get("block"), str) or not binding["block"] or not isinstance(binding.get("field"), str) or not binding["field"]:
                    raise ValueError("Block binding requires block and field")
            else:
                raise ValueError("A parameter binding must name a gain or a Simulink block/field")
            if binding in seen:
                raise ValueError("Multiple parameters cannot bind the same destination")
            seen.append(binding)
        kinds = {isinstance(binding, str) for binding in self.parameter_bindings.values()}
        if len(kinds) > 1:
            raise ValueError("Use either telemetry gain bindings or block bindings in one controller")
        self.block_bindings = kinds == {False}
        self._sample_callback = None
        self._block_snapshot = None
        if self.block_bindings:
            self.get_parameters()  # Hardware gain telemetry is learned during collection.

    def __getattr__(self, name):
        return getattr(self.environment, name)

    @property
    def sample_callback(self):
        return self._sample_callback

    @sample_callback.setter
    def sample_callback(self, callback):
        self._sample_callback = callback
        def notify(sample):
            self._annotate_sample(sample)
            callback(sample)
        self.environment.sample_callback = notify if callback is not None else None

    def _annotate_sample(self, sample):
        if self.block_bindings:
            sample["parameters"] = dict(self._block_snapshot or self.get_parameters())
        else:
            if any(binding not in sample for binding in self.parameter_bindings.values()):
                raise ValueError("Bound parameter values are missing from telemetry")
            sample["parameters"] = {name: finite(sample[binding]) for name, binding in self.parameter_bindings.items()}
        for signal, field in self.signal_bindings.items():
            if field in sample:
                sample[signal] = sample[field]

    def _read_blocks(self):
        eng = self.environment.bridge._eng
        return {name: finite(float(eng.feval("get_param", binding["block"], binding["field"], nargout=1)))
                for name, binding in self.parameter_bindings.items()}

    def get_parameters(self):
        if self.block_bindings:
            return self._read_blocks()
        primary, secondary = self.environment.get_current_pid()
        values = {**primary, **({k + "2": v for k, v in secondary.items()} if secondary else {})}
        if any(binding not in values for binding in self.parameter_bindings.values()):
            raise ValueError("A bound controller is absent from telemetry")
        return {name: finite(values[binding]) for name, binding in self.parameter_bindings.items()}

    def apply_parameters(self, parameters):
        if self.block_bindings:
            eng = self.environment.bridge._eng
            previous = self._read_blocks()
            written = []
            try:
                for name, value in parameters.items():
                    if value == previous[name]:
                        continue
                    binding = self.parameter_bindings[name]
                    written.append(name)
                    eng.feval("set_param", binding["block"], binding["field"], format(finite(value), ".17g"), nargout=0)
                if written:
                    self.environment.bridge._model_needs_update = True
                    update = getattr(self.environment.bridge, "_apply_controller_update", None)
                    if callable(update):
                        update()
                actual = self._read_blocks()
                if any(not math.isclose(actual[name], value, rel_tol=1.e-6, abs_tol=1.e-8) for name, value in parameters.items()):
                    raise RuntimeError("Parameter read-back differs from requested values")
            except Exception as exc:
                failed_restore = []
                for name in reversed(written):
                    binding = self.parameter_bindings[name]
                    try:
                        eng.feval("set_param", binding["block"], binding["field"], format(previous[name], ".17g"), nargout=0)
                    except Exception:
                        failed_restore.append(name)
                self.environment.bridge._model_needs_update = True
                raise RuntimeError(f"Parameter write failed; restore failures={failed_restore}: {exc}") from exc
            return
        else:
            primary, secondary = self.environment.get_current_pid()
            primary = dict(primary)
            secondary = dict(secondary) if secondary is not None else None
            for name, value in parameters.items():
                binding = self.parameter_bindings[name]
                (secondary if binding.endswith("2") else primary)[binding[0]] = finite(value)
            self.environment.apply_pid(primary, secondary)
            issue = getattr(self.environment, "last_apply_issue", "")
            if issue:
                raise RuntimeError(issue)
        actual = self.get_parameters()
        if any(not math.isclose(actual[name], value, rel_tol=1.e-6, abs_tol=1.e-8) for name, value in parameters.items()):
            raise RuntimeError("Parameter read-back differs from requested values")

    def collect_samples(self):
        self._block_snapshot = self.get_parameters() if self.block_bindings else None
        samples = self.environment.collect_samples()
        if self.block_bindings and self.get_parameters() != self._block_snapshot:
            raise ValueError("Model parameters changed during collection")
        for sample in samples:
            self._annotate_sample(sample)
            for required in self.controller_formula.required_signals:
                field = self.signal_bindings.get(required, required)
                if field in {"dt", "time"}:
                    if "timestamp" not in sample:
                        raise ValueError("Controller signal requires timestamp telemetry")
                elif field not in sample or not math.isfinite(float(sample[field])):
                    raise ValueError(f"Missing or invalid required telemetry: {required} -> {field}")
        return samples
