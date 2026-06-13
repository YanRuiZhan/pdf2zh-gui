# pdf2zh-gui

一个基于 [pdf2zh](https://github.com/Byaidu/PDFMathTranslate) 的 Windows 桌面 GUI（应用名：PDF Translator），用来把英文 PDF 文献翻译成中文并输出双语 PDF / 纯译文 PDF。

项目重点是双击即用：不需要启动 Web 服务，也不需要浏览器页面。

## 功能

- 拖拽或选择多个 PDF 文件
- 主界面分为「PDF 翻译」「单词速查」「翻译设置」三个选项卡
- 支持页码范围、输出目录、输出文件类型和并发线程设置
- 翻译注意事项：把自定义要求（默认「保留所有英文人名、地名不翻译」）注入翻译提示词，自动记住上次填写内容
- 单词速查：调用所选 AI 服务即时查词，支持英文/中文双向、音标和领域释义，结果区加高便于阅读
- 模型列表支持「获取可用模型」后通过右侧下拉选择；长列表会限高并滚动，不会自动覆盖输入框
- Ctrl+滚轮 缩放整个界面（70%–160%），Ctrl+0 复原；自动记住缩放比例和窗口尺寸
- 自动保存翻译服务、语言、输出模式、页码、线程、缓存、注意事项和输出目录等设置
- 支持 OpenAI 兼容、Claude/Anthropic 兼容、DeepSeek、Gemini、智谱、SiliconFlow、Grok、Groq、Ollama、Azure OpenAI 等服务
- 支持 LongCat 这类 Anthropic-compatible `/messages` 网关
- 自动创建桌面快捷方式
- GUI 内管理模型服务配置，API Key 只保存在本机用户配置目录

## 一键安装

在 Windows PowerShell 中运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/YanRuiZhan/pdf2zh-gui/main/install.ps1 | iex"
```

安装脚本会：

- 下载本仓库到 `%LOCALAPPDATA%\pdf2zh-gui`
- 安装 `requirements.txt` 中的 Python 依赖
- 在桌面创建 `PDF翻译.lnk`
- 使用项目内置图标

如果你想指定 Python，可先设置：

```powershell
$env:PDF2ZH_GUI_PYTHON="C:\Path\To\python.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/YanRuiZhan/pdf2zh-gui/main/install.ps1 | iex"
```

> 运行远程脚本前，建议先打开 `install.ps1` 检查内容。脚本不会读取、提交或上传你的 API Key。

## Codex 一键配置提示词

把下面这段粘贴给 Codex：

```text
请在这台 Windows 电脑上安装 pdf2zh-gui。不要读取、打印或提交我的 API Key。

请执行：
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/YanRuiZhan/pdf2zh-gui/main/install.ps1 | iex"

安装完成后，确认桌面存在“PDF翻译”快捷方式，并告诉我安装目录。
```

## 手动运行

```powershell
git clone https://github.com/YanRuiZhan/pdf2zh-gui.git
cd pdf2zh-gui
python -m pip install -r requirements.txt
python pdf2zh_gui.py
```

也可以双击：

```text
PDF翻译.bat
```

## 配置翻译服务

首次启动后，在 GUI 里点击“添加模型”。

LongCat / Claude 兼容服务可选：

- 类型：`Claude 兼容`
- Base URL：填写你的服务商给出的 Anthropic-compatible 地址
- API Key：填写你自己的 Key
- 模型名称：填写你的模型名

这些配置保存在本机：

```text
%USERPROFILE%\.config\PDFMathTranslate\gui_services.json
%USERPROFILE%\.config\PDFMathTranslate\gui_prefs.json
%USERPROFILE%\.config\PDFMathTranslate\config.json
```

这些文件包含个人配置和密钥，不应提交到 GitHub。

## 翻译注意事项与单词速查

- **注意事项**：在「翻译设置」最后一行填写对 AI 的要求（多条用分号或换行分隔），会作为规则注入每次翻译的提示词。修改注意事项后同一文档会重新翻译（缓存按提示词区分），留空则使用 pdf2zh 原始提示词。
- **单词速查**：在「单词速查」卡片输入英文或中文按 Enter，即用当前选中的 ★ AI 服务返回词典式释义。需要先配置一个自定义 AI 服务（google/bing 翻译不支持查词）。

## 说明

本项目在 GUI 启动翻译时会对 pdf2zh 做两个运行时兼容补丁：

- 修复部分 PDF 渲染时 `PDFPageInterpreterEx.scs` 未初始化的问题
- 对 Base URL 含 `/anthropic` 或 `anthropic.com` 的 OpenAI-like 服务，走 Anthropic `/messages` 请求，兼容 LongCat 等网关

这些补丁只在本 GUI 进程内生效，不会修改你 Python 环境里的 site-packages 文件。

## 文件

- `pdf2zh_gui.py`：主程序
- `PDF翻译.bat`：本地启动脚本
- `install.ps1`：Windows 一键安装脚本
- `requirements.txt`：依赖列表
- `pdf_translate_icon_full.ico` / `pdf_translate_icon_full.png`：应用和快捷方式图标
