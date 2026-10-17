"""Controller editor with deterministic dependency inspection before saving."""
import json
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QPlainTextEdit,
                              QPushButton, QVBoxLayout, QFileDialog)
from core.config import CONFIG
from core.controller_formula import BUILTIN_SIGNALS, ControllerFormula
from desktop.i18n import Bindings, t
from desktop.settings import save_settings, warning
from desktop.widgets import label


EXAMPLE = {
    "equations": "outer = Kp * error + Ki * integral(error)\nfiltered = lowpass(outer, tau)\nu = clip(filtered - Kd * derivative(input), 0, 255)",
    "output": "u",
    "parameters": {
        "Kp": {"value": 1., "min": 0., "max": 100., "max_step": 1.},
        "Ki": {"value": .1, "min": 0., "max": 10., "max_step": .1},
        "Kd": {"value": .05, "min": 0., "max": 10., "max_step": .1},
        "tau": {"value": .5, "min": .01, "max": 5., "max_step": .2},
    },
    "output_limits": [0., 255.],
}


class FormulaEditor(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.copy = Bindings()
        self.copy.bind(self.setWindowTitle, "formula_title")
        self.resize(820, 700)
        layout = QVBoxLayout(self)
        hint = label("", "muted", True)
        self.copy.bind(hint.setText, "formula_hint")
        layout.addWidget(hint)
        self.editor = QPlainTextEdit()
        self.editor.setPlainText(json.dumps(CONFIG["CONTROLLER_SPEC"], ensure_ascii=False, indent=2) if CONFIG.get("CONTROLLER_SPEC") else "")
        self.copy.bind(self.editor.setAccessibleName, "formula_title")
        layout.addWidget(self.editor, 3)
        buttons = QHBoxLayout()
        for key, callback in (("formula_example", lambda: self.editor.setPlainText(json.dumps(EXAMPLE, indent=2))),
                              ("formula_import", self.import_file), ("formula_inspect", self.inspect),
                              ("formula_clear", self.editor.clear)):
            button = QPushButton()
            self.copy.bind(button.setText, key)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.inspection = QPlainTextEdit()
        self.inspection.setReadOnly(True)
        layout.addWidget(self.inspection, 2)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.copy.bind(controls.button(QDialogButtonBox.StandardButton.Save).setText, "save")
        self.copy.bind(controls.button(QDialogButtonBox.StandardButton.Cancel).setText, "cancel")
        controls.accepted.connect(self.save)
        controls.rejected.connect(self.reject)
        layout.addWidget(controls)
        self.inspect()
        if parent is not None and hasattr(parent, "preferences"):
            parent.preferences.language_changed.connect(self.copy.refresh)

    def specification(self):
        text = self.editor.toPlainText().strip()
        if not text:
            return {}
        spec = json.loads(text)
        ControllerFormula(spec)
        return spec

    def inspect(self):
        try:
            text = self.editor.toPlainText().strip()
            if text and not text.startswith("{"):
                formula = ControllerFormula({"equations": text})
                info = formula.describe()
                info["symbols_requiring_parameter_bounds_or_signal_bindings"] = sorted(set(formula.required_signals) - BUILTIN_SIGNALS)
                self.show_inspection(info)
                return False  # Inspect raw equations, but require bounds before saving.
            spec = self.specification()
            if spec:
                self.show_inspection(ControllerFormula(spec).describe())
            else:
                self.inspection.setPlainText(t("formula_default"))
            return True
        except (ValueError, TypeError, RecursionError) as exc:
            self.inspection.setPlainText(str(exc))
            return False

    def show_inspection(self, info):
        self.last_inspection = info
        parameters = "\n".join(f"  {name}: [{spec['min']:g}, {spec['max']:g}], Δ ≤ {spec['max_step']:g}" for name, spec in info["parameters"].items()) or "  —"
        layers = "\n".join(
            f"  {name}: L{info['layers'][name]} ← {','.join(info['parameter_dependencies'][name]) or '—'}; "
            + ", ".join(f"{operator}×{depth}" for operator, depth in info["state_operator_depths"][name].items() if depth)
            for name in info["evaluation_order"])
        text = t("formula_summary", signals=", ".join(info["required_signals"]) or "—",
                 parameters=parameters, layers=layers, feedback=", ".join(info["feedback_nodes"]) or "—")
        unresolved = info.get("symbols_requiring_parameter_bounds_or_signal_bindings")
        if unresolved:
            text += "\n\n" + t("formula_unresolved", symbols=", ".join(unresolved))
        self.inspection.setPlainText(text)

    def import_file(self):
        path, _ = QFileDialog.getOpenFileName(self, t("formula_import"), "", "JSON (*.json)", options=QFileDialog.Option.DontUseNativeDialog)
        if path:
            try:
                from pathlib import Path
                self.editor.setPlainText(Path(path).read_text(encoding="utf-8-sig"))
                self.inspect()
            except OSError as exc:
                warning(self, t("check_settings"), str(exc))

    def save(self):
        if not self.inspect():
            return
        try:
            save_settings({"CONTROLLER_SPEC": self.specification()})
        except (ValueError, OSError) as exc:
            warning(self, t("save_error"), str(exc))
            return
        self.accept()
