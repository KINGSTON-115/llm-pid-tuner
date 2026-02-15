#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
tuner.py - 基于 LLM 的 PID 自动调参系统 - Python 上位机桥接脚本
===============================================================================

作者: KINGSTON-115
功能：串口数据读取 -> LLM 分析 -> 参数下发
依赖：pyserial, openai (或 requests)

【数据流说明】
MCU (下位机) --[Serial CSV]--> Python (上位机) --[API]--> LLM (决策大脑)
                                            |
                                            v (JSON)
                                    LLM 返回新参数
                                            |
                                            v
                                    Python --[Serial CMD]--> MCU (更新 PID)

===============================================================================
"""

import serial
import serial.tools.list_ports
import time
import json
import re
import sys
import threading
from collections import deque
from typing import Optional, List, Dict, Any

# ============================================================================
# 全局配置 (请根据实际情况修改)
# ============================================================================

# 串口配置
SERIAL_PORT = "COM3"          # Windows: "COM3" | Linux: "/dev/ttyUSB0" | macOS: "/dev/cu.usbserial-*"
BAUD_RATE = 115200

# LLM API 配置 (支持 OpenAI 兼容 API)
API_KEY = "your-api-key-here"           # 替换为你的 API Key
API_BASE_URL = "https://api.openai.com/v1"  # 或其他兼容 API
MODEL_NAME = "gpt-4"                     # 使用的模型

# 调参配置
BUFFER_SIZE = 20             # 数据缓冲大小 (行数)
MIN_ERROR_THRESHOLD = 0.5     # 误差阈值 (小于此值认为调参完成)
MAX_TUNING_ROUNDS = 50        # 最大调参轮数

# ============================================================================
# 调参器选择 (TUNER_MODE)
# ============================================================================
# 可选值:
#   - "llm":    使用外部 LLM API (OpenAI/Anthropic/自定义)
#   - "openclaw": 使用本地 OpenClaw (通过 CLI 调用)
TUNER_MODE = "openclaw"       # 默认使用本地 OpenClaw

# OpenClaw 配置 (当 TUNER_MODE = "openclaw" 时使用)
OPENCLAW_CLI_PATH = "openclaw"  # OpenClaw CLI 路径
OPENCLAW_SESSION = "main"     # 使用的会话标签

# ============================================================================
# AI 核心 Prompt 设计
# ============================================================================

SYSTEM_PROMPT = """你是一个控制算法专家，精通 PID 控制理论和自动调参技术。

## 你的任务
分析传入的时间序列数据（目标值 vs 实际值），判断当前 PID 参数的表现，并给出优化建议。

## 数据格式
- setpoint: 目标值 (期望温度/位置等)
- input: 实际值 (当前温度/位置等)
- pwm: 控制输出 (PWM 占空比)
- error: 误差 (setpoint - input)

## 判断逻辑

### 1. 震荡剧烈 (Oscillation)
特征：error 在目标值上下大幅波动，形成周期性震荡
操作：减小 Kp 或增大 Kd

### 2. 响应太慢 / 上升时间长 (Slow Response)
特征：input 接近 setpoint 的速度太慢
操作：增大 Kp

### 3. 稳态误差 (Steady-State Error)
特征：长时间后 error 始终存在，无法归零
操作：增大 Ki

### 4. 超调过大 (Overshoot)
特征：input 超过 setpoint 后才回落
操作：减小 Kp 或增大 Kd

### 5. 响应正常 (Good)
特征：error 快速趋于 0，无超调或超调很小
操作：保持当前参数或微调

## 输出格式要求
你必须返回严格的 JSON 格式，严禁包含 Markdown 代码块标记或其他废话：

{
  "analysis": "简短的分析结论（20字以内）",
  "p": <float>,
  "i": <float>,
  "d": <float>,
  "status": "TUNING"  // 如果误差极小且稳定则返回 "DONE"
}

## 重要约束
1. 只返回 JSON，不要有任何解释或多余文本
2. p, i, d 必须是可以实际使用的浮点数
3. 如果当前参数已经很好，返回 "status": "DONE"
4. 每次参数变化不要太大，建议每次调整 10-20%"""

# ============================================================================
# 数据缓冲类
# ============================================================================

class DataBuffer:
    """
    滑动窗口数据缓冲器
    用于存储最近的 N 条数据，供 LLM 分析
    """
    def __init__(self, max_size: int = 100):
        self.max_size = max_size
        self.buffer = deque(maxlen=max_size)
        self.current_pid = {"p": 1.0, "i": 0.1, "d": 0.05}
        self.setpoint = 100.0
    
    def add(self, data: Dict[str, float]):
        """添加一条数据到缓冲器"""
        self.buffer.append(data)
        if "p" in data:
            self.current_pid = {"p": data.get("p", 1.0), "i": data.get("i", 0.1), "d": data.get("d", 0.05)}
        if "setpoint" in data:
            self.setpoint = data["setpoint"]
    
    def is_full(self) -> bool:
        """检查缓冲器是否已满"""
        return len(self.buffer) >= self.max_size
    
    def get_recent_data(self, n: int = 50) -> List[Dict[str, float]]:
        """获取最近 N 条数据"""
        return list(self.buffer)[-n:]
    
    def calculate_metrics(self) -> Dict[str, float]:
        """计算当前数据的关键指标"""
        if not self.buffer:
            return {}
        
        errors = [abs(d.get("error", 0)) for d in self.buffer]
        inputs = [d.get("input", 0) for d in self.buffer]
        
        # 计算平均误差
        avg_error = sum(errors) / len(errors) if errors else 0
        
        # 计算最大误差
        max_error = max(errors) if errors else 0
        
        # 检查是否稳定 (误差变化小于阈值)
        error_variance = 0
        if len(errors) > 10:
            recent_errors = errors[-10:]
            error_variance = sum((e - sum(recent_errors)/len(recent_errors))**2 for e in recent_errors) / len(recent_errors)
        
        return {
            "avg_error": avg_error,
            "max_error": max_error,
            "error_variance": error_variance,
            "latest_input": inputs[-1] if inputs else 0,
            "setpoint": self.setpoint
        }
    
    def to_prompt_data(self) -> str:
        """将数据转换为供 LLM 分析的文本格式"""
        metrics = self.calculate_metrics()
        recent = self.get_recent_data(50)
        
        lines = []
        lines.append(f"当前 PID 参数: P={self.current_pid['p']}, I={self.current_pid['i']}, D={self.current_pid['d']}")
        lines.append(f"目标值: {self.setpoint}")
        lines.append(f"当前指标: 平均误差={metrics.get('avg_error', 0):.2f}, 最大误差={metrics.get('max_error', 0):.2f}")
        lines.append("")
        lines.append("最近 50 条数据 (timestamp, setpoint, input, pwm, error):")
        
        for d in recent:
            lines.append(f"{d.get('timestamp', 0)}, {d.get('setpoint', 0):.1f}, {d.get('input', 0):.2f}, {d.get('pwm', 0):.1f}, {d.get('error', 0):.2f}")
        
        return "\n".join(lines)


# ============================================================================
# LLM 接口类
# ============================================================================

class LLMTuner:
    """
    LLM 调参器
    负责调用 LLM API 并解析返回的 JSON
    """
    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        
        # 尝试导入 openai，如果失败则使用 requests
        try:
            import openai
            self.client = openai.OpenAI(api_key=api_key, base_url=base_url)
            self.use_openai = True
        except ImportError:
            import requests
            self.requests = requests
            self.use_openai = False
    
    def analyze_and_suggest(self, data_text: str) -> Optional[Dict[str, Any]]:
        """
        将数据发送给 LLM，获取调参建议
        
        Args:
            data_text: 格式化后的数据文本
            
        Returns:
            包含新 PID 参数的字典，或 None (如果失败)
        """
        user_prompt = f"""请分析以下 PID 控制系统数据，并给出优化建议：

{data_text}

请根据分析结果返回新的 PID 参数 (JSON 格式)。"""

        try:
            if self.use_openai:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.3,
                    max_tokens=500
                )
                result_text = response.choices[0].message.content
            else:
                # 使用 requests 直接调用 API
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 500
                }
                resp = self.requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=30
                )
                resp.raise_for_status()
                result_text = resp.json()["choices"][0]["message"]["content"]
            
            # 解析 JSON
            return self._parse_json_response(result_text)
            
        except Exception as e:
            print(f"[ERROR] LLM 调用失败: {e}")
            return None
    
    def _parse_json_response(self, text: str) -> Optional[Dict[str, Any]]:
        """解析 LLM 返回的 JSON"""
        # 尝试提取 JSON 块
        json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        
        # 尝试直接解析整个文本
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            print(f"[ERROR] 无法解析 LLM 返回的 JSON: {text}")
            return None


# ============================================================================
# OpenClaw 调参器类
# ============================================================================

class OpenClawTuner:
    """
    OpenClaw 本地调参器
    通过 CLI 调用本地 OpenClaw 来分析 PID 数据
    
    【数据流】
    Python -> OpenClaw CLI (消息) -> OpenClaw (LLM 分析) -> 返回 JSON -> Python
    """
    def __init__(self, cli_path: str = "openclaw", session: str = "main"):
        self.cli_path = cli_path
        self.session = session
    
    def analyze_and_suggest(self, data_text: str) -> Optional[Dict[str, Any]]:
        """
        将数据发送给 OpenClaw，获取调参建议
        
        Args:
            data_text: 格式化后的数据文本
            
        Returns:
            包含新 PID 参数的字典，或 None (如果失败)
        """
        import subprocess
        
        # 构建发送给 OpenClaw 的消息
        openclaw_prompt = f"""你是一个 PID 控制算法专家。请分析以下温度控制系统的时间序列数据，判断当前 PID 参数的表现，并给出优化建议。

## 数据格式
- setpoint: 目标温度
- input: 实际温度
- pwm: 控制输出 (PWM 占空比 0-255)
- error: 误差 (setpoint - input)

## 判断规则
- 震荡剧烈 → 减小 Kp 或增大 Kd
- 响应太慢 → 增大 Kp  
- 稳态误差 → 增大 Ki
- 超调过大 → 减小 Kp 或增大 Kd

## 数据内容
{data_text}

请返回严格的 JSON 格式 (不要有 markdown 代码块):
{{"analysis": "简短分析", "p": <float>, "i": <float>, "d": <float>, "status": "TUNING 或 DONE"}}"""

        try:
            # 调用 OpenClaw CLI
            cmd = [self.cli_path, "chat", "--session", self.session, "--yes"]
            result = subprocess.run(
                cmd,
                input=openclaw_prompt,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode != 0:
                print(f"[ERROR] OpenClaw 调用失败: {result.stderr}")
                return None
            
            # 解析返回结果
            output = result.stdout
            return self._parse_json_response(output)
            
        except subprocess.TimeoutExpired:
            print("[ERROR] OpenClaw 调用超时")
            return None
        except Exception as e:
            print(f"[ERROR] OpenClaw 调用异常: {e}")
            return None
    
    def _parse_json_response(self, text: str) -> Optional[Dict[str, Any]]:
        """解析返回的 JSON"""
        # 尝试提取 JSON 块
        json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        
        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            print(f"[ERROR] 无法解析 OpenClaw 返回: {text[:200]}")
            return None


# ============================================================================
# 串口通信类
# ============================================================================

class SerialBridge:
    """
    串口桥接器
    负责与 MCU 通信：读取数据和发送指令
    """
    def __init__(self, port: str, baudrate: int):
        self.port = port
        self.baudrate = baudrate
        self.serial: Optional[serial.Serial] = None
    
    def connect(self) -> bool:
        """连接串口"""
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=1
            )
            print(f"[INFO] 已连接到串口 {self.port}")
            return True
        except serial.SerialException as e:
            print(f"[ERROR] 无法打开串口 {self.port}: {e}")
            return False
    
    def disconnect(self):
        """断开串口连接"""
        if self.serial and self.serial.is_open:
            self.serial.close()
            print("[INFO] 已关闭串口")
    
    def read_line(self) -> Optional[str]:
        """读取一行数据"""
        if self.serial and self.serial.is_open:
            try:
                line = self.serial.readline().decode('utf-8', errors='ignore').strip()
                return line if line else None
            except Exception as e:
                return None
        return None
    
    def send_command(self, cmd: str) -> bool:
        """发送指令到 MCU"""
        if self.serial and self.serial.is_open:
            try:
                self.serial.write(f"{cmd}\n".encode('utf-8'))
                self.serial.flush()
                return True
            except Exception as e:
                print(f"[ERROR] 发送指令失败: {e}")
                return False
        return False
    
    def parse_data_line(self, line: str) -> Optional[Dict[str, float]]:
        """
        解析 CSV 数据行
        
        格式: timestamp_ms,setpoint,input,pwm,error,p,i,d
        
        示例:
        5000,100.0,45.23,127.5,54.77,1.0,0.1,0.05
        """
        # 跳过注释行
        if line.startswith('#'):
            return None
        
        # 跳过空行
        if not line or ',' not in line:
            return None
        
        parts = line.split(',')
        if len(parts) >= 5:
            try:
                return {
                    "timestamp": float(parts[0]),
                    "setpoint": float(parts[1]),
                    "input": float(parts[2]),
                    "pwm": float(parts[3]),
                    "error": float(parts[4]),
                    "p": float(parts[5]) if len(parts) > 5 else 1.0,
                    "i": float(parts[6]) if len(parts) > 6 else 0.1,
                    "d": float(parts[7]) if len(parts) > 7 else 0.05
                }
            except (ValueError, IndexError):
                return None
        
        return None


# ============================================================================
# 主程序
# ============================================================================

def find_serial_port() -> str:
    """自动查找可用的串口"""
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("[ERROR] 未找到可用串口")
        return ""
    
    print("[INFO] 可用串口:")
    for i, port in enumerate(ports):
        print(f"  {i}: {port.device} - {port.description}")
    
    if len(ports) == 1:
        return ports[0].device
    
    # 让用户选择
    try:
        choice = input("请选择串口 (输入编号): ").strip()
        idx = int(choice)
        if 0 <= idx < len(ports):
            return ports[idx].device
    except (ValueError, IndexError):
        pass
    
    return ports[0].device


def main():
    """主函数"""
    print("=" * 60)
    print("  LLM PID 自动调参系统 - Python 上位机")
    print("=" * 60)
    print(f"[INFO] 调参模式: {TUNER_MODE}")
    
    # 配置串口
    if not SERIAL_PORT or SERIAL_PORT == "COM3":
        port = find_serial_port()
    else:
        port = SERIAL_PORT
    
    if not port:
        print("[ERROR] 未选择串口，退出")
        sys.exit(1)
    
    # 初始化组件
    serial_bridge = SerialBridge(port, BAUD_RATE)
    if not serial_bridge.connect():
        sys.exit(1)
    
    # 根据 TUNER_MODE 初始化调参器
    if TUNER_MODE == "openclaw":
        print("[INFO] 使用 OpenClaw 本地调参器")
        tuner = OpenClawTuner(OPENCLAW_CLI_PATH, OPENCLAW_SESSION)
    else:
        # 初始化 LLM
        if API_KEY == "your-api-key-here":
            print("[ERROR] 请先配置 API_KEY!")
            serial_bridge.disconnect()
            sys.exit(1)
        print("[INFO] 使用外部 LLM API 调参器")
        tuner = LLMTuner(API_KEY, API_BASE_URL, MODEL_NAME)
    
    data_buffer = DataBuffer(max_size=BUFFER_SIZE)
    
    # 等待 MCU 初始化
    print("[INFO] 等待 MCU 初始化...")
    time.sleep(2)
    
    # 发送状态查询命令
    serial_bridge.send_command("STATUS")
    time.sleep(0.5)
    
    print("[INFO] 开始数据采集和调参...")
    print("-" * 60)
    
    round_num = 0
    tuning_complete = False
    
    try:
        while not tuning_complete and round_num < MAX_TUNING_ROUNDS:
            # 读取串口数据
            line = serial_bridge.read_line()
            if line:
                data = serial_bridge.parse_data_line(line)
                if data:
                    data_buffer.add(data)
                    
                    # 打印实时数据
                    print(f"[DATA] t={data['timestamp']:.0f}ms "
                          f"Set={data['setpoint']:.1f} "
                          f"Input={data['input']:.2f} "
                          f"Error={data['error']:+.2f} "
                          f"PWM={data['pwm']:.1f}")
            
            # 检查缓冲器是否已满
            if data_buffer.is_full():
                round_num += 1
                metrics = data_buffer.calculate_metrics()
                
                print(f"\n[第 {round_num} 轮] 缓冲器已满，开始 AI 分析...")
                print(f"  平均误差: {metrics.get('avg_error', 0):.2f}")
                print(f"  最大误差: {metrics.get('max_error', 0):.2f}")
                
                # 调用 AI 分析
                data_text = data_buffer.to_prompt_data()
                result = tuner.analyze_and_suggest(data_text)
                
                if result:
                    analysis = result.get("analysis", "无分析")
                    new_p = result.get("p", data_buffer.current_pid["p"])
                    new_i = result.get("i", data_buffer.current_pid["i"])
                    new_d = result.get("d", data_buffer.current_pid["d"])
                    status = result.get("status", "TUNING")
                    
                    print(f"  AI 分析: {analysis}")
                    print(f"  新参数: P={new_p}, I={new_i}, D={new_d}")
                    
                    # 发送新参数到 MCU
                    cmd = f"SET P:{new_p} I:{new_i} D:{new_d}"
                    serial_bridge.send_command(cmd)
                    
                    print(f"[第 {round_num} 轮] 参数已更新\n")
                    
                    # 检查是否完成调参
                    if status == "DONE" or metrics.get("avg_error", 999) < MIN_ERROR_THRESHOLD:
                        print("[SUCCESS] 调参完成！误差已降至可接受范围。")
                        tuning_complete = True
                        
                        # 发送最终确认
                        serial_bridge.send_command("STATUS")
                else:
                    print("[WARNING] AI 分析失败，继续采集数据...")
                
                # 清空缓冲器，重新开始采集
                data_buffer.buffer.clear()
            
            # 短暂休眠，避免 CPU 占用过高
            time.sleep(0.01)
    
    except KeyboardInterrupt:
        print("\n[INFO] 用户中断")
    
    finally:
        serial_bridge.disconnect()
        print("[INFO] 程序结束")


if __name__ == "__main__":
    main()
