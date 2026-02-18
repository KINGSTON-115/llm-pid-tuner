#!/usr/bin/env python3
"""
PID 系统辨识工具 - 硬件版
================

功能：
1. 从串口读取真实温度数据
2. 阶跃响应分析 - 从温度曲线识别系统参数
3. 传递函数计算 - 估算系统的传递函数
4. 零极点分析 - 分析系统动态特性
5. 稳定性判断 - 基于参数判断系统稳定性
6. Z-N 整定建议 - 根据系统特性计算初始 PID

使用方法：
    # 从串口实时读取数据进行辨识
    python system_id.py --mode live --port /dev/ttyUSB0
    
    # 从文件读取历史数据
    python system_id.py --mode file --data "data.csv"
    
    # 模拟分析
    python system_id.py --mode demo
"""

import serial
import math
import argparse
import time
from typing import List, Tuple, Dict, Optional
from collections import deque


def parse_csv_line(line: str) -> Optional[Dict]:
    """解析串口 CSV 数据"""
    try:
        parts = line.strip().split(',')
        if len(parts) >= 4:
            return {
                'timestamp': float(parts[0]),
                'setpoint': float(parts[1]),
                'input': float(parts[2]),  # 温度
                'pwm': float(parts[3]),
                'error': float(parts[4]) if len(parts) > 4 else 0
            }
    except:
        pass
    return None


def first_order_model(tau: float, K: float, theta: float = 0) -> Dict:
    """一阶滞后系统模型"""
    return {
        "type": "一阶滞后系统",
        "formula": f"G(s) = {K:.4f} * e^(-{theta:.4f}s) / ({tau:.4f}s + 1)",
        "K": K,
        "tau": tau,
        "theta": theta,
        "poles": [-1/tau] if tau > 0 else [],
        "zeros": [],
    }


def analyze_stability(poles: List) -> Dict:
    """分析系统稳定性"""
    unstable = 0
    for p in poles:
        if isinstance(p, complex):
            if p.real >= 0:
                unstable += 1
        else:
            if p >= 0:
                unstable += 1
    
    return {
        "stable": unstable == 0,
        "reason": "稳定" if unstable == 0 else f"有{unstable}个不稳定极点"
    }


def ziegler_nichols(K: float, tau: float, theta: float, pid_type: str = "PID") -> Dict:
    """Ziegler-Nichols PID 整定"""
    gains = {
        "P": (tau / (K * theta), 0, 0),
        "PI": (0.9 * tau / (K * theta), 0.9 * tau / K, 0),
        "PD": (1.2 * tau / (K * theta), 0, 0.6 * tau / K),
        "PID": (1.2 * tau / (K * theta), 0.6 * tau / K, 0.6 * tau * theta / K),
    }
    
    Kp, Ki, Kd = gains.get(pid_type, gains["PID"])
    
    return {
        "type": pid_type,
        "Kp": Kp,
        "Ki": Ki,
        "Kd": Kd,
        "formula": f"Kp={Kp:.3f}, Ki={Ki:.3f}, Kd={Kd:.3f}"
    }


def system_identify(time_data: List[float], temp_data: List[float], 
                   pwm_data: List[float] = None) -> Dict:
    """
    系统辨识主函数 - 从阶跃响应数据识别系统参数
    
    Args:
        time_data: 时间序列 (秒)
        temp_data: 温度序列 (°C)
        pwm_data: PWM 输入序列 (可选)
    
    Returns:
        系统参数字典
    """
    n = len(time_data)
    if n < 5:
        return {"error": "数据点太少，至少需要5个"}
    
    # 1. 计算稳态增益 K
    steady_temp = sum(temp_data[-10:]) / min(10, n)
    initial_temp = temp_data[0]
    delta_temp = steady_temp - initial_temp
    
    # 根据 PWM 计算增益 (假设 PWM 变化)
    if pwm_data:
        avg_pwm = sum(pwm_data[-10:]) / min(10, n)
        initial_pwm = pwm_data[0]
        delta_pwm = avg_pwm - initial_pwm
        if delta_pwm > 0:
            K = delta_temp / delta_pwm
        else:
            K = delta_temp / 255.0  # 假设满 PWM
    else:
        K = delta_temp / 255.0  # 默认假设满 PWM
    
    # 2. 计算时间常数 tau (达到 63.2% 稳态的时间)
    target_63 = initial_temp + delta_temp * 0.632
    tau = time_data[-1] - time_data[0]  # 默认用总时长
    
    for i, temp in enumerate(temp_data):
        if temp >= target_63:
            tau = time_data[i] - time_data[0]
            break
    
    # 3. 估算延迟 theta (达到 5% 稳态的时间)
    target_5 = initial_temp + delta_temp * 0.05
    theta = 0
    for i, temp in enumerate(temp_data):
        if temp > target_5:
            theta = time_data[i] - time_data[0]
            break
    
    # 4. 构建模型
    model = first_order_model(tau, K, theta)
    
    # 5. 稳定性分析
    stability = analyze_stability(model["poles"])
    
    # 6. Z-N 整定建议
    if theta > 0 and tau > 0 and K > 0:
        znpid = ziegler_nichols(K, tau, theta, "PID")
        znpi = ziegler_nichols(K, tau, theta, "PI")
    else:
        znpid = {"error": "参数异常，无法计算"}
        znpi = {"error": "参数异常，无法计算"}
    
    return {
        "model": model,
        "stability": stability,
        "ziegler_nichols": {"PID": znpid, "PI": znpi},
        "summary": {
            "gain_K": K,
            "time_constant_tau": tau,
            "delay_theta": theta,
            "steady_temp": steady_temp,
            "initial_temp": initial_temp,
            "temp_rise": delta_temp
        }
    }


def print_report(result: Dict):
    """打印分析报告"""
    if "error" in result:
        print(f"❌ 错误: {result['error']}")
        return
    
    m = result["model"]
    s = result["summary"]
    znpid = result["ziegler_nichols"]["PID"]
    znpi = result["ziegler_nichols"]["PI"]
    
    print("\n" + "="*60)
    print("               🔧 系统辨识报告")
    print("="*60)
    
    print(f"\n📊 系统参数:")
    print(f"   初始温度: {s['initial_temp']:.1f}°C")
    print(f"   稳态温度: {s['steady_temp']:.1f}°C")
    print(f"   温升: {s['temp_rise']:.1f}°C")
    print(f"   增益 K: {s['gain_K']:.4f}°C/PWM")
    print(f"   时间常数 τ: {s['time_constant_tau']:.4f}秒")
    print(f"   延迟 θ: {s['delay_theta']:.4f}秒")
    
    print(f"\n🔄 传递函数:")
    print(f"   {m['formula']}")
    
    print(f"\n📍 极点: {m['poles']}")
    print(f"   零点: {m['zeros']}")
    
    st = result["stability"]
    print(f"\n✅ 稳定性: {'✓ 稳定' if st['stable'] else '✗ 不稳定'} ({st['reason']})")
    
    if "error" not in znpid:
        print(f"\n🎯 Ziegler-Nichols 整定建议:")
        print(f"   PID: Kp={znpid['Kp']:.3f}, Ki={znpid['Ki']:.3f}, Kd={znpid['Kd']:.3f}")
        print(f"   PI:  Kp={znpi['Kp']:.3f}, Ki={znpi['Ki']:.3f}")
    
    print("\n" + "="*60)


def read_from_serial(port: str, baud: int = 115200, duration: float = 10.0) -> Dict:
    """
    从串口读取数据进行系统辨识
    
    Args:
        port: 串口名称
        baud: 波特率
        duration: 读取时长(秒)
    
    Returns:
        辨识结果
    """
    print(f"🔌 正在连接串口 {port} @ {baud} baud...")
    
    try:
        ser = serial.Serial(port, baud, timeout=1)
        time.sleep(2)  # 等待连接稳定
        
        time_data = []
        temp_data = []
        pwm_data = []
        start_time = None
        
        print(f"📡 开始读取数据 (时长: {duration}秒)...")
        print("   按 Ctrl+C 提前停止")
        
        while time.time() - (start_time or time.time()) < duration:
            try:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    data = parse_csv_line(line)
                    if data and data['input'] > 0:
                        if start_time is None:
                            start_time = time.time()
                        
                        elapsed = time.time() - start_time
                        time_data.append(elapsed)
                        temp_data.append(data['input'])
                        pwm_data.append(data['pwm'])
                        
                        # 实时显示
                        print(f"\r   t={elapsed:.1f}s T={data['input']:.1f}°C PWM={data['pwm']:.0f}", end='')
                        
            except Exception as e:
                continue
        
        ser.close()
        print("\n\n✅ 数据读取完成")
        
        if len(time_data) < 10:
            return {"error": f"数据点太少 ({len(time_data)})，至少需要10个"}
        
        return system_identify(time_data, temp_data, pwm_data)
        
    except serial.SerialException as e:
        return {"error": f"串口错误: {e}"}


def demo():
    """演示模式"""
    # 模拟真实硬件数据 (时间秒, 温度°C, PWM)
    time_data = [0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
    temp_data = [25.0, 25.5, 28.0, 35.0, 45.0, 55.0, 65.0, 73.0, 80.0, 85.0, 88.0]
    pwm_data = [0, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255]
    
    print("� demo 模式：分析模拟数据")
    result = system_identify(time_data, temp_data, pwm_data)
    print_report(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PID 系统辨识工具 - 硬件版")
    parser.add_argument("--mode", choices=["demo", "live", "file", "stdin"], default="demo",
                      help="模式: demo=演示, live=串口, file=文件, stdin=标准输入")
    parser.add_argument("--port", type=str, default="/dev/ttyUSB0",
                      help="串口名称")
    parser.add_argument("--baud", type=int, default=115200,
                      help="波特率")
    parser.add_argument("--duration", type=float, default=10.0,
                      help="读取时长(秒)")
    parser.add_argument("--data", type=str,
                      help="内联数据: 时间,温度,PWM 空格分隔")
    
    args = parser.parse_args()
    
    if args.data:
        # 内联数据模式
        time_data = []
        temp_data = []
        pwm_data = []
        for item in args.data.strip().split():
            if ',' in item:
                parts = item.split(',')
                if len(parts) >= 3:
                    time_data.append(float(parts[0]) / 1000)  # ms -> s
                    temp_data.append(float(parts[1]))
                    pwm_data.append(float(parts[2]))
        
        if len(time_data) >= 5:
            result = system_identify(time_data, temp_data, pwm_data)
            print_report(result)
        else:
            print("数据点太少，至少需要5个")
    elif args.mode == "demo":
        demo()
    elif args.mode == "live":
        result = read_from_serial(args.port, args.baud, args.duration)
        print_report(result)
    elif args.mode == "file":
        # TODO: 从文件读取
        print("📁 文件模式开发中...")
    elif args.mode == "stdin":
        # 从 stdin 读取数据
        import sys
        data_str = sys.stdin.read().strip()
        if data_str:
            time_data = []
            temp_data = []
            pwm_data = []
            for item in data_str.split():
                if ',' in item:
                    parts = item.split(',')
                    if len(parts) >= 3:
                        time_data.append(float(parts[0]) / 1000)
                        temp_data.append(float(parts[1]))
                        pwm_data.append(float(parts[2]))
            
            if len(time_data) >= 5:
                result = system_identify(time_data, temp_data, pwm_data)
                print_report(result)
            else:
                print("数据点太少，至少需要5个")
