# 字体配置

TUI 支持任何等宽字体，但安装 **JetBrains Mono Nerd Font** 后可显示完整图标集。
项目根目录的 `font/` 文件夹中已内置该字体文件。

## 方式一 — 安装内置字体

1. 打开项目根目录下的 `font/` 文件夹。
2. 右键 `JetBrainsMonoNerdFont-Regular.ttf` → **为所有用户安装**。

## 方式二 — 从官网下载

访问 https://www.nerdfonts.com/font-downloads，搜索 **JetBrainsMono**，
下载 zip 后解压，同样右键 `.ttf` 文件选择安装。

## 配置终端使用该字体

| 终端 | 操作路径 |
|------|----------|
| **Windows Terminal** | 设置 → 配置文件 → 外观 → 字体 → 输入 `JetBrainsMono Nerd Font Mono` |
| **VS Code 内置终端** | 设置项 `terminal.integrated.fontFamily` 填写 `"JetBrainsMono Nerd Font Mono"` |
| **其他终端** | 在该终端自身的字体偏好设置中选择已安装的字体名称 |

修改字体后重启终端生效。
