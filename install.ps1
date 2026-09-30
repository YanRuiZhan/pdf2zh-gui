param(
    [string]$InstallDir = "$env:LOCALAPPDATA\pdf2zh-gui",
    [string]$RepoOwner = "YanRuiZhan",
    [string]$RepoName = "pdf2zh-gui",
    [string]$Branch = "main",
    [string]$Python = $env:PDF2ZH_GUI_PYTHON,
    [switch]$NoVenv
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if ($env:PDF2ZH_GUI_NO_VENV -eq "1") { $NoVenv = $true }

function Test-PythonVersion {
    param([string]$Exe)

    try {
        $out = & $Exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    }
    catch {
        return $false
    }
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $false }
    try {
        $v = [version]$out.Trim()
    }
    catch {
        return $false
    }
    return $v -ge [version]"3.11"
}

function Resolve-Python {
    param([string]$Preferred)

    if ($Preferred) {
        if (-not (Test-Path -LiteralPath $Preferred)) {
            throw "PDF2ZH_GUI_PYTHON 指向的文件不存在：$Preferred"
        }
        return (Resolve-Path -LiteralPath $Preferred).Path
    }

    $candidates = @()
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        foreach ($tag in @("-3.13", "-3.12", "-3.11", "-3")) {
            $resolved = & $pyLauncher.Source $tag -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $resolved) { $candidates += $resolved.Trim() }
        }
    }
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) { $candidates += $pythonCmd.Source }
    foreach ($minor in @(13, 12, 11)) {
        $candidates += "$env:LOCALAPPDATA\Programs\Python\Python3$minor\python.exe"
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if ((Test-Path -LiteralPath $candidate) -and (Test-PythonVersion $candidate)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "未找到 Python 3.11+。请先安装 Python，或设置 PDF2ZH_GUI_PYTHON 指向 python.exe。"
}

function Resolve-Git {
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if ($gitCmd) { return $gitCmd.Source }
    throw "未找到 Git。请先安装 Git for Windows，并确保 git 命令已加入 PATH。"
}

function Sync-AppRepository {
    param([string]$Destination)

    $destinationPath = [IO.Path]::GetFullPath($Destination)
    $parentDir = Split-Path -Parent $destinationPath
    $folderName = Split-Path -Leaf $destinationPath
    if (-not (Test-Path -LiteralPath $parentDir)) {
        New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
    }
    $repoUrl = "https://github.com/$RepoOwner/$RepoName.git"

    # Existing checkout: fast-forward in place so the virtualenv survives.
    if (Test-Path -LiteralPath (Join-Path $destinationPath ".git")) {
        Write-Host "更新已有安装：$destinationPath"
        & $script:GitExe -C $destinationPath fetch --quiet origin $Branch
        if ($LASTEXITCODE -eq 0) {
            & $script:GitExe -C $destinationPath checkout --quiet $Branch
            & $script:GitExe -C $destinationPath merge --ff-only "origin/$Branch"
            if ($LASTEXITCODE -eq 0) { return }
        }
        Write-Warning "增量更新失败，改为全新克隆。"
    }

    $suffix = [Guid]::NewGuid().ToString("N")
    $stagingDir = Join-Path $parentDir "$folderName.installing-$suffix"
    $backupDir = Join-Path $parentDir "$folderName.backup-$suffix"
    Write-Host "克隆 $repoUrl ($Branch)"

    try {
        & $script:GitExe clone --branch $Branch --single-branch $repoUrl $stagingDir
        if ($LASTEXITCODE -ne 0) { throw "Git 仓库克隆失败。" }

        if (Test-Path -LiteralPath $destinationPath) {
            Move-Item -LiteralPath $destinationPath -Destination $backupDir
        }
        Move-Item -LiteralPath $stagingDir -Destination $destinationPath

        # carry the previous virtualenv over so we do not re-download wheels
        $oldVenv = Join-Path $backupDir ".venv"
        if (Test-Path -LiteralPath $oldVenv) {
            Move-Item -LiteralPath $oldVenv -Destination (Join-Path $destinationPath ".venv")
        }
    }
    catch {
        if (
            -not (Test-Path -LiteralPath $destinationPath) -and
            (Test-Path -LiteralPath $backupDir)
        ) {
            Move-Item -LiteralPath $backupDir -Destination $destinationPath
        }
        if (Test-Path -LiteralPath $stagingDir) {
            Remove-Item -LiteralPath $stagingDir -Recurse -Force
        }
        throw
    }

    if (Test-Path -LiteralPath $backupDir) {
        try { Remove-Item -LiteralPath $backupDir -Recurse -Force }
        catch { Write-Warning "新版本已安装，但旧安装备份未能删除：$backupDir" }
    }
}

function Initialize-Venv {
    param([string]$InstallPath, [string]$BasePython)

    $venvDir = Join-Path $InstallPath ".venv"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Host "创建虚拟环境: $venvDir"
        & $BasePython -m venv $venvDir
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
            throw "创建虚拟环境失败。可加 -NoVenv 改为安装到当前 Python。"
        }
    }
    else {
        Write-Host "复用已有虚拟环境: $venvDir"
    }
    return (Resolve-Path -LiteralPath $venvPython).Path
}

function New-DesktopShortcut {
    param([string]$InstallPath, [string]$PythonExe)

    $pythonw = Join-Path (Split-Path -Parent $PythonExe) "pythonw.exe"
    if (-not (Test-Path -LiteralPath $pythonw)) { $pythonw = $PythonExe }

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
    if (Test-Path -LiteralPath $iconPath) { $shortcut.IconLocation = "$iconPath,0" }
    $shortcut.Save()
    return $shortcutPath
}

$basePython = Resolve-Python -Preferred $Python
Write-Host "使用 Python: $basePython"
$script:GitExe = Resolve-Git
Write-Host "使用 Git: $script:GitExe"

Sync-AppRepository -Destination $InstallDir

if ($NoVenv) {
    Write-Warning "已跳过虚拟环境，依赖将装入 $basePython 所在环境。"
    $runtimePython = $basePython
}
else {
    $runtimePython = Initialize-Venv -InstallPath $InstallDir -BasePython $basePython
}

Write-Host "安装依赖（首次约需几分钟）..."
& $runtimePython -m pip install --upgrade pip --quiet
& $runtimePython -m pip install -r (Join-Path $InstallDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败。" }

$shortcut = New-DesktopShortcut -InstallPath $InstallDir -PythonExe $runtimePython

Write-Host ""
Write-Host "pdf2zh-gui 已安装完成。"
Write-Host "安装目录  : $InstallDir"
Write-Host "运行环境  : $runtimePython"
Write-Host "桌面快捷方式: $shortcut"
Write-Host "首次运行后，请在 GUI 内添加自己的翻译服务配置。"
