/**
 * ============================================================================
 * firmware.cpp - 基于 LLM 的 PID 自动调参系统 - MCU 端固件
 * ============================================================================
 * 
 * 作者: KINGSTON-115
 * 功能：内置仿真模型 + 数据上报 + 指令接收
 * 目标平台：Arduino / PlatformIO
 * 
 * 【数据流说明】
 * MCU (下位机) -> Serial TX -> Python (上位机) -> LLM (决策大脑)
 *                                           |
 *                                           v
 *                                    LLM 返回新参数
 *                                           |
 *                                           v
 * Python -> Serial RX -> MCU (更新 PID 参数)
 * 
 * ============================================================================
 */

#include <Arduino.h>

// ---------------------------------------------------------------------------
// 全局配置
// ---------------------------------------------------------------------------
#define SERIAL_BAUD 115200        // 串口波特率
#define CONTROL_INTERVAL_MS 50    // 控制周期 (50ms)

// ---------------------------------------------------------------------------
// PID 参数 (全局变量，会被上位机更新)
// ---------------------------------------------------------------------------
float kp = 1.0f;   // 比例系数
float ki = 0.1f;   // 积分系数
float kd = 0.05f; // 微分系数

// ---------------------------------------------------------------------------
// 仿真模型参数 (虚拟被控对象：带惯性的加热系统)
// ---------------------------------------------------------------------------
float setpoint = 100.0f;      // 目标温度
float current_temp = 20.0f;   // 当前温度 (初始环境温度)
float pwm_output = 0.0f;      // PWM 输出

// ---------------------------------------------------------------------------
// PWM 安全限制 (电机保护)
// ---------------------------------------------------------------------------
const float PWM_MAX = 6000.0f;       // PWM 上限 (满转10000)
const float PWM_CHANGE_MAX = 500.0f; // 每周期最大 PWM 变化量
float prev_pwm_output = 0.0f;        // 上一次 PWM 输出
float prev_error = 0.0f;      // 上一次误差 (用于微分计算)
float integral = 0.0f;        // 积分项

// ---------------------------------------------------------------------------
// 时间管理
// ---------------------------------------------------------------------------
unsigned long last_control_time = 0;
unsigned long timestamp_ms = 0;

// ---------------------------------------------------------------------------
// 函数声明
// ---------------------------------------------------------------------------
void updateSimulation();       // 更新仿真模型
void computePID();            // 计算 PID 输出
void processSerialCommand();   // 处理上位机指令
void sendDataToSerial();       // 上报数据给上位机

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------
void setup() {
    Serial.begin(SERIAL_BAUD);
    while (!Serial) {
        ; // 等待串口就绪
    }
    
    // 初始化仿真模型
    current_temp = 20.0f;
    pwm_output = 0.0f;
    integral = 0.0f;
    prev_error = 0.0f;
    timestamp_ms = 0;
    
    // 打印初始化信息
    Serial.println("# LLM PID Tuner Firmware v1.0 Ready");
    Serial.println("# Format: timestamp_ms,setpoint,input,pwm,error,p,i,d");
}

// ---------------------------------------------------------------------------
// 主循环
// ---------------------------------------------------------------------------
void loop() {
    unsigned long current_time = millis();
    
    // 每 50ms 执行一次控制循环
    if (current_time - last_control_time >= CONTROL_INTERVAL_MS) {
        last_control_time = current_time;
        timestamp_ms = current_time;
        
        // 1. 计算 PID 控制输出
        computePID();
        
        // 2. 更新仿真模型 (基于 PID 输出)
        updateSimulation();
        
        // 3. 上报数据给上位机 (Python)
        sendDataToSerial();
    }
    
    // 4. 监听并处理上位机指令
    processSerialCommand();
}

// ---------------------------------------------------------------------------
// 计算 PID 控制输出
// ---------------------------------------------------------------------------
void computePID() {
    // 计算误差
    float error = setpoint - current_temp;
    
    // 积分项 (带抗饱和限制)
    integral += error * (CONTROL_INTERVAL_MS / 1000.0f);
    
    // 限制积分项范围，防止积分饱和
    integral = constrain(integral, -100.0f, 100.0f);
    
    // 微分项
    float derivative = (error - prev_error) / (CONTROL_INTERVAL_MS / 1000.0f);
    
    // PID 输出计算
    float pid_output = kp * error + ki * integral + kd * derivative;
    
    // PWM 变化率限制 (防突变)
    float pwm_delta = pid_output - prev_pwm_output;
    if (fabs(pwm_delta) > PWM_CHANGE_MAX) {
        pid_output = prev_pwm_output + (pwm_delta > 0 ? PWM_CHANGE_MAX : -PWM_CHANGE_MAX);
    }
    
    // 限制 PWM 输出范围 (0-PWM_MAX)
    pwm_output = constrain(pid_output, 0.0f, PWM_MAX);
    prev_pwm_output = pwm_output;
    
    // 保存当前误差作为下一次微分的输入
    prev_error = error;
}

// ---------------------------------------------------------------------------
// 仿真模型：带惯性的加热系统
// ---------------------------------------------------------------------------
/**
 * 模型说明：
 * 这是一个简单的一阶惯性系统，模拟加热器的温度变化。
 * 温度变化率与 (PWM输出 - 当前温度) 成正比。
 * 
 * 物理模型：
 *   dT/dt = (T_desired - T_current) * heating_rate - (T_current - T_ambient) * cooling_rate
 * 
 * 简化实现：
 *   new_temp = current_temp + (pwm_output/255.0 * heating_factor - (current_temp - ambient) * cooling_factor)
 */
void updateSimulation() {
    const float heating_factor = 2.0f;   // 加热系数
    const float cooling_factor = 0.02f; // 散热系数
    const float ambient_temp = 20.0f;   // 环境温度
    
    // 计算温度变化
    float heat_input = (pwm_output / 255.0f) * heating_factor;
    float heat_loss = (current_temp - ambient_temp) * cooling_factor;
    
    // 更新温度
    current_temp += heat_input - heat_loss;
}

// ---------------------------------------------------------------------------
// 上报数据给上位机 (CSV 格式)
// ---------------------------------------------------------------------------
/**
 * 数据格式说明：
 * timestamp_ms, setpoint, input_value, pwm_output, error, kp, ki, kd
 * 
 * 示例：
 * 5000,100.0,45.23,127.5,54.77,1.0,0.1,0.05
 */
void sendDataToSerial() {
    float error = setpoint - current_temp;
    
    Serial.print(timestamp_ms);
    Serial.print(",");
    Serial.print(setpoint, 2);
    Serial.print(",");
    Serial.print(current_temp, 2);
    Serial.print(",");
    Serial.print(pwm_output, 2);
    Serial.print(",");
    Serial.print(error, 2);
    Serial.print(",");
    Serial.print(kp, 3);
    Serial.print(",");
    Serial.print(ki, 3);
    Serial.print(",");
    Serial.println(kd, 3);
}

// ---------------------------------------------------------------------------
// 处理上位机指令
// ---------------------------------------------------------------------------
/**
 * 指令格式：
 * SET P:1.5 I:0.2 D:0.05
 * SET KP:1.5 KI:0.2 KD:0.05
 * PID 1.5 0.2 0.05
 * 
 * 说明：
 * - 接收到新参数后，立即更新全局 PID 变量
 * - 务必清零积分项 (integral)，防止积分饱和导致的失控
 */
void processSerialCommand() {
    if (!Serial.available()) {
        return;
    }
    
    // 读取一行指令
    String command = Serial.readStringUntil('\n');
    command.trim();
    
    if (command.length() == 0) {
        return;
    }
    
    // 解析指令
    // 格式1: SET P:1.5 I:0.2 D:0.05
    // 格式2: SET KP:1.5 KI:0.2 KD:0.05
    
    if (command.startsWith("SET") || command.startsWith("PID")) {
        // 提取参数部分
        String params = command;
        if (command.startsWith("SET")) {
            params = command.substring(3);
        }
        params.trim();
        
        // 解析 P/KP
        float new_kp = kp;
        float new_ki = ki;
        float new_kd = kd;
        
        // 解析各个参数
        int p_idx = params.indexOf("P:");
        int i_idx = params.indexOf("I:");
        int d_idx = params.indexOf("D:");
        
        if (p_idx >= 0) {
            int p_end = params.indexOf(" ", p_idx + 2);
            if (p_end < 0) p_end = params.length();
            new_kp = params.substring(p_idx + 2, p_end).toFloat();
        }
        
        if (i_idx >= 0) {
            int i_end = params.indexOf(" ", i_idx + 2);
            if (i_end < 0) i_end = params.length();
            new_ki = params.substring(i_idx + 2, i_end).toFloat();
        }
        
        if (d_idx >= 0) {
            int d_end = params.indexOf(" ", d_idx + 2);
            if (d_end < 0) d_end = params.length();
            new_kd = params.substring(d_idx + 2, d_end).toFloat();
        }
        
        // 解析纯数字格式: PID 1.5 0.2 0.05
        if (params.indexOf(":") < 0) {
            int space1 = params.indexOf(" ");
            int space2 = params.lastIndexOf(" ");
            if (space1 > 0 && space2 > space1) {
                new_kp = params.substring(0, space1).toFloat();
                new_ki = params.substring(space1 + 1, space2).toFloat();
                new_kd = params.substring(space2 + 1).toFloat();
            }
        }
        
        // 更新 PID 参数
        kp = new_kp;
        ki = new_ki;
        kd = new_kd;
        
        // 【关键】清零积分项，防止积分饱和
        integral = 0.0f;
        
        // 确认参数已更新
        Serial.print("# PID Updated: P=");
        Serial.print(kp, 3);
        Serial.print(" I=");
        Serial.print(ki, 3);
        Serial.print(" D=");
        Serial.println(kd, 3);
    }
    else if (command.startsWith("SETPOINT")) {
        // 设置新的目标值
        int colon_idx = command.indexOf(":");
        if (colon_idx > 0) {
            float new_setpoint = command.substring(colon_idx + 1).toFloat();
            setpoint = new_setpoint;
            Serial.print("# Setpoint Updated: ");
            Serial.println(setpoint, 2);
        }
    }
    else if (command == "RESET") {
        // 重置所有参数
        kp = 1.0f;
        ki = 0.1f;
        kd = 0.05f;
        integral = 0.0f;
        current_temp = 20.0f;
        pwm_output = 0.0f;
        Serial.println("# System Reset");
    }
    else if (command == "STATUS") {
        // 查询当前状态
        Serial.print("# STATUS: Kp=");
        Serial.print(kp, 3);
        Serial.print(" Ki=");
        Serial.print(ki, 3);
        Serial.print(" Kd=");
        Serial.print(kd, 3);
        Serial.print(" Setpoint=");
        Serial.print(setpoint, 2);
        Serial.print(" Temp=");
        Serial.println(current_temp, 2);
    }
}
