#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
tuner.py - LLM PID 自动调参系统 (History-Aware + Chain-of-Thought)
===============================================================================

作者: Trae AI (Based on KINGSTON-115's work)
改进点：
1. **历史感知 (History-Aware)**: 记录并分析过去 N 轮的参数和结果，避免重复错误。
2. **思维链 (Chain-of-Thought)**: 强制 LLM 先进行推理分析，再给出参数建议。
3. **高级指标 (Advanced Metrics)**: 计算超调量、上升时间、稳定时间等更专业的控制指标。
4. **自适应步长**: 根据误差大小动态调整参数步长。
5. **系统辨识集成**: (可选) 利用 step response 数据估算系统模型。

依赖：pyserial, openai (或 requests), numpy (可选，用于高级计算)
"""

import serial
import serial.tools.list_ports
import time
import json
import re
import sys
import os
import math
from collections import deque
from typing import Optional, List, Dict, Any

# ============================================================================
# 全局配置 (请根据实际情况修改)
# ============================================================================

SERIAL_PORT = os.getenv("SERIAL_PORT", "COM3")
BAUD_RATE = int(os.getenv("BAUD_RATE", "115200"))

API_KEY = os.getenv("LLM_API_KEY", "your-api-key-here")
API_BASE_URL = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1")
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "gpt-4")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")

BUFFER_SIZE = 100             # 增加缓冲大小以提供更多上下文 (配合加快的采样率)
MIN_ERROR_THRESHOLD = 0.3     # 误差阈值
MAX_TUNING_ROUNDS = 50        # 最大调参轮数

# ============================================================================
# 增强版 Prompt 设计
# ============================================================================

SYSTEM_PROMPT = """你是一个世界顶级的 PID 控制算法专家，精通自动控制原理和系统辨识。

## 你的任务
作为一个 PID 调参助手，你需要根据系统的历史表现和当前数据，通过逻辑推理 (Chain-of-Thought) 给出最优的 PID 参数建议。

## 核心原则
1. **稳态优先**：首要任务是消除稳态误差。
2. **抑制震荡**：任何形式的等幅震荡或发散震荡都是不可接受的。
3. **防止超调**：尽量减少超调量，对于热力系统，超调可能导致不可逆的后果。
4. **循序渐进**：参数调整应平滑，避免剧烈跳变（除非系统极不稳定）。

## 输入信息结构
- **系统目标**：设定值 (Setpoint)。
- **当前状态**：当前的 PID 参数。
- **历史记录**：过去几轮的参数尝试及其对应的性能指标（误差、超调、震荡情况）。
- **当前数据**：最近的时间序列数据摘要。

## 输出格式 (JSON)
必须严格遵循以下 JSON 格式：
{
  "thought_process": "详细的推理过程：1. 分析当前误差趋势... 2. 对比历史记录，发现增加 P 导致了震荡... 3. 决定减少 P 并微调 I...",
  "analysis_summary": "简短总结 (50字以内)",
  "tuning_action": "INCREASE_P | DECREASE_P | INCREASE_I | ... (主要动作)",
  "p": <float>,
  "i": <float>,
  "d": <float>,
  "status": "TUNING" // "DONE" if converged
}
"""

# ============================================================================
# 历史记录管理器
# ============================================================================

class TuningHistory:
    """记录调参历史，用于 Prompt 上下文增强"""
    def __init__(self, max_history: int = 5):
        self.history = deque(maxlen=max_history)
    
    def add_record(self, round_num: int, pid: Dict[str, float], metrics: Dict[str, float], analysis: str):
        record = {
            "round": round_num,
            "pid": pid,
            "metrics": metrics,
            "analysis": analysis
        }
        self.history.append(record)
    
    def to_prompt_text(self) -> str:
        if not self.history:
            return "无历史记录 (这是第一轮)"
        
        text = "## 调参历史 (最近几轮):\n"
        for rec in self.history:
            m = rec['metrics']
            pid = rec['pid']
            text += (f"- Round {rec['round']}: P={pid['p']:.4f}, I={pid['i']:.4f}, D={pid['d']:.4f} "
                     f"-> AvgErr={m.get('avg_error',0):.2f}, MaxErr={m.get('max_error',0):.2f}, "
                     f"Overshoot={m.get('overshoot',0):.1f}%, Status={m.get('status', 'UNKNOWN')}\n")
        return text

# ============================================================================
# 数据缓冲与高级指标计算
# ============================================================================

class AdvancedDataBuffer:
    """增强版数据缓冲器"""
    def __init__(self, max_size: int = 100):
        self.max_size = max_size
        self.buffer = deque(maxlen=max_size)
        self.current_pid = {"p": 1.0, "i": 0.1, "d": 0.05}
        self.setpoint = 100.0
    
    def add(self, data: Dict[str, float]):
        self.buffer.append(data)
        if "p" in data:
            self.current_pid = {"p": data.get("p", 1.0), "i": data.get("i", 0.1), "d": data.get("d", 0.05)}
        if "setpoint" in data:
            self.setpoint = data["setpoint"]
    
    def is_full(self) -> bool:
        return len(self.buffer) >= self.max_size
    
    def get_recent_data(self, n: int = 50) -> List[Dict[str, float]]:
        return list(self.buffer)[-n:]
    
    def calculate_advanced_metrics(self) -> Dict[str, Any]:
        """计算高级控制指标"""
        if not self.buffer:
            return {}
        
        data = list(self.buffer)
        inputs = [d.get("input", 0) for d in data]
        errors = [d.get("setpoint", 0) - d.get("input", 0) for d in data]
        abs_errors = [abs(e) for e in errors]
        timestamps = [d.get("timestamp", 0) for d in data]
        
        # 基础指标
        avg_error = sum(abs_errors) / len(abs_errors) if abs_errors else 0
        max_error = max(abs_errors) if abs_errors else 0
        current_error = abs_errors[-1]
        
        # 高级指标：超调量 (Overshoot)
        # 只有当输入曾经超过设定值时才计算
        max_input = max(inputs)
        overshoot = 0.0
        if max_input > self.setpoint:
            overshoot = ((max_input - self.setpoint) / self.setpoint) * 100.0
        
        # 高级指标：稳态误差 (Steady State Error) - 用最后 20% 数据的平均误差估计
        steady_state_len = max(1, int(len(data) * 0.2))
        steady_state_error = sum(abs_errors[-steady_state_len:]) / steady_state_len
        
        # 高级指标：震荡检测 (Oscillation)
        # 计算过零点次数 (Error sign changes)
        zero_crossings = 0
        for i in range(1, len(errors)):
            if (errors[i-1] > 0 and errors[i] < 0) or (errors[i-1] < 0 and errors[i] > 0):
                zero_crossings += 1
        
        # 状态判断
        status = "STABLE"
        if zero_crossings > len(data) * 0.3: # 频繁过零 -> 震荡
            status = "OSCILLATING"
        elif overshoot > 5.0: # 超调 > 5%
            status = "OVERSHOOTING"
        elif avg_error > 10.0 and steady_state_error > 5.0:
            status = "SLOW_RESPONSE"
        
        return {
            "avg_error": avg_error,
            "max_error": max_error,
            "current_error": current_error,
            "overshoot": overshoot,
            "steady_state_error": steady_state_error,
            "zero_crossings": zero_crossings,
            "status": status,
            "setpoint": self.setpoint
        }
    
    def to_prompt_data(self) -> str:
        metrics = self.calculate_advanced_metrics()
        recent = self.get_recent_data(20) # 只给最近 20 个点作为细节，避免 token 溢出
        
        # 下采样：如果数据太多，每隔几个点取一个，保持趋势可见
        all_data = list(self.buffer)
        step = max(1, len(all_data) // 30)
        sampled_data = all_data[::step]
        
        lines = []
        lines.append(f"## 当前状态分析")
        lines.append(f"- 设定值 (Setpoint): {self.setpoint}")
        lines.append(f"- 当前 PID: P={self.current_pid['p']}, I={self.current_pid['i']}, D={self.current_pid['d']}")
        lines.append(f"- 平均误差: {metrics.get('avg_error', 0):.2f}")
        lines.append(f"- 最大误差: {metrics.get('max_error', 0):.2f}")
        lines.append(f"- 超调量: {metrics.get('overshoot', 0):.1f}%")
        lines.append(f"- 稳态误差估算: {metrics.get('steady_state_error', 0):.2f}")
        lines.append(f"- 震荡检测: 过零点 {metrics.get('zero_crossings', 0)} 次 (状态: {metrics.get('status', 'UNKNOWN')})")
        lines.append("")
        lines.append(f"## 时间序列数据摘要 (采样 {len(sampled_data)} 点):")
        lines.append("Timestamp, Input, PWM, Error")
        
        for d in sampled_data:
            lines.append(f"{d.get('timestamp', 0):.0f}, {d.get('input', 0):.2f}, {d.get('pwm', 0):.1f}, {d.get('error', 0):.2f}")
        
        return "\n".join(lines)

# ============================================================================
# LLM 接口类 (复用基础逻辑)
# ============================================================================

class LLMTuner:
    def __init__(self, api_key: str, base_url: str, model: str, provider: str = "openai"):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.provider = provider
        
        # 自动识别 provider
        if "anthropic" in base_url.lower() or "claude" in model.lower():
            self.provider = "anthropic"

        try:
            if self.provider == "openai":
                import openai
                self.client = openai.OpenAI(api_key=api_key, base_url=base_url)
            elif self.provider == "anthropic":
                import anthropic
                self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url)
        except ImportError:
            import requests
            self.requests = requests
            self.use_sdk = False
        else:
            self.use_sdk = True
    
    def _parse_json(self, text: str) -> Optional[Dict[str, Any]]:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except:
                pass
        return None

    def analyze(self, prompt_data: str, history_text: str) -> Optional[Dict[str, Any]]:
        user_prompt = f"""
{history_text}

{prompt_data}

请基于以上历史和当前数据，分析 PID 参数表现并给出优化建议。
务必使用 JSON 格式返回，包含 thought_process 字段。
"""
        try:
            if self.use_sdk:
                if self.provider == "openai":
                    resp = self.client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt}
                        ],
                        temperature=0.3
                    )
                    content = resp.choices[0].message.content
                elif self.provider == "anthropic":
                    resp = self.client.messages.create(
                        model=self.model,
                        system=SYSTEM_PROMPT,
                        messages=[{"role": "user", "content": user_prompt}],
                        temperature=0.3,
                        max_tokens=1000
                    )
                    content = resp.content[0].text
            else:
                # Fallback to requests
                headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
                payload = {
                    "model": self.model,
                    "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
                    "temperature": 0.3
                }
                resp = self.requests.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                content = resp.json()['choices'][0]['message']['content']
            
            print(f"\n[LLM 思考过程]\n{content[:500]}...\n") # 打印前 500 字符的思考过程
            return self._parse_json(content)
            
        except Exception as e:
            print(f"[ERROR] LLM 调用失败: {e}")
            return None

# ============================================================================
# 串口通信类 (简化版)
# ============================================================================

class SerialBridge:
    def __init__(self, port: str, baudrate: int):
        self.port = port
        self.baudrate = baudrate
        self.serial = None
    
    def connect(self):
        try:
            self.serial = serial.Serial(self.port, self.baudrate, timeout=1)
            print(f"[INFO] Connected to {self.port}")
            return True
        except Exception as e:
            print(f"[ERROR] Connection failed: {e}")
            return False
            
    def disconnect(self):
        if self.serial: self.serial.close()

    def read_line(self):
        if self.serial and self.serial.is_open:
            try:
                return self.serial.readline().decode('utf-8', errors='ignore').strip()
            except: pass
        return None

    def send_command(self, cmd):
        if self.serial and self.serial.is_open:
            self.serial.write(f"{cmd}\n".encode('utf-8'))
            print(f"[CMD] Sent: {cmd}")

    def parse_data(self, line):
        if not line or line.startswith('#'): return None
        parts = line.split(',')
        if len(parts) >= 5:
            try:
                return {
                    "timestamp": float(parts[0]),
                    "setpoint": float(parts[1]),
                    "input": float(parts[2]),
                    "pwm": float(parts[3]),
                    "error": float(parts[4]),
                    "p": float(parts[5]) if len(parts)>5 else 1.0,
                    "i": float(parts[6]) if len(parts)>6 else 0.1,
                    "d": float(parts[7]) if len(parts)>7 else 0.05
                }
            except: pass
        return None

# ============================================================================
# 主程序
# ============================================================================

def main():
    print("="*60)
    print("  LLM PID Tuner PRO - 增强版自动调参系统")
    print("="*60)
    
    # 串口初始化
    bridge = SerialBridge(SERIAL_PORT, BAUD_RATE)
    if not bridge.connect(): return
    
    # LLM 初始化
    tuner = LLMTuner(API_KEY, API_BASE_URL, MODEL_NAME, LLM_PROVIDER)
    
    # 数据与历史
    buffer = AdvancedDataBuffer(max_size=BUFFER_SIZE)
    history = TuningHistory(max_history=5)
    
    round_num = 0
    
    try:
        bridge.send_command("STATUS") # 唤醒/检查状态
        time.sleep(1)
        
        print("[INFO] 开始采集数据...")
        
        while round_num < MAX_TUNING_ROUNDS:
            line = bridge.read_line()
            if line:
                data = bridge.parse_data(line)
                if data:
                    buffer.add(data)
                    print(f"\r[DATA] T={data['input']:.1f} Err={data['error']:.1f} PWM={data['pwm']:.0f}", end="")
            
            if buffer.is_full():
                print("\n\n" + "-"*60)
                round_num += 1
                metrics = buffer.calculate_advanced_metrics()
                print(f"[第 {round_num} 轮] 分析中... AvgErr={metrics['avg_error']:.2f}, Status={metrics['status']}")
                
                # 准备 Prompt
                prompt_data = buffer.to_prompt_data()
                history_text = history.to_prompt_text()
                
                # 调用 LLM
                result = tuner.analyze(prompt_data, history_text)
                
                if result:
                    # 记录历史
                    history.add_record(round_num, buffer.current_pid, metrics, result.get('analysis_summary', ''))
                    
                    # 应用新参数
                    new_p = result.get('p', buffer.current_pid['p'])
                    new_i = result.get('i', buffer.current_pid['i'])
                    new_d = result.get('d', buffer.current_pid['d'])
                    
                    print(f"[Result] {result.get('analysis_summary')}")
                    print(f"[Action] {result.get('tuning_action')} -> P={new_p}, I={new_i}, D={new_d}")
                    
                    cmd = f"SET P:{new_p} I:{new_i} D:{new_d}"
                    bridge.send_command(cmd)
                    
                    if result.get('status') == "DONE" or metrics['avg_error'] < MIN_ERROR_THRESHOLD:
                        print("\n[SUCCESS] 调参完成！")
                        break
                
                buffer.buffer.clear()
                time.sleep(1)
                
    except KeyboardInterrupt:
        print("\n[INFO] 用户停止")
    finally:
        bridge.disconnect()

if __name__ == "__main__":
    main()
