#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import os
sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buffering=1)  # 行缓冲

"""
===============================================================================
simulator.py - PID 调参模拟器 (调用 MiniMax M2.5)

用于本地模拟调参实验，调用 MiniMax M2.5 模型分析 PID 参数

===============================================================================
"""

import time
import json
import requests
import sys
from collections import deque

# ============================================================================
# MiniMax M2.5 API 配置
# ============================================================================

API_BASE_URL = "http://115.190.127.51:19882/v1"
API_KEY = "sk-cyWHsMGgfUWm4FGxBWj8wxYKXfjMTPzT7T0rKPd8X2ac3XPS"
MODEL_NAME = "MiniMax-M2.5"

# ============================================================================
# 配置
# ============================================================================

SETPOINT = 100.0          # 目标温度
INITIAL_TEMP = 0.0        # 初始温度
BUFFER_SIZE = 20           # 数据缓冲大小 (减少到20行，提高调参效率)
MAX_ROUNDS = 20           # 最大调参轮数
CONTROL_INTERVAL = 0.05   # 控制周期 (50ms)

# PID 初始参数
kp, ki, kd = 1.0, 0.1, 0.05

# ============================================================================
# 仿真模型
# ============================================================================

class HeatingSimulator:
    """加热系统仿真器"""
    def __init__(self):
        self.temp = INITIAL_TEMP
        self.pwm = 0
        self.setpoint = SETPOINT
        self.integral = 0.0
        self.prev_error = 0.0
        self.timestamp = 0
        
        # 仿真参数 - 更真实的加热系统模型
        # 二阶系统：加热器温度 + 环境温度
        self.heater_temp = 20.0       # 加热器温度 (PWM 加热)
        self.ambient_temp = 20.0       # 环境温度
        self.heater_coeff = 300.0      # 加热器加热系数 (最高可达 320°C)
        self.heat_transfer = 0.5       # 加热器到物体的传热系数
        self.cooling_coeff = 0.3       # 向环境散热系数
        self.noise_level = 0.3         # 传感器噪声
        
    def compute_pid(self):
        """计算 PID 输出"""
        error = self.setpoint - self.temp
        self.integral += error * CONTROL_INTERVAL
        self.integral = max(-200, min(200, self.integral))  # 抗饱和
        derivative = (error - self.prev_error) / CONTROL_INTERVAL
        
        pid_output = kp * error + ki * self.integral + kd * derivative
        self.pwm = max(0, min(255, pid_output))
        self.prev_error = error
        
    def update(self):
        """更新温度 - 更真实的物理模型"""
        import random
        
        # 1. 加热器温度由 PWM 决定 (更高功率)
        target_heater_temp = self.ambient_temp + (self.pwm / 255.0) * self.heater_coeff
        self.heater_temp += (target_heater_temp - self.heater_temp) * 0.3  # 加热器热惯性
        
        # 2. 物体温度变化：吸收加热器热量 - 向环境散热
        # 热流从加热器到物体
        heat_from_heater = (self.heater_temp - self.temp) * self.heat_transfer
        # 热流向环境散失
        heat_to_ambient = (self.temp - self.ambient_temp) * self.cooling_coeff
        
        # 净热量
        net_heat = heat_from_heater - heat_to_ambient
        
        # 温度变化 = 净热量 / 热容
        self.temp += net_heat * CONTROL_INTERVAL
        
        # 3. 添加传感器噪声
        noise = random.gauss(0, self.noise_level)
        self.temp += noise
        
        self.timestamp += int(CONTROL_INTERVAL * 1000)
        
    def get_data(self):
        """获取当前数据"""
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
    
    def reset(self):
        """重置仿真状态"""
        self.temp = INITIAL_TEMP
        self.heater_temp = 20.0
        self.pwm = 0
        self.integral = 0.0
        self.prev_error = 0.0
        self.timestamp = 0


# ============================================================================
# MiniMax API 调用
# ============================================================================

def call_llm(data_text: str) -> dict:
    """调用 MiniMax M2.5 API 分析 PID 数据"""
    
    prompt = f"""你是一个 PID 控制算法专家。请分析以下温度控制系统数据，判断当前 PID 参数表现并给出优化建议。

## 规则
- 震荡剧烈 → 减小 Kp 或增大 Kd
- 响应太慢 → 增大 Kp
- 稳态误差 → 增大 Ki
- 超调过大 → 减小 Kp 或增大 Kd

## 数据
{data_text}

请直接返回 JSON 格式 (不要有任何其他文字):
{{"analysis": "简短分析", "p": 数值, "i": 数值, "d": 数值, "status": "TUNING" 或 "DONE"}}"""

    print("\n[MiniMax] 调用 API 中...")
    
    try:
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3,
            "max_tokens": 500
        }
        
        resp = requests.post(
            f"{API_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60
        )
        
        if resp.status_code != 200:
            print(f"[错误] API 调用失败: {resp.status_code} - {resp.text[:200]}")
            return None
        
        result_text = resp.json()["choices"][0]["message"]["content"]
        print(f"[MiniMax] 原始返回: {result_text[:200]}...")
        
        # 尝试多种方式解析 JSON
        import re
        
        # 方法1: 提取 JSON 块
        json_match = re.search(r'\{[^{}]*\}', result_text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError as e:
                print(f"[解析] JSON 块解析失败: {e}")
        
        # 方法2: 尝试直接解析
        try:
            return json.loads(result_text)
        except json.JSONDecodeError:
            pass
        
        # 方法3: 尝试提取 key-value 对
        p_match = re.search(r'"p"\s*:\s*([0-9.]+)', result_text)
        i_match = re.search(r'"i"\s*:\s*([0-9.]+)', result_text)
        d_match = re.search(r'"d"\s*:\s*([0-9.]+)', result_text)
        
        if p_match and i_match and d_match:
            analysis_match = re.search(r'"analysis"\s*:\s*"([^"]*)"', result_text)
            status_match = re.search(r'"status"\s*:\s*"([^"]*)"', result_text)
            
            return {
                "analysis": analysis_match.group(1) if analysis_match else "分析",
                "p": float(p_match.group(1)),
                "i": float(i_match.group(1)),
                "d": float(d_match.group(1)),
                "status": status_match.group(1) if status_match else "TUNING"
            }
        
        print(f"[错误] 无法解析返回内容")
        return None
        
    except Exception as e:
        print(f"[错误] {e}")
        return None


# ============================================================================
# 数据缓冲
# ============================================================================

buffer = deque(maxlen=BUFFER_SIZE)
sim = HeatingSimulator()


# ============================================================================
# 模拟主循环
# ============================================================================

def collect_data():
    """收集仿真数据"""
    global kp, ki, kd
    
    print("\n" + "="*60)
    print(f"开始采集数据 (目标温度: {SETPOINT}°C, 初始温度: {INITIAL_TEMP}°C)")
    print("="*60)
    
    for i in range(BUFFER_SIZE):
        sim.compute_pid()
        sim.update()
        data = sim.get_data()
        buffer.append(data)
        
        if i % 10 == 0:
            print(f"[数据] t={data['timestamp']}ms T={data['input']:.1f}°C PWM={data['pwm']:.1f} Error={data['error']:+.1f}")
        
        time.sleep(CONTROL_INTERVAL)


def calculate_metrics():
    """计算指标"""
    if not buffer:
        return {}
    
    errors = [abs(d['error']) for d in buffer]
    return {
        'avg_error': sum(errors) / len(errors),
        'max_error': max(errors),
        'latest_temp': buffer[-1]['input']
    }


def run_tuning():
    """运行调参主循环"""
    global kp, ki, kd
    
    start_time = time.time()
    rounds = 0
    tuning_log = []  # 记录调参过程
    
    print("\n" + "="*60)
    print("开始 PID 自动调参实验 (MiniMax M2.5)")
    print("="*60)
    
    while rounds < MAX_ROUNDS:
        rounds += 1
        
        # 1. 收集数据
        collect_data()
        
        # 2. 计算指标
        metrics = calculate_metrics()
        print(f"\n[第 {rounds} 轮] 平均误差: {metrics['avg_error']:.2f}°C, 最大误差: {metrics['max_error']:.2f}°C")
        
        # 3. 检查是否完成
        if metrics['avg_error'] < 0.5:
            print("\n✅ 调参完成！误差已达到目标范围")
            break
        
        # 4. 准备数据并调用 LLM
        recent = list(buffer)[-15:]
        
        data_text = f"""当前 PID 参数: P={kp}, I={ki}, D={kd}
目标温度: {SETPOINT}°C
当前温度: {recent[-1]['input']:.2f}°C
平均误差: {metrics['avg_error']:.2f}°C
最大误差: {metrics['max_error']:.2f}°C

最近 15 条数据 (timestamp, setpoint, input, pwm, error):"""
        
        for d in recent:
            data_text += f"\n{d['timestamp']}, {d['setpoint']:.1f}, {d['input']:.2f}, {d['pwm']:.1f}, {d['error']:+.2f}"
        
        # 5. 调用 MiniMax API
        result = call_llm(data_text)
        
        if result:
            analysis = result.get('analysis', '无')
            new_p = result.get('p', kp)
            new_i = result.get('i', ki)
            new_d = result.get('d', kd)
            status = result.get('status', 'TUNING')
            
            print(f"[MiniMax] 分析: {analysis}")
            print(f"[MiniMax] 新参数: P={new_p}, I={new_i}, D={new_d}")
            
            # 记录调参过程
            tuning_log.append({
                "round": rounds,
                "old_pid": {"p": kp, "i": ki, "d": kd},
                "new_pid": {"p": new_p, "i": new_i, "d": new_d},
                "analysis": analysis,
                "avg_error": metrics['avg_error'],
                "status": status
            })
            
            # 更新参数
            kp, ki, kd = new_p, new_i, new_d
            
            if status == 'DONE':
                print("\n✅ MiniMax 标记调参完成")
                break
        else:
            print("[警告] LLM 分析失败，继续当前参数")
        
        # 清空缓冲
        buffer.clear()
        
        # 重置仿真以观察新参数效果
        sim.reset()
    
    end_time = time.time()
    duration = end_time - start_time
    
    # 最终评估
    # 再跑一次获取最终指标
    for _ in range(BUFFER_SIZE):
        sim.compute_pid()
        sim.update()
        buffer.append(sim.get_data())
    
    final_metrics = calculate_metrics()
    
    return {
        "duration": duration,
        "rounds": rounds,
        "initial_pid": {"p": 1.0, "i": 0.1, "d": 0.05},
        "final_pid": {"p": kp, "i": ki, "d": kd},
        "final_temp": final_metrics['latest_temp'],
        "final_avg_error": final_metrics['avg_error'],
        "final_max_error": final_metrics['max_error'],
        "tuning_log": tuning_log
    }


def print_report(report: dict):
    """打印详细测试报告"""
    print("\n" + "="*70)
    print("                    PID 自动调参实验报告")
    print("="*70)
    
    print(f"\n📊 实验概况:")
    print(f"   目标温度: {SETPOINT}°C")
    print(f"   初始温度: {INITIAL_TEMP}°C")
    print(f"   模型: {MODEL_NAME}")
    
    print(f"\n⏱️  时间统计:")
    print(f"   总耗时: {report['duration']:.1f} 秒")
    print(f"   调参轮数: {report['rounds']} 轮")
    
    print(f"\n🔧 参数变化:")
    print(f"   初始参数: P={report['initial_pid']['p']}, I={report['initial_pid']['i']}, D={report['initial_pid']['d']}")
    print(f"   最终参数: P={report['final_pid']['p']:.4f}, I={report['final_pid']['i']:.4f}, D={report['final_pid']['d']:.4f}")
    
    print(f"\n📈 控制效果:")
    print(f"   最终温度: {report['final_temp']:.2f}°C (目标: {SETPOINT}°C)")
    print(f"   平均误差: {report['final_avg_error']:.2f}°C")
    print(f"   最大误差: {report['final_max_error']:.2f}°C")
    
    print(f"\n📝 调参过程:")
    for log in report['tuning_log']:
        print(f"   第{log['round']:2d}轮: {log['old_pid']} → {log['new_pid']} | 分析: {log['analysis']} | 误差: {log['avg_error']:.1f}°C")
    
    print("\n" + "="*70)


if __name__ == "__main__":
    report = run_tuning()
    print_report(report)
