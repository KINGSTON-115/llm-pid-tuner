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
# LLM API 配置
# ============================================================================

# 示例：MiniMax 国内节点
API_BASE_URL = "http://115.190.127.51:19882/v1"  # 替换为你的 API 地址
# 示例：OpenAI
# API_BASE_URL = "https://api.openai.com/v1"
API_KEY = "sk-cyWHsMGgfUWm4FGxBWj8wxYKXfjMTPzT7T0rKPd8X2ac3XPS"
MODEL_NAME = "MiniMax-M2.5"

# ============================================================================
# 配置
# ============================================================================

SETPOINT = 200.0          # 目标温度
INITIAL_TEMP = 0.0        # 初始温度
BUFFER_SIZE = 25           # 数据缓冲大小 (平衡速度和准确性)
MAX_ROUNDS = 30           # 最大调参轮数
CONTROL_INTERVAL = 0.05   # 控制周期 (50ms)

# PWM 输出限制 (电机安全阈值)
# 仿真模式用 255，温度控制用
# 电机模式用 6000 (满转10000)
PWM_MAX = 6000            # 电机安全上限
PWM_CHANGE_MAX = 500      # 每周期最大 PWM 变化 (防突变)

# 保守模式 (减少超调)
CONSERVATIVE_MODE = True   # True: 用 PI + 0.5倍增益 | False: 激进 PID
Z_N_GAIN_FACTOR = 0.5      # Z-N 增益折扣 (保守模式用 0.5)

# PID 初始参数
kp, ki, kd = 1.0, 0.1, 0.05

# ============================================================================
# PID 公式模板选择
# ============================================================================
# 可选值: "standard" | "parallel" | "positional" | "velocity" | "incremental" | "custom"
# ============================================================================

PID_FORMULA = "standard"

# ============================================================================
# PID 公式模板 (用户可自定义)
# ============================================================================

# 标准 PID (位置式): u(t) = Kp*e + Ki*∫e + Kd*de/dt
PID_TEMPLATES = {
    "standard": {
        "name": "标准位置式 PID",
        "formula": "kp * error + ki * integral + kd * derivative",
        "description": "最常用的 PID 形式，直接输出控制量"
    },
    "parallel": {
        "name": "并行 PID", 
        "formula": "kp * error + ki * integral + kd * derivative",
        "description": "与标准 PID 等价，各参数独立调节"
    },
    "positional": {
        "name": "位置式 PID",
        "formula": "kp * error + ki * integral + kd * derivative",
        "description": "输出绝对控制量，需注意积分饱和"
    },
    "velocity": {
        "name": "速度式 PID",
        "formula": "pid_output = kp * (error - prev_error) + ki * error + kd * (error - 2*prev_error + prev_prev_error)",
        "description": "输出控制量的变化率"
    },
    "incremental": {
        "name": "增量式 PID",
        "formula": "delta_u = kp * (error - prev_error) + ki * error + kd * (error - 2*prev_error + prev_prev_error); pid_output += delta_u",
        "description": "计算控制增量，适用于步进电机等"
    },
    "custom": {
        "name": "自定义 PID",
        "formula": "kp * error + ki * integral + kd * derivative",  # 修改这里定义你的公式
        "description": "用户自定义公式"
    }
}

# 自定义公式 (当 PID_FORMULA = "custom" 时使用)
# 可用变量: error, integral, derivative, prev_error, prev_prev_error, kp, ki, kd
# 示例: "kp * error + ki * integral + kd * derivative"  (标准)
# 示例: "kp * error + kd * derivative"  (PD 控制器)
# 示例: "kp * error + ki * integral"  (PI 控制器)
CUSTOM_PID_FORMULA = "kp * error + ki * integral + kd * derivative"

# 全局变量
system_id_output = ""  # 系统辨识结果

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
        
        # PID 历史值 (用于增量式/速度式)
        self.prev_prev_error = 0.0
        self.last_pid_output = 0.0
    
    def compute_pid(self):
        """计算 PID 输出 - 支持多种公式"""
        error = self.setpoint - self.temp
        self.integral += error * CONTROL_INTERVAL
        self.integral = max(-200, min(200, self.integral))  # 抗饱和
        derivative = (error - self.prev_error) / CONTROL_INTERVAL
        
        # 根据配置的公式计算 PID 输出
        if PID_FORMULA == "incremental":
            # 增量式 PID
            delta_u = (kp * (error - self.prev_error) + 
                      ki * error * CONTROL_INTERVAL + 
                      kd * (error - 2*self.prev_error + self.prev_prev_error) / CONTROL_INTERVAL)
            self.last_pid_output += delta_u
            pid_output = self.last_pid_output
        elif PID_FORMULA == "velocity":
            # 速度式 PID
            pid_output = (kp * (error - self.prev_error) + 
                         ki * error + 
                         kd * (error - 2*self.prev_error + self.prev_prev_error) / CONTROL_INTERVAL)
        else:
            # 标准/位置式 PID (default)
            pid_output = kp * error + ki * self.integral + kd * derivative
        
        # PWM 变化率限制 (防突变)
        if hasattr(self, 'prev_pwm'):
            pwm_delta = pid_output - self.prev_pwm
            if abs(pwm_delta) > PWM_CHANGE_MAX:
                pid_output = self.prev_pwm + (PWM_CHANGE_MAX if pwm_delta > 0 else -PWM_CHANGE_MAX)
        
        self.pwm = max(0, min(PWM_MAX, pid_output))  # PWM_MAX=6000 for motor, 255 for sim
        self.prev_pwm = self.pwm
        
        # 更新历史值
        self.prev_prev_error = self.prev_error
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

def call_llm(data_text: str, rounds: int = 1, metrics: dict = None) -> dict:
    """调用 MiniMax M2.5 API 分析 PID 数据"""
    
    # 检查是否需要调用系统辨识
    use_system_id = (rounds <= 3 and metrics.get('avg_error', 0) > 50)
    
    global system_id_output
    system_id_info = ""
    system_id_output = ""
    if use_system_id:
        # 自动执行系统辨识
        print("\n[系统] 误差较大，自动执行系统辨识...")
        import subprocess
        try:
            # 准备数据：time,temp,pwm 格式
            data_str = ""
            for d in buffer:
                data_str += f"{int(d['timestamp'])},{d['input']:.1f},{d['pwm']:.0f} "
            
            result = subprocess.run(
                ['python3', 'system_id.py', '--data', data_str],
                capture_output=True, text=True, timeout=30,
                cwd='/home/KINGSTON/.openclaw/workspace/llm-pid-tuner'
            )
            system_id_output = result.stdout
            if result.stderr:
                system_id_output += f"\n错误: {result.stderr}"
            print(f"[系统辨识] 结果:\n{system_id_output[:500]}")
            system_id_info = f"\n\n## 系统辨识结果\n{system_id_output}\n\n请根据系统辨识的Z-N建议来调整PID参数。"
        except Exception as e:
            print(f"[系统辨识] 错误: {e}")
    
    prompt = f"""你是一个 PID 控制算法专家。请分析以下温度控制系统数据，判断当前 PID 参数表现并给出优化建议。

## 重要约束
- **禁止超调**：严禁让温度超过目标值，一旦发现超调必须立即减小 Kp 和增大 Kd
- **稳态优先**：优先消除稳态误差，再考虑响应速度
- **请直接给出参数**：不要建议我运行其他脚本，直接根据你的经验给出新的 PID 参数
- **输出格式**：只输出纯 JSON，不要有任何 Markdown 标记或解释文字

## 规则
- 震荡剧烈 → 减小 Kp 或增大 Kd
- 响应太慢 → 增大 Kp（可以大胆增加，如 +50%）
- 稳态误差 → 增大 Ki（可以大胆增加，如 +50%）
- 超调过大 → 大幅减小 Kp（至少减少 30%）和增大 Kd（至少增加 50%）

## 数据
{data_text}

请直接返回 JSON 格式:
{{"analysis": "简短分析原因", "p": 数值, "i": 数值, "d": 数值, "status": "TUNING"}}"""

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
        
        resp_data = resp.json()["choices"][0]["message"]
        
        # MiniMax M2.5 可能使用 reasoning 模式，内容在 reasoning_content 中
        result_text = resp_data.get("content", "")
        if not result_text or result_text.strip() == "":
            result_text = resp_data.get("reasoning_content", "")
        
        print(f"[MiniMax] 原始返回: {result_text[:200]}...")
        
        # 预处理：去掉 Markdown 代码块标记
        result_text = result_text.replace("```json", "").replace("```", "").strip()
        
        # 尝试多种方式解析 JSON
        import re
        
        # 方法1: 尝试直接解析（如果返回的是纯JSON）
        try:
            return json.loads(result_text)
        except json.JSONDecodeError:
            pass
        
        # 方法2: 提取 JSON 块（支持嵌套 - 找最外层的{}）
        # 找到第一个 { 和最后一个 }
        start = result_text.find('{')
        end = result_text.rfind('}')
        if start != -1 and end != -1 and end > start:
            json_str = result_text[start:end+1]
            try:
                parsed = json.loads(json_str)
                # 检查是否有所需字段
                if 'p' in parsed or 'i' in parsed:
                    return parsed
            except json.JSONDecodeError as e:
                print(f"[解析] JSON 块解析失败: {e}")
        
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
        
        # 方法4: 支持更宽松的格式 (p: 1.0 或 'p': 1.0)
        p_match = re.search(r'["\']?p["\']?\s*:\s*([0-9.]+)', result_text)
        i_match = re.search(r'["\']?i["\']?\s*:\s*([0-9.]+)', result_text)
        d_match = re.search(r'["\']?d["\']?\s*:\s*([0-9.]+)', result_text)
        
        if p_match and i_match and d_match:
            # 提取 analysis
            analysis_match = re.search(r'[Aa]nalysis[:\s]+([^0-9"]+)', result_text)
            status_match = re.search(r'[Ss]tatus[:\s]+["\']?(\w+)', result_text)
            
            return {
                "analysis": analysis_match.group(1).strip() if analysis_match else "解析成功",
                "p": float(p_match.group(1)),
                "i": float(i_match.group(1)),
                "d": float(d_match.group(1)),
                "status": status_match.group(1) if status_match else "TUNING"
            }
        
        # 方法5: 支持纯数字格式 "p 1.0" 或 "p=1.0"
        p_match = re.search(r'[Pp]\s*[=:]\s*([0-9.]+)', result_text)
        i_match = re.search(r'[Ii]\s*[=:]\s*([0-9.]+)', result_text)
        d_match = re.search(r'[Dd]\s*[=:]\s*([0-9.]+)', result_text)
        
        if p_match and i_match and d_match:
            return {
                "analysis": "解析成功",
                "p": float(p_match.group(1)),
                "i": float(i_match.group(1)),
                "d": float(d_match.group(1)),
                "status": "TUNING"
            }
        
        # 方法6: 查找 Z-N 建议参数
        zn_match = re.search(r'Z-N[建议建议:：]+\s*[Pp]ID[:\s]+.*?Kp=([0-9.]+).*?Ki=([0-9.]+).*?Kd=([0-9.]+)', result_text, re.DOTALL)
        if zn_match:
            return {
                "analysis": "采用Z-N建议参数",
                "p": float(zn_match.group(1)),
                "i": float(zn_match.group(2)),
                "d": float(zn_match.group(3)),
                "status": "TUNING"
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
        
        # 3. 检查是否完成 (精细调参到 0.3°C)
        if metrics['avg_error'] < 0.3:
            print("\n✅ 调参完成！误差已达到目标范围")
            break
        
        # 4. 准备数据并调用 LLM
        recent = list(buffer)[-10:]  # 减少到 10 个数据点
        
        data_text = f"""当前 PID 参数: P={kp}, I={ki}, D={kd}
目标温度: {SETPOINT}°C
当前温度: {recent[-1]['input']:.2f}°C
平均误差: {metrics['avg_error']:.2f}°C
最大误差: {metrics['max_error']:.2f}°C

最近 10 条数据 (时间ms, 温度°C, PWM, 误差):"""
        
        for d in recent:
            data_text += f"\n{int(d['timestamp'])},{d['input']:.1f},{d['pwm']:.0f},{d['error']:+.1f}"
        
        # 5. 调用 MiniMax API
        result = call_llm(data_text, rounds, metrics)
        
        if result:
            analysis = result.get('analysis', '无')
            new_p = result.get('p', kp)
            new_i = result.get('i', ki)
            new_d = result.get('d', kd)
            status = result.get('status', 'TUNING')
            
            # 根据误差大小调整变化限制
            avg_error = metrics.get('avg_error', 0)
            
            # 误差大时允许更大变化 (50%=0.5, 100%=1.0)
            if avg_error > 100:
                MAX_CHANGE = 1.0  # 第一轮可以用任何值
            elif avg_error > 50:
                MAX_CHANGE = 0.8  # 误差大时允许80%变化
            else:
                MAX_CHANGE = 0.2  # 正常微调20%
            
            def limit_change(old_val, new_val, max_change=MAX_CHANGE):
                """限制参数变化幅度"""
                if old_val == 0 or new_val == 0:
                    return new_val if new_val != 0 else old_val
                ratio = new_val / old_val
                if ratio > 1 + max_change:
                    return old_val * (1 + max_change)
                elif ratio < 1 - max_change:
                    return old_val * (1 - max_change)
                return new_val
            
            # 如果前2轮误差>50，强制使用系统辨识的Z-N建议
            # 重要：必须在 limit_change 之前执行，否则参数已被裁剪
            zg_nichols_override = False
            if rounds <= 2 and metrics['avg_error'] > 50 and system_id_output:
                import re
                zn_match = re.search(r'PID:\s*Kp=([0-9.]+).*?Ki=([0-9.]+).*?Kd=([0-9.]+)', system_id_output, re.DOTALL)
                if zn_match:
                    # 直接采用 Z-N 建议，绕过 LLM 返回值和 limit_change
                    new_p = float(zn_match.group(1))
                    new_i = float(zn_match.group(2))
                    new_d = float(zn_match.group(3))
                    
                    # 保守模式：降低增益 + 用 PI
                    if CONSERVATIVE_MODE:
                        new_p *= Z_N_GAIN_FACTOR
                        new_i *= Z_N_GAIN_FACTOR
                        new_d = 0  # 去掉 D，用 PI 控制器
                        analysis = f"保守模式: PI, 0.5x增益 P={new_p:.1f}, I={new_i:.2f}"
                        print(f"[保守模式] {analysis}")
                    else:
                        analysis = f"激进模式: PID P={new_p:.1f}, I={new_i:.2f}, D={new_d:.3f}"
                        print(f"[激进采纳] {analysis}")
                    
                    zg_nichols_override = True
            
            # 普通参数才应用变化限制
            if not zg_nichols_override:
                new_p = limit_change(kp, new_p)
                new_i = limit_change(ki, new_i)
                new_d = limit_change(kd, new_d)
            
            print(f"[MiniMax] 分析: {analysis}")
            print(f"[MiniMax] 新参数: P={new_p:.4f}, I={new_i:.4f}, D={new_d:.4f}")
            
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
        
        # 不再重置！让仿真持续运行，这样才能达到稳态
        # sim.reset()
    
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
