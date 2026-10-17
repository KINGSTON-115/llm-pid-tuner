"""Execute user controllers against the existing thermal plant."""
from core.controller_formula import BUILTIN_SIGNALS, ControllerFormula
from sim.model import CONTROL_INTERVAL, HeatingSimulator


class FormulaHeatingSimulator(HeatingSimulator):
    def __init__(self, formula: ControllerFormula, **kwargs):
        super().__init__(**kwargs)
        missing = set(formula.required_signals) - BUILTIN_SIGNALS
        if missing:
            raise ValueError("Thermal simulation cannot supply: " + ", ".join(sorted(missing)))
        if formula.output_limits[0] < 0 or formula.output_limits[1] > 255:
            raise ValueError("Thermal actuator output_limits must lie within [0, 255]")
        self.controller_formula = formula
        self.parameters = formula.initial_parameters
        self.formula_runtime = formula.runtime()

    def compute_pid(self):
        self.pwm = self.formula_runtime.step(dict(error=self.setpoint - self.temp,
            setpoint=self.setpoint, input=self.temp, time=self.timestamp / 1000.),
            self.parameters, CONTROL_INTERVAL)

    def get_data(self):
        return {**super().get_data(), "parameters": dict(self.parameters)}
