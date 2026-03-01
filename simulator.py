#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
simulator.py - 增强版 PID 调参模拟器 (PRO)
===============================================================================

功能：
1. 使用 tuner.py 中的增强逻辑 (History-Aware, CoT, Advanced Metrics)
2. 运行 HeatingSimulator 物理模型
3. 生成对比报告

===============================================================================
"""

import time
import os
import sys
import json
import math
import random
from collections import deque

# ============================================================================
# 配置
# ============================================================================

# LLM 配置
API_BASE_URL = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1")
API_KEY = os.getenv("LLM_API_KEY", "your-api-key-here")
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "gpt-4")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")

BUFFER_SIZE = 100         # 增加缓冲以提供更多上下文
MAX_ROUNDS = 20           # 调参轮数
MIN_ERROR = 0.3           # 目标误差
CONTROL_INTERVAL = 0.2    # 仿真步长 (200ms)

SETPOINT = 200.0          # 目标温度
INITIAL_TEMP = 20.0       # 初始温度
PWM_MAX = 6000            # PWM 上限

# 初始 PID
kp, ki, kd = 1.0, 0.1, 0.05

# 导入增强版调参器组件
try:
    from tuner import LLMTuner, AdvancedDataBuffer, TuningHistory
except ImportError:
    print("[ERROR] 找不到 tuner.py，请确保文件存在")
    sys.exit(1)

# ============================================================================
# 仿真模型 (内置)
# ============================================================================

class HeatingSimulator:
    """加热系统仿真器 (更真实的物理模型)"""
    def __init__(self):
        self.temp = INITIAL_TEMP
        self.pwm = 0
        self.setpoint = SETPOINT
        self.integral = 0.0
        self.prev_error = 0.0
        self.timestamp = 0
        
        # 二阶系统参数
        self.heater_temp = INITIAL_TEMP       # 加热器温度
        self.ambient_temp = INITIAL_TEMP      # 环境温度
        self.heater_coeff = 300.0      # 加热器加热系数
        self.heat_transfer = 0.5       # 加热器到物体的传热系数
        self.cooling_coeff = 0.05      # 向环境散热系数 (略微降低以模拟保温)
        self.noise_level = 0.1         # 传感器噪声
        
    def compute_pid(self):
        """计算 PID 输出"""
        error = self.setpoint - self.temp
        self.integral += error * CONTROL_INTERVAL
        self.integral = max(-500, min(500, self.integral))  # 抗饱和
        derivative = (error - self.prev_error) / CONTROL_INTERVAL
        
        # 使用全局 kp, ki, kd
        pid_output = kp * error + ki * self.integral + kd * derivative
        
        self.pwm = max(0, min(255, pid_output))  # 仿真限制在 0-255
        self.prev_error = error
        
    def update(self):
        """更新温度状态"""
        # 1. 加热器升温
        target_heater_temp = self.ambient_temp + (self.pwm / 255.0) * self.heater_coeff
        self.heater_temp += (target_heater_temp - self.heater_temp) * 0.1 * CONTROL_INTERVAL
        
        # 2. 热传递
        heat_in = (self.heater_temp - self.temp) * self.heat_transfer
        heat_out = (self.temp - self.ambient_temp) * self.cooling_coeff
        
        self.temp += (heat_in - heat_out) * CONTROL_INTERVAL
        
        # 3. 噪声
        self.temp += random.gauss(0, self.noise_level)
        self.timestamp += int(CONTROL_INTERVAL * 1000)
        
    def get_data(self):
        return {
            "timestamp": self.timestamp,
            "setpoint": self.setpoint,
            "input": self.temp,
            "pwm": self.pwm,
            "error": self.setpoint - self.temp,
            "p": kp,
            "i": ki,
            "d": kd
        }

# ============================================================================
# 模拟主程序
# ============================================================================

def run_simulation():
    global kp, ki, kd
    
    print("="*60)
    print("  LLM PID Tuner PRO - 仿真测试")
    print("="*60)
    print(f"目标: {SETPOINT}, 模型: {MODEL_NAME}")
    
    # 初始化组件
    sim = HeatingSimulator()
    tuner = LLMTuner(API_KEY, API_BASE_URL, MODEL_NAME, LLM_PROVIDER)
    buffer = AdvancedDataBuffer(max_size=BUFFER_SIZE)
    history = TuningHistory(max_history=5)
    
    round_num = 0
    start_time = time.time()
    
    # 设置初始 PID 到 buffer
    buffer.current_pid = {"p": kp, "i": ki, "d": kd}
    buffer.setpoint = SETPOINT
    
    try:
        while round_num < MAX_ROUNDS:
            # 1. 运行仿真并采集数据
            sim_steps = 0
            print(f"\n[第 {round_num + 1} 轮] 数据采集中...", end="")
            
            # 采集 BUFFER_SIZE 个数据点
            while not buffer.is_full():
                sim.compute_pid()
                sim.update()
                data = sim.get_data()
                buffer.add(data)
                sim_steps += 1
            
            print(f" 完成 ({sim_steps} 步)")
            
            # 2. 计算指标
            metrics = buffer.calculate_advanced_metrics()
            print(f"  当前状态: AvgErr={metrics['avg_error']:.2f}, MaxErr={metrics['max_error']:.2f}, "
                  f"Overshoot={metrics['overshoot']:.1f}%, Status={metrics['status']}")
            
            # 检查是否达标
            if metrics['avg_error'] < MIN_ERROR and metrics['status'] == "STABLE":
                print("\n[SUCCESS] 调参成功！系统已稳定。")
                break
            
            round_num += 1
            
            # 3. 准备 Prompt
            prompt_data = buffer.to_prompt_data()
            history_text = history.to_prompt_text()
            
            # 4. 调用 LLM
            print("  [LLM] 正在思考...")
            result = tuner.analyze(prompt_data, history_text)
            
            if result:
                analysis = result.get('analysis_summary', '无分析')
                thought = result.get('thought_process', '无思考过程')
                action = result.get('tuning_action', 'UNKNOWN')
                
                print(f"  [思考] {thought[:100]}...")
                print(f"  [分析] {analysis}")
                
                # 更新参数
                old_p, old_i, old_d = kp, ki, kd
                kp = float(result.get('p', kp))
                ki = float(result.get('i', ki))
                kd = float(result.get('d', kd))
                
                print(f"  [动作] {action}: P {old_p:.4f}->{kp:.4f}, I {old_i:.4f}->{ki:.4f}, D {old_d:.4f}->{kd:.4f}")
                
                # 记录历史
                history.add_record(round_num, {"p": kp, "i": ki, "d": kd}, metrics, analysis)
                buffer.current_pid = {"p": kp, "i": ki, "d": kd}
                
                if result.get('status') == "DONE":
                    print("\n[LLM] 认为调参已完成。")
                    break
            else:
                print("  [ERROR] LLM 调用失败")
            
            # 清空缓冲，准备下一轮
            buffer.buffer.clear()
            
            # 注意：不重置仿真器状态 (sim.reset())，因为我们要模拟连续调参过程
    
    except KeyboardInterrupt:
        print("\n用户中断")
    
    end_time = time.time()
    print(f"\n测试结束，耗时 {end_time - start_time:.1f} 秒")
    print(f"最终参数: P={kp}, I={ki}, D={kd}")

if __name__ == "__main__":
    run_simulation()
