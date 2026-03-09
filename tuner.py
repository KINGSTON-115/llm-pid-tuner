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

from pid_safety import (
    apply_pid_guardrails,
    build_fallback_suggestion,
    is_good_enough,
    maybe_update_best_result,
    pid_equals,
    should_rollback_to_best,
)

# ============================================================================
# 全局配置 (请根据实际情况修改)
# ============================================================================

# 默认配置
CONFIG = {
    "SERIAL_PORT": "AUTO",          # "AUTO" 或具体端口号 (如 "COM3")
    "BAUD_RATE": 115200,
    "LLM_API_KEY": "your-api-key-here",
    "LLM_API_BASE_URL": "https://api.openai.com/v1",
    "LLM_MODEL_NAME": "gpt-4",
    "LLM_PROVIDER": "openai",
    "BUFFER_SIZE": 100,
    "MIN_ERROR_THRESHOLD": 0.3,
    "MAX_TUNING_ROUNDS": 50,
    "LLM_REQUEST_TIMEOUT": 60,
    "LLM_DEBUG_OUTPUT": False,
    "GOOD_ENOUGH_AVG_ERROR": 1.2,
    "GOOD_ENOUGH_STEADY_STATE_ERROR": 0.3,
    "GOOD_ENOUGH_OVERSHOOT": 2.0,
    "REQUIRED_STABLE_ROUNDS": 2
}

CONFIG_PATH = "config.json"

def _parse_env_value(default_value: Any, raw_value: str) -> Any:
    if isinstance(default_value, bool):
        return raw_value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(default_value, int) and not isinstance(default_value, bool):
        return int(raw_value)
    if isinstance(default_value, float):
        return float(raw_value)
    return raw_value

def load_config(create_if_missing: bool = True, verbose: bool = True):
    """加载配置文件；按需创建，避免 import 时产生副作用"""
    global CONFIG
    
    # 1. 尝试读取配置文件
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                user_config = json.load(f)
                CONFIG.update(user_config)
                if verbose:
                    print(f"[INFO] 已加载配置文件: {CONFIG_PATH}")
        except Exception as e:
            if verbose:
                print(f"[WARN] 配置文件加载失败: {e}，将使用默认值。")
    elif create_if_missing:
        # 2. 如果不存在，自动创建默认配置
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(CONFIG, f, indent=4, ensure_ascii=False)
            if verbose:
                print(f"[INFO] 未找到配置文件，已生成默认配置: {CONFIG_PATH}")
                print(f"[HINT] 请打开 {CONFIG_PATH} 修改您的 API Key 和串口设置。")
        except Exception as e:
            if verbose:
                print(f"[WARN] 无法创建配置文件: {e}")

    # 3. 环境变量覆盖 (优先级最高)
    for key in CONFIG:
        env_val = os.getenv(key)
        if env_val:
            try:
                CONFIG[key] = _parse_env_value(CONFIG[key], env_val)
            except Exception:
                if verbose:
                    print(f"[WARN] 环境变量 {key} 值无效，已忽略。")

def apply_runtime_config():
    globals().update(
        SERIAL_PORT=CONFIG["SERIAL_PORT"],
        BAUD_RATE=CONFIG["BAUD_RATE"],
        API_KEY=CONFIG["LLM_API_KEY"],
        API_BASE_URL=CONFIG["LLM_API_BASE_URL"],
        MODEL_NAME=CONFIG["LLM_MODEL_NAME"],
        LLM_PROVIDER=CONFIG["LLM_PROVIDER"],
        BUFFER_SIZE=CONFIG["BUFFER_SIZE"],
        MIN_ERROR_THRESHOLD=CONFIG["MIN_ERROR_THRESHOLD"],
        MAX_TUNING_ROUNDS=CONFIG["MAX_TUNING_ROUNDS"],
        LLM_REQUEST_TIMEOUT=CONFIG["LLM_REQUEST_TIMEOUT"],
        LLM_DEBUG_OUTPUT=CONFIG["LLM_DEBUG_OUTPUT"],
        GOOD_ENOUGH_AVG_ERROR=CONFIG["GOOD_ENOUGH_AVG_ERROR"],
        GOOD_ENOUGH_STEADY_STATE_ERROR=CONFIG["GOOD_ENOUGH_STEADY_STATE_ERROR"],
        GOOD_ENOUGH_OVERSHOOT=CONFIG["GOOD_ENOUGH_OVERSHOOT"],
        REQUIRED_STABLE_ROUNDS=CONFIG["REQUIRED_STABLE_ROUNDS"],
    )

def initialize_runtime_config(create_if_missing: bool = True, verbose: bool = True):
    load_config(create_if_missing=create_if_missing, verbose=verbose)
    apply_runtime_config()

initialize_runtime_config(create_if_missing=False, verbose=False)

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
    
    def calculate_advanced_metrics(self) -> Dict[str, Any]:
        """计算高级控制指标"""
        if not self.buffer:
            return {}
        
        data = list(self.buffer)
        inputs = [d.get("input", 0) for d in data]
        errors = [d.get("setpoint", 0) - d.get("input", 0) for d in data]
        abs_errors = [abs(e) for e in errors]
        
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

        
        # 下采样：如果数据太多，每隔几个点取一个，保持趋势可见
        all_data = list(self.buffer)
        step = max(1, len(all_data) // 30)
        sampled_data = all_data[::step]
        
        lines = []
        lines.append("## Current Status")
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
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.provider_choice = self._normalize_provider_choice(provider)
        self.provider = self._resolve_transport()

        try:
            if self.provider == "openai":
                import openai
                self.client = openai.OpenAI(api_key=api_key, base_url=self.base_url)
            elif self.provider == "anthropic":
                import anthropic
                self.client = anthropic.Anthropic(api_key=api_key, base_url=self.base_url)
        except ImportError:
            self.requests = self._import_requests()
            self.use_sdk = False
        else:
            self.use_sdk = True

    @staticmethod
    def _normalize_provider_choice(provider: Optional[str]) -> str:
        provider_choice = str(provider or "").strip().lower()
        provider_choice = provider_choice.replace("-", "_").replace(" ", "_")
        return provider_choice or "openai"

    def _resolve_transport(self) -> str:
        if self.provider_choice in (
            "openai",
            "openai_compat",
            "openai_compatible",
            "openai_claude",
            "claude_openai",
            "claude_relay",
        ):
            return "openai"
        if self.provider_choice in ("anthropic", "anthropic_native", "claude_native"):
            return "anthropic"

        base_url_lower = self.base_url.lower()
        if self.provider_choice == "auto" and "api.anthropic.com" in base_url_lower:
            return "anthropic"

        return "openai"

    def _import_requests(self):
        import requests
        return requests

    def _ensure_requests(self):
        if not hasattr(self, "requests") or self.requests is None:
            self.requests = self._import_requests()

    def _request_via_http(self, user_prompt: str) -> str:
        self._ensure_requests()

        if self.provider == "anthropic":
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            }
            payload = {
                "model": self.model,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_prompt}],
                "temperature": 0.3,
                "max_tokens": 1000
            }
            resp = self.requests.post(
                f"{self.base_url}/messages",
                headers=headers,
                json=payload,
                timeout=LLM_REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            response_json = resp.json()
            content_blocks = response_json.get("content", [])
            return "\n".join(block.get("text", "") for block in content_blocks if isinstance(block, dict))

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
            "temperature": 0.3
        }
        resp = self.requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=LLM_REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        response_json = resp.json()
        return response_json['choices'][0]['message']['content']

    def _extract_json_candidates(self, text: str) -> List[str]:
        candidates: List[str] = []
        stripped = text.strip()

        if stripped:
            candidates.append(stripped)

        fenced_matches = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
        candidates.extend(fenced_matches)

        for start in range(len(text)):
            if text[start] != '{':
                continue
            depth = 0
            for end in range(start, len(text)):
                char = text[end]
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth == 0:
                        candidates.append(text[start:end + 1])
                        break

        return candidates

    def _sanitize_result(self, data: Dict[str, Any]) -> Dict[str, Any]:
        sanitized = dict(data)

        for key in ("p", "i", "d"):
            value = sanitized.get(key)
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                sanitized.pop(key, None)
                continue

            if not math.isfinite(numeric) or numeric < 0:
                sanitized.pop(key, None)
            else:
                sanitized[key] = numeric

        if "status" in sanitized:
            status = str(sanitized["status"]).strip().upper()
            sanitized["status"] = "DONE" if status == "DONE" else "TUNING"

        if not sanitized.get("analysis_summary"):
            sanitized["analysis_summary"] = str(sanitized.get("analysis") or "未提供分析摘要")

        if not sanitized.get("thought_process"):
            sanitized["thought_process"] = str(sanitized.get("analysis_summary") or "模型未提供详细推理")

        if not sanitized.get("tuning_action"):
            sanitized["tuning_action"] = "ADJUST_PID"

        return sanitized
    
    def _parse_json(self, text: str) -> Optional[Dict[str, Any]]:
        for candidate in self._extract_json_candidates(text):
            try:
                return self._sanitize_result(json.loads(candidate))
            except Exception:
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
                try:
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
                except Exception as sdk_error:
                    print(f"[WARN] SDK 调用失败，尝试 HTTP 回退: {sdk_error}")
                    content = self._request_via_http(user_prompt)
            else:
                content = self._request_via_http(user_prompt)
            
            if LLM_DEBUG_OUTPUT:
                print(f"\n[LLM 原始响应预览]\n{content[:500]}...\n")

            parsed = self._parse_json(content)
            if parsed:
                return parsed

            print("[WARN] LLM 响应未能解析为 JSON，已忽略本轮建议。")
            return None
            
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


def safe_pause(message: str = "按回车键退出..."):
    try:
        input(message)
    except EOFError:
        pass

def select_serial_port():
    """交互式选择串口"""
    print("\n[INFO] 正在扫描可用串口...")
    ports = list(serial.tools.list_ports.comports())
    
    if not ports:
        print("[WARN] 未发现任何串口设备！")
        port_name = input("请输入串口号 (例如 COM3 或 /dev/ttyUSB0): ").strip()
        return port_name
    
    print(f"发现 {len(ports)} 个设备:")
    for i, p in enumerate(ports):
        print(f"  [{i+1}] {p.device} - {p.description}")
    
    while True:
        choice = input(f"\n请选择序号 (1-{len(ports)}) 或输入 'm' 手动指定: ").strip().lower()
        if choice == 'm':
            return input("请输入串口号: ").strip()
        
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(ports):
                return ports[idx].device
        
        print("[ERROR] 输入无效，请重试。")

# ============================================================================
# 主程序
# ============================================================================

def main():
    initialize_runtime_config(create_if_missing=True, verbose=True)

    print("="*60)
    print("  LLM PID Tuner PRO - 增强版自动调参系统")
    print("="*60)
    
    # 串口选择逻辑
    global SERIAL_PORT
    if len(sys.argv) > 1:
        # 如果命令行提供了参数，优先使用命令行参数 (方便脚本调用)
        # 例如: tuner.exe COM3
        if not sys.argv[1].startswith("-"):
             SERIAL_PORT = sys.argv[1]
    else:
        # 否则尝试交互式选择，如果配置文件里写了 "AUTO" 或空，则交互选择
        # 如果配置文件里写了具体的 "COM3"，则使用配置
        env_port = CONFIG.get("SERIAL_PORT")
        
        if env_port and env_port.upper() != "AUTO":
             print(f"[INFO] 使用配置端口: {env_port}")
             use_env = input("是否使用该端口? (Y/n): ").strip().lower()
             if use_env == 'n':
                 SERIAL_PORT = select_serial_port()
             else:
                 SERIAL_PORT = env_port
        else:
             SERIAL_PORT = select_serial_port()

    if not SERIAL_PORT:
        print("[ERROR] 未指定串口，程序退出。")
        safe_pause()
        return

    print(f"[INFO] 即将连接到: {SERIAL_PORT}")
    
    # 串口初始化
    bridge = SerialBridge(SERIAL_PORT, BAUD_RATE)
    if not bridge.connect(): 
        print(f"[ERROR] 无法打开串口 {SERIAL_PORT}")
        safe_pause()
        return
    
    # LLM 初始化
    tuner = LLMTuner(API_KEY, API_BASE_URL, MODEL_NAME, LLM_PROVIDER)
    
    # 数据与历史
    buffer = AdvancedDataBuffer(max_size=BUFFER_SIZE)
    history = TuningHistory(max_history=5)
    good_enough_rules = {
        "avg_error_threshold": GOOD_ENOUGH_AVG_ERROR,
        "steady_state_error_threshold": GOOD_ENOUGH_STEADY_STATE_ERROR,
        "overshoot_threshold": GOOD_ENOUGH_OVERSHOOT,
    }
    
    round_num = 0
    stable_rounds = 0
    best_result = None
    
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
                previous_best = best_result
                best_result = maybe_update_best_result(best_result, buffer.current_pid, metrics, round_num)
                if best_result is not None and best_result is not previous_best:
                    print(
                        f"[Best] 更新最佳参数 -> "
                        f"P={best_result['pid']['p']}, I={best_result['pid']['i']}, D={best_result['pid']['d']}"
                    )

                if best_result and not pid_equals(buffer.current_pid, best_result["pid"]) and should_rollback_to_best(metrics, best_result["metrics"]):
                    rollback_pid = best_result["pid"]
                    print(
                        f"[Rollback] 当前表现劣于第 {best_result['round']} 轮最佳结果，"
                        f"恢复到 P={rollback_pid['p']}, I={rollback_pid['i']}, D={rollback_pid['d']}"
                    )
                    bridge.send_command(f"SET P:{rollback_pid['p']} I:{rollback_pid['i']} D:{rollback_pid['d']}")
                    buffer.current_pid = dict(rollback_pid)

                    if is_good_enough(best_result["metrics"], good_enough_rules):
                        print("\n[SUCCESS] 已回滚到历史最佳且满足可用标准，提前结束调参。")
                        break

                    buffer.buffer.clear()
                    time.sleep(1)
                    continue

                stable_rounds = stable_rounds + 1 if is_good_enough(metrics, good_enough_rules) else 0

                if stable_rounds >= REQUIRED_STABLE_ROUNDS:
                    print(f"\n[SUCCESS] 系统已连续 {stable_rounds} 轮达到可用稳定状态，提前结束调参。")
                    break
                
                # 准备 Prompt
                prompt_data = buffer.to_prompt_data()
                history_text = history.to_prompt_text()
                
                # 调用 LLM
                result = tuner.analyze(prompt_data, history_text)

                if not result:
                    print("[WARN] LLM 本轮不可用，启用保守兜底策略。")
                    result = build_fallback_suggestion(buffer.current_pid, metrics)
                
                if result:
                    safe_pid, guardrail_notes = apply_pid_guardrails(buffer.current_pid, result)
                    new_p = safe_pid['p']
                    new_i = safe_pid['i']
                    new_d = safe_pid['d']

                    # 记录历史
                    history.add_record(round_num, safe_pid, metrics, result.get('analysis_summary', ''))
                    
                    print(f"[Result] {result.get('analysis_summary')}")
                    print(f"[Action] {result.get('tuning_action')} -> P={new_p}, I={new_i}, D={new_d}")
                    if guardrail_notes:
                        print(f"[Guardrail] {'; '.join(guardrail_notes)}")
                    if result.get('fallback_used'):
                        print("[Fallback] 本轮使用规则策略替代 LLM 建议。")
                    
                    cmd = f"SET P:{new_p} I:{new_i} D:{new_d}"
                    bridge.send_command(cmd)
                    buffer.current_pid = safe_pid
                    
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
