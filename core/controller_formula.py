"""A bounded mathematical expression graph, interpreted without eval/exec.

Instantaneous assignments form an acyclic graph; delay permits feedback.
Stateful operators are keyed by their AST
location, so nested integrators/derivatives retain independent discrete state.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass
import math
import re
from typing import Any


FUNCTIONS = {"abs": (1, 1), "min": (2, 8), "max": (2, 8), "clip": (3, 3),
             "sin": (1, 1), "cos": (1, 1), "tanh": (1, 1), "sqrt": (1, 1),
             "integral": (1, 1), "derivative": (1, 1), "delay": (1, 2), "lowpass": (2, 2)}
BUILTIN_SIGNALS = {"error", "setpoint", "input", "dt", "time"}


def finite(value: Any) -> float:
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"Expected a finite number, got {value!r}")
    return float(value)


def valid_name(name: str) -> bool:
    return isinstance(name, str) and bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name)) and name not in FUNCTIONS


@dataclass(frozen=True)
class Parameter:
    value: float
    minimum: float
    maximum: float
    max_step: float


class ControllerFormula:
    MAX_NODES = 2048
    MAX_DEPTH = 48

    def __init__(self, specification: dict[str, Any]):
        if not isinstance(specification, dict):
            raise ValueError("Controller specification must be a JSON object")
        self.specification = deepcopy(specification)
        equations = specification.get("equations")
        if not isinstance(equations, str) or not equations.strip() or len(equations) > 24000:
            raise ValueError("equations must contain assignments (maximum 24000 characters)")
        raw_parameters = specification.get("parameters", {})
        if not isinstance(raw_parameters, dict):
            raise ValueError("parameters must be a mapping of names to value/min/max/max_step")
        self.parameters: dict[str, Parameter] = {}
        for name, raw in raw_parameters.items():
            if not valid_name(name) or name in BUILTIN_SIGNALS or not isinstance(raw, dict):
                raise ValueError(f"Invalid parameter {name!r}")
            try:
                value, lo, hi, step = (finite(raw[key]) for key in ("value", "min", "max", "max_step"))
            except KeyError as exc:
                raise ValueError(f"Parameter {name} requires value/min/max/max_step") from exc
            if not lo <= value <= hi or step <= 0 or lo >= hi:
                raise ValueError(f"Invalid bounds or step for {name}")
            self.parameters[name] = Parameter(value, lo, hi, step)
        try:
            tree = ast.parse(equations.replace("^", "**"), mode="exec")
        except (SyntaxError, RecursionError) as exc:
            raise ValueError(f"Invalid controller equations: {exc}") from exc
        if len(list(ast.walk(tree))) > self.MAX_NODES:
            raise ValueError("Controller graph is too large")
        self.expressions: dict[str, ast.expr] = {}
        self.dependencies: dict[str, set[str]] = {}
        self.operations: dict[str, set[str]] = {}
        self.instant_dependencies: dict[str, set[str]] = {}
        for statement in tree.body:
            if not isinstance(statement, ast.Assign) or len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
                raise ValueError("Only name = mathematical expression assignments are supported")
            name = statement.targets[0].id
            if not valid_name(name) or name in self.parameters or name in BUILTIN_SIGNALS or name in self.expressions:
                raise ValueError(f"Duplicate or reserved node {name!r}")
            names, calls = set(), set()
            self._validate(statement.value, names, calls)
            self.expressions[name] = statement.value
            self.dependencies[name], self.operations[name] = names, calls
            def instant_names(node):
                if isinstance(node, ast.Name): return {node.id}
                if isinstance(node, ast.Call):
                    children = node.args[1:] if node.func.id == "delay" else node.args
                else:
                    children = ast.iter_child_nodes(node)
                return set().union(*(instant_names(child) for child in children))
            self.instant_dependencies[name] = instant_names(statement.value)
        self.output = specification.get("output", "u" if "u" in self.expressions else next(reversed(self.expressions), "u"))
        if not isinstance(self.output, str) or self.output not in self.expressions:
            raise ValueError(f"Output {self.output!r} is not defined")
        self.order: list[str] = []
        self.layers: dict[str, int] = {}
        reachable, pending = set(), [self.output]
        while pending:
            name = pending.pop()
            if name in reachable: continue
            reachable.add(name)
            pending.extend(self.dependencies[name] & self.expressions.keys())

        def visit(name, path):
            if len(path) > self.MAX_DEPTH:
                raise ValueError("Controller dependency depth exceeds 48")
            if name in path:
                raise ValueError(f"Algebraic cycle at {name}; use an explicit stateful operator")
            if name in self.order:
                return
            for dependency in sorted(self.instant_dependencies[name] & self.expressions.keys()):
                visit(dependency, path + [name])
            self.layers[name] = 1 + max((self.layers[d] for d in self.instant_dependencies[name] if d in self.layers), default=0)
            self.order.append(name)

        for name in sorted(reachable):
            visit(name, [])
        reachable_names = set().union(*(self.dependencies[name] for name in self.order))
        unused = set(self.parameters) - reachable_names
        if unused:
            raise ValueError("Unused tunable parameters: " + ", ".join(sorted(unused)))
        self.required_signals = sorted(reachable_names - self.expressions.keys() - self.parameters.keys())
        limits = specification.get("output_limits", [0., 255.])
        if not isinstance(limits, list) or len(limits) != 2:
            raise ValueError("output_limits requires [minimum, maximum]")
        self.output_limits = tuple(finite(v) for v in limits)
        if self.output_limits[0] >= self.output_limits[1]:
            raise ValueError("Invalid output limits")

    def _validate(self, node, names, calls, depth=0):
        if depth > self.MAX_DEPTH:
            raise ValueError("Expression depth exceeds 48")
        if isinstance(node, ast.Constant):
            finite(node.value)
        elif isinstance(node, ast.Name) and valid_name(node.id):
            names.add(node.id)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            self._validate(node.left, names, calls, depth + 1)
            self._validate(node.right, names, calls, depth + 1)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            self._validate(node.operand, names, calls, depth + 1)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS and not node.keywords:
            lo, hi = FUNCTIONS[node.func.id]
            if not lo <= len(node.args) <= hi:
                raise ValueError(f"Wrong argument count for {node.func.id}")
            calls.add(node.func.id)
            for arg in node.args:
                self._validate(arg, names, calls, depth + 1)
        else:
            raise ValueError(f"Unsupported expression: {ast.dump(node)[:120]}")

    @property
    def initial_parameters(self):
        return {name: spec.value for name, spec in self.parameters.items()}

    def project(self, current: dict, proposed: dict) -> tuple[dict[str, float], list[str]]:
        if not isinstance(proposed, dict):
            raise ValueError("parameters must be a JSON object")
        if set(proposed) - self.parameters.keys():
            raise ValueError("Unknown tunable parameter: " + ", ".join(sorted(set(proposed) - self.parameters.keys())))
        output, notes = dict(current), []
        for name, value in proposed.items():
            value = finite(value)
            spec = self.parameters[name]
            safe = max(spec.minimum, min(spec.maximum, max(current[name] - spec.max_step, min(current[name] + spec.max_step, value))))
            output[name] = safe
            if safe != value:
                notes.append(f"{name}: {value:g} -> {safe:g} (bounds/step)")
        return output, notes

    def describe(self):
        influences = {}
        feedback_nodes = []
        for name in self.order:
            visited, pending, parameters = set(), [name], set()
            while pending:
                current = pending.pop()
                if current in visited: continue
                visited.add(current)
                parameters.update(self.dependencies[current] & self.parameters.keys())
                if name in self.dependencies[current]:
                    feedback_nodes.append(name)
                pending.extend(self.dependencies[current] & self.expressions.keys())
            influences[name] = sorted(parameters)
        state_depths = {}
        def depth(node, operator):
            if isinstance(node, ast.Name):
                return state_depths.get(node.id, {}).get(operator, 0)
            own = int(isinstance(node, ast.Call) and node.func.id == operator)
            children = node.args if isinstance(node, ast.Call) else list(ast.iter_child_nodes(node))
            return own + max((depth(child, operator) for child in children), default=0)
        for name in self.order:
            state_depths[name] = {operator: depth(self.expressions[name], operator) for operator in ("integral", "derivative", "delay", "lowpass")}
        return dict(equations=self.specification["equations"], output=self.output,
                    parameters={name: dict(min=spec.minimum, max=spec.maximum, max_step=spec.max_step) for name, spec in self.parameters.items()},
                    required_signals=self.required_signals, evaluation_order=self.order, layers=self.layers,
                    parameter_dependencies=influences,
                    delayed_feedback=bool(feedback_nodes), feedback_nodes=sorted(set(feedback_nodes)),
                    state_operator_depths=state_depths,
                    stateful_operators=sorted(set().union(*(self.operations[n] for n in self.order)) & {"integral", "derivative", "delay", "lowpass"}),
                    output_limits=self.output_limits,
                    discretization="integral: forward rectangular; derivative: backward difference (first=0); delay: previous sample (default initial=0), supports delayed feedback; lowpass: alpha=dt/(tau+dt). Each call has independent persistent state. Output is saturated before feedback is stored.")

    def runtime(self):
        return FormulaRuntime(self)


class FormulaRuntime:
    def __init__(self, formula: ControllerFormula):
        self.formula = formula
        self.state: dict = {}

    def step(self, signals: dict, parameters: dict, dt: float) -> float:
        dt = finite(dt)
        if dt <= 0:
            raise ValueError("dt must be positive")
        values = {**signals, **parameters, "dt": dt}
        for name in self.formula.required_signals:
            if name not in values:
                raise ValueError(f"Missing required signal: {name}")
            values[name] = finite(values[name])
        pending = dict(self.state)
        delayed_updates = {}

        def evaluate(node):
            if isinstance(node, ast.Constant):
                return float(node.value)
            if isinstance(node, ast.Name):
                return finite(values[node.id])
            if isinstance(node, ast.UnaryOp):
                value = evaluate(node.operand)
                return -value if isinstance(node.op, ast.USub) else value
            if isinstance(node, ast.BinOp):
                a, b = evaluate(node.left), evaluate(node.right)
                if isinstance(node.op, ast.Add): result = a + b
                elif isinstance(node.op, ast.Sub): result = a - b
                elif isinstance(node.op, ast.Mult): result = a * b
                elif isinstance(node.op, ast.Div): result = a / b
                else:
                    if abs(b) > 16: raise ValueError("Exponent exceeds runtime limit 16")
                    result = a ** b
                return finite(result)
            name, key = node.func.id, (node.lineno, node.col_offset)
            if name == "delay":
                delayed_updates[key] = node.args[0]
                if key in self.state:
                    return self.state[key]
                return evaluate(node.args[1]) if len(node.args) > 1 else 0.
            args = [evaluate(arg) for arg in node.args]
            if name == "integral":
                result = self.state.get(key, 0.) + args[0] * dt
            elif name == "derivative":
                result = (args[0] - self.state.get(key, args[0])) / dt
                pending[key] = args[0]
                return finite(result)
            elif name == "lowpass":
                if args[1] <= 0: raise ValueError("lowpass time constant must be positive")
                previous = self.state.get(key, args[0])
                result = previous + dt / (args[1] + dt) * (args[0] - previous)
            elif name == "clip":
                if args[1] > args[2]: raise ValueError("clip bounds reversed")
                return max(args[1], min(args[2], args[0]))
            else:
                func = {"abs": abs, "min": min, "max": max, "sin": math.sin, "cos": math.cos, "tanh": math.tanh, "sqrt": math.sqrt}[name]
                return finite(func(*args))
            pending[key] = finite(result)
            return result

        try:
            for name in self.formula.order:
                values[name] = finite(evaluate(self.formula.expressions[name]))
                if name == self.formula.output:
                    values[name] = max(self.formula.output_limits[0], min(self.formula.output_limits[1], values[name]))
            output = max(self.formula.output_limits[0], min(self.formula.output_limits[1], values[self.formula.output]))
            values[self.formula.output] = output
            processed = set()
            while delayed_updates.keys() - processed:
                for key in list(delayed_updates.keys() - processed):
                    pending[key] = finite(evaluate(delayed_updates[key]))
                    processed.add(key)
        except (ArithmeticError, TypeError, KeyError) as exc:
            raise ValueError(f"Controller evaluation failed: {exc}") from exc
        self.state = pending
        return output
