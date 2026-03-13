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
 *    Python   -> Serial RX -> MCU (更新 PID 参数)
 *
 * ============================================================================
 */

#include <Arduino.h>

// ---------------------------------------------------------------------------
// 全局配置
// ---------------------------------------------------------------------------
#define SERIAL_BAUD 115200      // 串口波特率
#define CONTROL_INTERVAL_MS 50  // 控制周期 (50ms)

// ---------------------------------------------------------------------------
// PID 参数 (全局变量，会被上位机更新)
// ---------------------------------------------------------------------------
float kp = 1.0f;   // 比例系数
float ki = 0.1f;   // 积分系数
float kd = 0.05f; // 微分系数

// ---------------------------------------------------------------------------
// 仿真模型参数 (虚拟被控对象：带惯性的加热系统)
// ---------------------------------------------------------------------------
float setpoint     = 100.0f;  // 目标温度
float current_temp = 20.0f;   // 当前温度 (初始环境温度)
float pwm_output   = 0.0f;    // PWM 输出

// ---------------------------------------------------------------------------
// PWM 安全限制 (电机保护)
// ---------------------------------------------------------------------------
const float PWM_MAX        = 6000.0f;  // PWM 上限 (满转10000)
const float PWM_CHANGE_MAX = 500.0f;   // 每周期最大 PWM 变化量
float prev_pwm_output      = 0.0f;     // 上一次 PWM 输出
float prev_error           = 0.0f;     // 上一次误差 (用于微分计算)
float integral             = 0.0f;     // 积分项

// ---------------------------------------------------------------------------
// 时间管理
// ---------------------------------------------------------------------------
unsigned long last_control_time = 0;
unsigned long timestamp_ms      = 0;

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
    pwm_output   = 0.0f;
    integral     = 0.0f;
    prev_error   = 0.0f;
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
 * 一阶惯性热系统，模拟 PWM 驱动的加热器温度动态。
 *
 * 连续时间物理模型（牛顿加热/冷却定律）：
 *
 *   C * dT/dt = Q_in - Q_loss
 *
 *   其中：
 *     Q_in   = (pwm / PWM_MAX) * Q_max     — 加热功率，正比于 PWM 占空比
 *     Q_loss = k * (T - T_amb)             — 牛顿冷却，正比于与环境的温差
 *
 *   整理后：
 *     dT/dt  = (pwm / PWM_MAX) * α  -  (T - T_amb) * β
 *
 *   其中 α = Q_max / C（最大加热速率），β = k / C（散热时间常数的倒数）
 *
 * 离散化实现（前向欧拉，步长 Δt = CONTROL_INTERVAL_MS）：
 *
 *   heat_input = (pwm / PWM_MAX) * heating_factor   // α·Δt，归一化 PWM 驱动的温升
 *   heat_loss  = (T - T_amb)    * cooling_factor    // β·Δt，散热温降
 *   T[n+1]     = T[n] + heat_input - heat_loss
 *
 * 注：heating_factor / cooling_factor 已将 Δt 吸收在内，为无量纲步长增量系数。
 */
void updateSimulation() {
    const float heating_factor = 2.0f;   // 加热系数
    const float cooling_factor = 0.02f;  // 散热系数
    const float ambient_temp   = 20.0f;  // 环境温度

    // 计算温度变化
    float heat_input = (pwm_output / PWM_MAX) * heating_factor;  // 按实际量程归一化
    float heat_loss  = (current_temp - ambient_temp) * cooling_factor;

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
    // 静态缓冲区：逐字节非阻塞积累，避免 readStringUntil() 阻塞控制循环
    static char    cmd_buf[80];
    static uint8_t cmd_len      = 0;
    static bool    cmd_overflow = false;  // 标记本行是否发生缓冲区溢出

    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\r') continue;               // 兼容 Windows CRLF
        if (c != '\n') {
            if (!cmd_overflow) {
                if (cmd_len < sizeof(cmd_buf) - 1) {
                    cmd_buf[cmd_len++] = c;
                } else {
                    cmd_overflow = true;  // 缓冲区满，标记溢出，后续直到换行都丢弃
                }
            }
            continue;
        }

        // 收到换行符 —— 如果发生过溢出，则整行丢弃并报错
        if (cmd_overflow) {
            cmd_len     = 0;
            cmd_overflow = false;
            Serial.println(F("# ERROR: command too long"));
            continue;
        }

        // 处理完整命令行
        cmd_buf[cmd_len] = '\0';
        cmd_len = 0;
        if (cmd_buf[0] == '\0') continue;      // 空行跳过

        String command = String(cmd_buf);
        command.trim();

        // ----------------------------------------------------------------
        // SET P:1.5 I:0.2 D:0.05  /  SET KP:1.5 KI:0.2 KD:0.05
        // PID 1.5 0.2 0.05
        // ----------------------------------------------------------------
        if (command.startsWith("SET ") || command.startsWith("PID")) {
            // 剥离 "SET " 或 "PID" 前缀（注意 "SET " 含尾随空格，避免误匹配 "SETPOINT"）
            String params = command.substring(3);
            params.trim();

            float new_kp = kp, new_ki = ki, new_kd = kd;

            if (params.indexOf(":") >= 0) {
                // P:/KP:, I:/KI:, D:/KD:
                int p_idx = params.indexOf("P:");
                int i_idx = params.indexOf("I:");
                int d_idx = params.indexOf("D:");

                if (p_idx >= 0) {
                    int end = params.indexOf(" ", p_idx + 2);
                    if (end < 0) end = params.length();
                    new_kp = params.substring(p_idx + 2, end).toFloat();
                }
                if (i_idx >= 0) {
                    int end = params.indexOf(" ", i_idx + 2);
                    if (end < 0) end = params.length();
                    new_ki = params.substring(i_idx + 2, end).toFloat();
                }
                if (d_idx >= 0) {
                    int end = params.indexOf(" ", d_idx + 2);
                    if (end < 0) end = params.length();
                    new_kd = params.substring(d_idx + 2, end).toFloat();
                }
            } else {
                // 纯数字格式：1.5 0.2 0.05
                int space1 = params.indexOf(" ");
                int space2 = params.lastIndexOf(" ");
                if (space1 > 0 && space2 > space1) {
                    new_kp = params.substring(0, space1).toFloat();
                    new_ki = params.substring(space1 + 1, space2).toFloat();
                    new_kd = params.substring(space2 + 1).toFloat();
                } else {
                    Serial.println("# ERROR: PID format invalid (expected: Kp Ki Kd or P:v I:v D:v)");
                    return;
                }
            }

            // 参数范围校验
            const float KP_MAX = 100.0f, KI_MAX = 50.0f, KD_MAX = 50.0f;
            if (new_kp > 0.0f  && new_kp <= KP_MAX &&
                new_ki >= 0.0f && new_ki <= KI_MAX &&
                new_kd >= 0.0f && new_kd <= KD_MAX) {
                kp              = new_kp;
                ki              = new_ki;
                kd              = new_kd;
                integral        = 0.0f;    // 清零防积分饱和
                prev_error      = 0.0f;    // 防微分项在下一周期突变
                prev_pwm_output = 0.0f;    // 防 PWM 变化率限制误触发
                Serial.print("# PID Updated: P=");
                Serial.print(kp, 3);
                Serial.print(" I=");
                Serial.print(ki, 3);
                Serial.print(" D=");
                Serial.println(kd, 3);
            } else {
                Serial.println("# ERROR: PID params rejected (out of valid range)");
            }
        }
        // ----------------------------------------------------------------
        // SETPOINT:100.0
        // ----------------------------------------------------------------
        else if (command.startsWith("SETPOINT")) {
            int colon_idx = command.indexOf(":");
            if (colon_idx > 0) {
                float new_sp = command.substring(colon_idx + 1).toFloat();
                // 限制目标值在合理范围内，防止失控
                if (new_sp > 0.0f && new_sp <= 500.0f) {
                    setpoint = new_sp;
                    Serial.print("# Setpoint Updated: ");
                    Serial.println(setpoint, 2);
                } else {
                    Serial.println("# ERROR: Setpoint rejected (valid range: 0 < sp <= 500)");
                }
            } else {
                Serial.println("# ERROR: SETPOINT format invalid (expected: SETPOINT:value)");
            }
        }
        // ----------------------------------------------------------------
        // RESET
        // ----------------------------------------------------------------
        else if (command == "RESET") {
            kp              = 1.0f;
            ki              = 0.1f;
            kd              = 0.05f;
            integral        = 0.0f;
            prev_error      = 0.0f;
            prev_pwm_output = 0.0f;
            current_temp    = 20.0f;
            pwm_output      = 0.0f;
            Serial.println("# System Reset");
        }
        // ----------------------------------------------------------------
        // STATUS
        // ----------------------------------------------------------------
        else if (command == "STATUS") {
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

        return; // 每次 loop() 最多处理一条完整命令，其余留待下次
    }
}
