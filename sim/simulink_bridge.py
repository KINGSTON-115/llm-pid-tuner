#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sim/simulink_bridge.py - Simulink 仿真桥接层

通过 MATLAB Engine API for Python 与 Simulink 模型双向通信。
返回数据格式与 sim/model.py 的 HeatingSimulator.get_data() 完全一致，
保证上层调参逻辑可零修改复用。

前置条件：
  - 已安装 MATLAB R2021b 或更高版本
  - 已安装 MATLAB Engine API for Python：
      cd <MATLAB_ROOT>/extern/engines/python && python setup.py install
  - Simulink 模型中须包含：
      1. 一个 PID Controller 模块（或等效块）
      2. 一个 To Workspace 模块，变量名与 MATLAB_OUTPUT_SIGNAL 配置一致
         （保存格式设为 Array）
"""

from __future__ import annotations

import time
from typing import Optional

try:
    import matlab.engine  # type: ignore
    _MATLAB_AVAILABLE = True
except ImportError:
    _MATLAB_AVAILABLE = False


class SimulinkBridge:
    """
    Simulink 仿真桥接层。

    Parameters
    ----------
    model_path : str
        Simulink .slx 文件的完整路径，例如 "C:/models/my_pid_model.slx"。
    setpoint : float
        调参目标值（与模型中的 Setpoint 一致）。
    pid_block_path : str
        PID 模块在 Simulink 模型中的完整路径，
        例如 "my_pid_model/PID Controller"。
    output_signal : str
        To Workspace 模块输出到 MATLAB 工作区的变量名，例如 "y_out"。
    sim_step_time : float
        每轮调参仿真的时长（仿真时间，单位秒）。
    """

    def __init__(
        self,
        model_path    : str,
        setpoint      : float,
        pid_block_path: str,
        output_signal : str,
        sim_step_time : float = 10.0,
    ) -> None:
        if not _MATLAB_AVAILABLE:
            raise ImportError(
                "[SimulinkBridge] 未找到 matlabengine 包。\n"
                "请先安装 MATLAB Engine API for Python：\n"
                "  cd <MATLAB_ROOT>/extern/engines/python\n"
                "  python setup.py install\n"
                "详见：https://www.mathworks.com/help/matlab/matlab_external/"
                "install-the-matlab-engine-for-python.html"
            )

        self.model_path     = model_path
        self.setpoint       = setpoint
        self.pid_block_path = pid_block_path
        self.output_signal  = output_signal
        self.sim_step_time  = sim_step_time

        self.kp: float = 1.0
        self.ki: float = 0.1
        self.kd: float = 0.05

        self._eng             : Optional[object] = None
        self._model_name      : str              = ""
        self._current_sim_time: float            = 0.0
        self._last_data       : list[dict]       = []

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """启动 MATLAB Engine 并加载 Simulink 模型。"""
        print("[Simulink] 正在启动 MATLAB Engine，请稍候...")
        self._eng = matlab.engine.start_matlab()

        # 提取模型名（不含路径和扩展名）
        import os
        self._model_name = os.path.splitext(os.path.basename(self.model_path))[0]
        model_dir = os.path.dirname(os.path.abspath(self.model_path))

        # 将模型目录加入 MATLAB 路径并加载模型
        self._eng.addpath(model_dir, nargout=0)
        self._eng.load_system(self.model_path, nargout=0)
        print(f"[Simulink] 模型已加载: {self._model_name}")
        self._apply_model_setpoint()

        # 设置仿真模式为外部控制（逐步推进）
        self._eng.set_param(self._model_name, "SimulationMode", "normal", nargout=0)
        self._current_sim_time = 0.0

        # 从 Simulink PID 块读取当前参数作为初始值
        try:
            self.kp = float(self._eng.get_param(self.pid_block_path, "P", nargout=1))
            self.ki = float(self._eng.get_param(self.pid_block_path, "I", nargout=1))
            self.kd = float(self._eng.get_param(self.pid_block_path, "D", nargout=1))
            print(f"[Simulink] 读取初始 PID: P={self.kp}, I={self.ki}, D={self.kd}")
        except Exception:
            print(f"[Simulink] 无法读取 PID 初始值，使用默认值 P={self.kp}, I={self.ki}, D={self.kd}")

    def disconnect(self) -> None:
        """关闭 Simulink 模型并退出 MATLAB Engine。"""
        if self._eng is not None:
            try:
                self._eng.save_system(self._model_name, self.model_path, nargout=0)
                print(f"[Simulink] 模型已保存: {self.model_path}")
            except Exception as e:
                print(f"[WARN] 保存 Simulink 模型时出错: {e}")
            try:
                self._eng.close_system(self._model_name, 0, nargout=0)
            except Exception as e:
                print(f"[WARN] 关闭 Simulink 模型时出错: {e}")
            self._eng.quit()
            self._eng = None
            print("[Simulink] Engine 已关闭。")

    # ------------------------------------------------------------------
    # PID 参数写入
    # ------------------------------------------------------------------

    def set_pid(self, p: float, i: float, d: float) -> None:
        """
        将 PID 参数写入 Simulink PID Controller 模块。

        依赖 MATLAB Engine 的 set_param，对标准 Simulink PID Controller
        模块的 P / I / D 参数名直接写入。
        """
        self.kp, self.ki, self.kd = p, i, d
        if self._eng is not None:
            self._eng.set_param(self.pid_block_path, "P", str(p), nargout=0)
            self._eng.set_param(self.pid_block_path, "I", str(i), nargout=0)
            self._eng.set_param(self.pid_block_path, "D", str(d), nargout=0)

    # ------------------------------------------------------------------
    # 仿真步进与数据采集
    # ------------------------------------------------------------------

    def _get_field_or_none(self, obj: object, field_name: str) -> Optional[object]:
        """Best-effort MATLAB struct/object field access."""
        try:
            return self._eng.getfield(obj, field_name, nargout=1)  # type: ignore[union-attr]
        except Exception:
            return None

    def _to_string_list(self, raw_value: object) -> list[str]:
        if raw_value is None:
            return []
        if isinstance(raw_value, str):
            return [raw_value]
        try:
            values = list(raw_value)  # type: ignore[arg-type]
        except TypeError:
            return [str(raw_value)]
        return [str(value) for value in values]

    def _with_suppressed_engine_warnings(self, callback):
        if self._eng is None or not hasattr(self._eng, "eval"):
            return callback()

        warnings_disabled = False
        try:
            self._eng.eval("warning('off','all');", nargout=0)  # type: ignore[union-attr]
            warnings_disabled = True
        except Exception:
            return callback()

        try:
            return callback()
        finally:
            if warnings_disabled:
                try:
                    self._eng.eval("warning('on','all');", nargout=0)  # type: ignore[union-attr]
                except Exception:
                    pass

    def _find_blocks_by_type(self, block_type: str) -> list[str]:
        def _call_find_system():
            return self._eng.find_system(  # type: ignore[union-attr]
                self._model_name,
                "LookUnderMasks",
                "all",
                "FollowLinks",
                "on",
                "BlockType",
                block_type,
                nargout=1,
            )

        raw_blocks = self._with_suppressed_engine_warnings(_call_find_system)
        return self._to_string_list(raw_blocks)

    def _resolve_setpoint_block(self) -> tuple[str | None, str | None]:
        keywords = ("setpoint", "reference", "ref", "step", "目标", "给定")
        candidates: list[tuple[int, str, str]] = []

        for block_type in ("Step", "Constant"):
            for block_path in self._find_blocks_by_type(block_type):
                score = 0
                lowered = block_path.lower()
                if any(keyword in lowered for keyword in keywords):
                    score += 10
                if block_path.rsplit("/", 1)[-1] in {"Step", "Setpoint", "Reference"}:
                    score += 5
                candidates.append((score, block_path, block_type))

        if not candidates:
            return None, None

        candidates.sort(key=lambda item: (-item[0], item[1]))
        best_score, best_path, best_type = candidates[0]

        if len(candidates) > 1:
            second_score = candidates[1][0]
            if best_score == second_score and best_score == 0:
                return None, None

        return best_path, best_type

    def _setpoint_parameter_name(self, block_type: str) -> str | None:
        if block_type == "Step":
            return "After"
        if block_type == "Constant":
            return "Value"
        return None

    def _apply_model_setpoint(self) -> None:
        block_path, block_type = self._resolve_setpoint_block()
        if not block_path or not block_type:
            print(
                "[Simulink][WARN] 未能自动识别设定值来源块，"
                f"请确认模型内目标值已与 MATLAB_SETPOINT={self.setpoint} 保持一致。"
            )
            return

        parameter_name = self._setpoint_parameter_name(block_type)
        if not parameter_name:
            print(
                f"[Simulink][WARN] 识别到设定值块 {block_path}，"
                f"但暂不支持自动写入块类型 {block_type}。"
            )
            return

        self._eng.set_param(  # type: ignore[union-attr]
            block_path,
            parameter_name,
            str(self.setpoint),
            nargout=0,
        )
        print(
            f"[Simulink] 已将目标值 {self.setpoint} 同步到 {block_path} "
            f"({parameter_name})."
        )

    def _to_float_scalar(self, value: object) -> float:
        """Convert MATLAB numeric wrappers (including nested iterables) to float."""
        current = value
        while isinstance(current, (list, tuple)):
            if not current:
                return 0.0
            current = current[0]
        try:
            iterator = iter(current)  # type: ignore[arg-type]
        except TypeError:
            return float(current)  # type: ignore[arg-type]
        converted = list(iterator)
        if not converted:
            return 0.0
        return self._to_float_scalar(converted[0])

    def _to_float_series(self, raw_values: object) -> list[float]:
        if raw_values is None:
            return []
        if isinstance(raw_values, (str, bytes)):
            return []
        try:
            values = list(raw_values)  # type: ignore[arg-type]
        except TypeError:
            return [self._to_float_scalar(raw_values)]
        return [self._to_float_scalar(item) for item in values]

    def _resolve_signal_container(self, sim_out: object) -> object:
        """
        Resolve signal output container from different MATLAB versions/configs.

        Supported lookup order:
        1. simOut.<output_signal>
        2. simOut.out.<output_signal>  (common in some MATLAB/Simulink setups)
        """
        direct_signal = self._get_field_or_none(sim_out, self.output_signal)
        if direct_signal is not None:
            return direct_signal

        out_container = self._get_field_or_none(sim_out, "out")
        if out_container is not None:
            nested_signal = self._get_field_or_none(out_container, self.output_signal)
            if nested_signal is not None:
                return nested_signal

        raise RuntimeError(
            f"[SimulinkBridge] 无法在仿真输出中找到信号 '{self.output_signal}'。"
            f"已尝试 simOut.{self.output_signal} 和 simOut.out.{self.output_signal}。"
        )

    def _resolve_time_vector(self, sim_out: object) -> list[float]:
        """Resolve simulation time vector for array-style workspace output."""
        for candidate in ("tout", "time", "Time"):
            raw_time = self._get_field_or_none(sim_out, candidate)
            if raw_time is not None:
                values = self._to_float_series(raw_time)
                if values:
                    return values

        out_container = self._get_field_or_none(sim_out, "out")
        if out_container is not None:
            for candidate in ("tout", "time", "Time"):
                raw_time = self._get_field_or_none(out_container, candidate)
                if raw_time is not None:
                    values = self._to_float_series(raw_time)
                    if values:
                        return values

        return []

    def run_step(self) -> None:
        """
        将仿真推进 sim_step_time 秒，并将输出数据存入 _last_data。

        每次调用后 _last_data 存放本轮采样点列表，
        调用方通过 get_data() 逐条取出。
        """
        if self._eng is None:
            raise RuntimeError(
                "[SimulinkBridge] 未连接 MATLAB Engine，请先调用 connect()。"
            )

        # 用 sim() 同步运行，每轮从 0 开始仿真到 sim_step_time
        self._eng.set_param(self._model_name, "StopTime", str(self.sim_step_time), nargout=0)
        sim_out = self._eng.sim(self._model_name, nargout=1)

        # 从 sim() 返回对象中读取输出信号（兼容不同 MATLAB 版本的层级差异）
        try:
            signal_container = self._resolve_signal_container(sim_out)

            # Case A: Timeseries-like container with Time/Data fields.
            raw_time = self._get_field_or_none(signal_container, "Time")
            raw_output = self._get_field_or_none(signal_container, "Data")
            if raw_time is not None and raw_output is not None:
                time_values = self._to_float_series(raw_time)
                output_values = self._to_float_series(raw_output)
            else:
                # Case B: Array-like container (e.g. out.y_out) with time from tout.
                output_values = self._to_float_series(signal_container)
                time_values = self._resolve_time_vector(sim_out)
                if not time_values:
                    time_values = [float(idx) for idx in range(len(output_values))]
        except Exception as e:
            raise RuntimeError(
                f"[SimulinkBridge] 无法读取输出信号 '{self.output_signal}'：{e}。"
                "请检查 MATLAB_OUTPUT_SIGNAL 配置及 Simulink To Workspace 变量名。"
            ) from e

        # 每轮独立仿真，取全部数据点
        self._current_sim_time = self.sim_step_time
        self._last_data = []
        for t, y in zip(time_values, output_values):
            error = self.setpoint - float(y)
            self._last_data.append({
                "timestamp" : float(t) * 1000.0,  # 转为 ms 与其他模式一致
                "setpoint"  : self.setpoint,
                "input"     : float(y),
                "pwm"       : 0.0,  # Simulink 模型一般不暴露控制量，填 0
                "error"     : error,
                "p"         : self.kp,
                "i"         : self.ki,
                "d"         : self.kd,
            })

    def get_data(self) -> list[dict]:
        """
        返回最近一次 run_step() 采集到的数据点列表。

        每个元素格式与 sim/model.py HeatingSimulator.get_data() 完全一致：
        {
            "timestamp": float (ms),
            "setpoint" : float,
            "input"    : float,
            "pwm"      : float,
            "error"    : float,
            "p"        : float,
            "i"        : float,
            "d"        : float,
        }
        """
        return self._last_data
