# NexScope Amazon Intelligence 安装说明

需要 Windows x64 或 macOS x64/arm64，以及 Python 3.11 或更高版本。安装包不会修改系统 PATH。

- macOS：双击 `install.command`，或在终端运行 `./install.command`。
- Windows：双击 `install.bat`。

安装器只从包内 `wheels/` 安装固定依赖，然后显示安装、更新、状态、断开和卸载菜单。连接账号时会打开 NexScope 网页完成授权。凭据写入系统钥匙串/凭据管理器，不会保存到配置文件。若浏览器未自动打开，请复制终端显示的网址和短码自行打开。

常用诊断：`.runtime-venv/bin/python runtime/installer.py doctor`（Windows 使用 `.runtime-venv\Scripts\python.exe`）。诊断结果不包含密钥。
