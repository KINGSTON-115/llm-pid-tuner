# v2.5.0 — 原生桌面工作台

发布日期：2026-10-16

- 独立 Windows 桌面窗口：实时响应与控制输出曲线、PID 参数、调参分析、轮次记录和完整 CSV / JSON 导出。
- 白昼、暗黑与跟随系统主题；简体中文 / English 全局即时切换并保存偏好。运行中切换保留曲线与调参状态。
- 修复白昼模式下侧栏下拉选项文字过浅的问题，主题、语言、运行模式和连接协议列表统一使用当前主题绘制。
- 首次启动可直接运行本地热系统仿真与离线规则，无需 API Key。AI、串口和 Simulink 继续复用现有引擎。
- 发布包同时包含无控制台桌面启动器和终端启动器，保留 `--tui`、`sim`、`tune` 等入口及 OrcaRouter 设置入口。

## 下载与升级

从 [Release](https://github.com/KINGSTON-115/llm-pid-tuner/releases/tag/v2.5.0) 下载
`llm-pid-tuner-v2.5.0-windows-x64.zip`，完整解压，双击 `llm-pid-tuner-desktop.exe`。
必须保留 `_internal` 文件夹。已有用户可将自己的 `config.json` 复制到新目录，保留接口、密钥和高级设置。
通过 `SHA256SUMS.txt` 校验 ZIP。详细操作见 [桌面指南](DESKTOP_GUIDE.md)。

## 验证范围

完整回归 **503 passed, 13 subtests passed**。新增测试实际打开下拉列表、检查普通与选中项文字，
覆盖中英文、反复切换主题、鼠标 / 键盘选择；另外在 Windows 原生窗口及 DPI 缩放环境运行。
桌面核心测试覆盖离线调参、暂停 / 继续 / 停止、目标值更新、完整导出、偏好保存失败回滚和资源释放。
本次真实串口设备、真实 MATLAB 模型及付费模型服务未实测，相关路径使用 DEMO 或测试替身验证。

---

# Release v2.1.0 PRO - 新手可直接上手的硬件调参版

发布日期：2026-03-06

这个版本的重点不是堆功能，而是把项目整理成一个**更稳、更好懂、更适合真实调参**的形态。

## 这次更新了什么

### 1. 发布了新的 `exe`

- Release 资产里的 `llm-pid-tuner.exe` 已更新到最新构建
- 对应的是整理后的主流程版本，更适合直接给 Windows 用户使用

### 2. 调参流程更稳

- 加入了 PID 护栏，避免参数一口气跑飞
- LLM 给不出可用结果时，会使用保底建议继续工作
- 会记录历史最佳稳定结果
- 如果后续某轮建议让效果明显变差，会自动回退到更好的历史参数
- 当系统已经“够好”时，会尽量提前停止，避免越调越坏
- 达到 `GOOD_ENOUGH_*` 阈值后会先保持当前 PID 观察，连续 `REQUIRED_STABLE_ROUNDS` 轮稳定后停止；默认稳定轮数为 3，plain 和 TUI 模式行为一致

### 3. LLM 接口兼容性更好

- 对 OpenAI 兼容接口更友好
- SDK 路径不顺时，会尝试更直接的 HTTP 路径
- 对返回 JSON 的解析更稳，减少格式抖动导致的失败

### 4. 文档重写为“小白优先”

- `README.md` 现在把 `exe` 用法放在最前面
- 明确说明 `config.json` 怎么填
- 加入更清晰的串口、API、常见报错排查说明
- 补充 `docs/zh-CN/PROJECT_DOC.md` 方便后续维护和二次开发

## 最适合谁下载这个版本

- 想直接调 Arduino / ESP32 / 其他串口控制板
- 不想先安装 Python 环境
- 想先用起来，再决定要不要看源码

## 打包版的推荐使用方式

1. 从本 Release 下载 `llm-pid-tuner.exe`
2. 准备好你的硬件，并尽量从 `firmware.cpp` 开始适配
3. 首次运行 exe，让程序生成 `config.json`
4. 填好 API 配置和串口配置
5. 再运行 exe，开始调参

## 配置示例

```json
{
  "SERIAL_PORT": "AUTO",
  "BAUD_RATE": 115200,
  "LLM_API_KEY": "sk-your-key",
  "LLM_API_BASE_URL": "https://api.openai.com/v1",
  "LLM_MODEL_NAME": "gpt-4",
  "LLM_PROVIDER": "openai"
}
```

如果你使用 MiniMax / DeepSeek / Ollama / LM Studio 这类 OpenAI 兼容接口，通常只需要改：

- `LLM_API_BASE_URL`
- `LLM_MODEL_NAME`
- `LLM_API_KEY`

并保持：

```json
{
  "LLM_PROVIDER": "openai"
}
```

## 升级说明

- 如果你之前下载过四天前的旧 Release，请重新下载本次新的 `llm-pid-tuner.exe`
- 如果你之前已经有旧的 `config.json`，建议对照新的 `README.md` 检查字段是否完整
- 如果你是源码用户，建议同步更新 `README.md`、和 `docs/zh-CN/PROJECT_DOC.md`

## 仍然要提醒的事

这个项目的目标是**帮你更轻松地找到可用 PID 参数**，不是替代硬件安全保护。

真实硬件调参时，请务必做好：

- 超温保护
- 传感器异常保护
- 功率级失控保护
- 人工值守

---

下载后建议先看 `README.md:1`，按“3 分钟上手：Windows 打包版”那一节直接开始。
