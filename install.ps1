param(
    [string]$InstallDir = "$env:LOCALAPPDATA\pdf2zh-gui",
    [string]$RepoOwner = "YanRuiZhan",
    [string]$RepoName = "pdf2zh-gui",
    [string]$Branch = "main",
    [string]$Python = $env:PDF2ZH_GUI_PYTHON
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Resolve-Python {
    param([string]$Preferred)

    if ($Preferred -and (Test-Path -LiteralPath $Preferred)) {
        return (Resolve-Path -LiteralPath $Preferred).Path
    }

    $candidates = @(
        "$env:USERPROFILE\.conda\envs\agent_work_env\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $resolved = & $pyLauncher.Source -3 -c "import sys; print(sys.executable)"
        if ($LASTEXITCODE -eq 0 -and $resolved) {
            return $resolved.Trim()
        }
    }

    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        return $pythonCmd.Source
    }

    throw "未找到 Python。请先安装 Python 3.11+，或设置 PDF2ZH_GUI_PYTHON 指向 python.exe。"
}

function Copy-AppFiles {
    param([string]$Destination)

    $scriptDir = if ($PSCommandPath) { Split-Path -Parent $PSCommandPath } else { "" }
    if ($scriptDir -and (Test-Path -LiteralPath (Join-Path $scriptDir "pdf2zh_gui.py"))) {
        $sourceDir = $scriptDir
    } else {
        $tmpRoot = Join-Path $env:TEMP "pdf2zh-gui-install"
        $zipPath = Join-Path $tmpRoot "source.zip"
        if (Test-Path -LiteralPath $tmpRoot) {
            Remove-Item -LiteralPath $tmpRoot -Recurse -Force
        }
        New-Item -ItemType Directory -Path $tmpRoot | Out-Null

        $zipUrl = "https://github.com/$RepoOwner/$RepoName/archive/refs/heads/$Branch.zip"
        Write-Host "下载 $zipUrl"
        Invoke-WebRequest -UseBasicParsing -Uri $zipUrl -OutFile $zipPath
        Expand-Archive -LiteralPath $zipPath -DestinationPath $tmpRoot -Force
        $sourceDir = (Get-ChildItem -LiteralPath $tmpRoot -Directory | Select-Object -First 1).FullName
    }

    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Destination | Out-Null
    Copy-Item -Path (Join-Path $sourceDir "*") -Destination $Destination -Recurse -Force
}

function New-DesktopShortcut {
    param(
        [string]$InstallPath,
        [string]$PythonExe
    )

    $pythonw = Join-Path (Split-Path -Parent $PythonExe) "pythonw.exe"
    if (-not (Test-Path -LiteralPath $pythonw)) {
        $pythonw = $PythonExe
    }

    $desktop = [Environment]::GetFolderPath("DesktopDirectory")
    $shortcutPath = Join-Path $desktop "PDF Translator.lnk"
    $legacyShortcutPath = Join-Path $desktop "PDF翻译.lnk"
    $scriptPath = Join-Path $InstallPath "pdf2zh_gui.py"
    $iconPath = Join-Path $InstallPath "pdf_translate_icon_full.ico"

    if (Test-Path -LiteralPath $legacyShortcutPath) {
        Remove-Item -LiteralPath $legacyShortcutPath -Force
    }

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = '"' + $scriptPath + '"'
    $shortcut.WorkingDirectory = $InstallPath
    if (Test-Path -LiteralPath $iconPath) {
        $shortcut.IconLocation = "$iconPath,0"
    }
    $shortcut.Save()
}

$pythonExe = Resolve-Python -Preferred $Python
Write-Host "使用 Python: $pythonExe"

Copy-AppFiles -Destination $InstallDir

Write-Host "安装依赖..."
& $pythonExe -m pip install -r (Join-Path $InstallDir "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "依赖安装失败。"
}

New-DesktopShortcut -InstallPath $InstallDir -PythonExe $pythonExe

Write-Host ""
Write-Host "pdf2zh-gui 已安装完成。"
Write-Host "安装目录: $InstallDir"
Write-Host "桌面快捷方式: PDF Translator.lnk"
Write-Host "首次运行后，请在 GUI 内添加自己的翻译服务配置。"
