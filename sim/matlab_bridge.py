#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sim/matlab_bridge.py - MATLAB/Simulink 仿真桥接层

通过 MATLAB Engine API for Python 与 Simulink 模型双向通信。
返回数据格式与 sim/model.py 的 HeatingSimulator.get_data() 完全一致，
保证上层调参逻辑（matlab_tuner.py）可零修改复用。

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


class MatlabBridge:
    """
    MATLAB/Simulink 仿真桥接层。

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
        model_path: str,
        setpoint: float,
        pid_block_path: str,
        output_signal: str,
        sim_step_time: float = 10.0,
    ) -> None:
        if not _MATLAB_AVAILABLE:
            raise ImportError(
                "[MatlabBridge] 未找到 matlabengine 包。\n"
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

        self._eng: Optional[object] = None
        self._model_name: str = ""
        self._current_sim_time: float = 0.0
        self._last_data: list[dict] = []

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """启动 MATLAB Engine 并加载 Simulink 模型。"""
        print("[MATLAB] 正在启动 MATLAB Engine，请稍候...")
        self._eng = matlab.engine.start_matlab()

        # 提取模型名（不含路径和扩展名）
        import os
        self._model_name = os.path.splitext(os.path.basename(self.model_path))[0]
        model_dir = os.path.dirname(os.path.abspath(self.model_path))

        # 将模型目录加入 MATLAB 路径并加载模型
        self._eng.addpath(model_dir, nargout=0)
        self._eng.load_system(self.model_path, nargout=0)
        print(f"[MATLAB] 模型已加载: {self._model_name}")

        # 设置仿真模式为外部控制（逐步推进）
        self._eng.set_param(self._model_name, "SimulationMode", "normal", nargout=0)
        self._current_sim_time = 0.0

    def disconnect(self) -> None:
        """关闭 Simulink 模型并退出 MATLAB Engine。"""
        if self._eng is not None:
            try:
                self._eng.close_system(self._model_name, nargout=0)
            except Exception:
                pass
            self._eng.quit()
            self._eng = None
            print("[MATLAB] Engine 已关闭。")

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

    def run_step(self) -> None:
        """
        将仿真推进 sim_step_time 秒，并将输出数据存入 _last_data。

        每次调用后 _last_data 存放本轮采样点列表，
        调用方通过 get_data() 逐条取出。
        """
        next_time = self._current_sim_time + self.sim_step_time

        self._eng.set_param(
            self._model_name, "StopTime", str(next_time), nargout=0
        )
        self._eng.set_param(
            self._model_name,
            "SimulationCommand",
            "start" if self._current_sim_time == 0.0 else "continue",
            nargout=0,
        )

        # 等待仿真完成
        status = ""
        while status != "stopped" and status != "terminating":
            status = str(
                self._eng.get_param(self._model_name, "SimulationStatus")
            )
            time.sleep(0.05)

        # 从工作区读取输出信号（To Workspace，格式 Array）
        raw_output = self._eng.workspace[self.output_signal]  # type: ignore
        # raw_output 是 matlab.double，转为 Python list
        output_values = list(raw_output)

        # 时间轴：MATLAB 默认将时间存为 tout
        try:
            raw_time = self._eng.workspace["tout"]  # type: ignore
            time_values = list(raw_time)
        except Exception:
            time_values = [
                self._current_sim_time + i * (self.sim_step_time / max(len(output_values), 1))
                for i in range(len(output_values))
            ]

        self._current_sim_time = next_time

        # 只取本轮新增的数据点（截取最后 sim_step_time 时间段内的点）
        step_start = next_time - self.sim_step_time
        self._last_data = []
        for t, y in zip(time_values, output_values):
            if float(t) >= step_start:
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
