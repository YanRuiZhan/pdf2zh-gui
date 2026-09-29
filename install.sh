#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${PDF2ZH_GUI_REPO_URL:-https://github.com/YanRuiZhan/pdf2zh-gui.git}"
BRANCH="${PDF2ZH_GUI_BRANCH:-main}"
APP_DIR="${PDF2ZH_GUI_INSTALL_DIR:-$HOME/.local/share/pdf2zh-gui}"

if [[ -n "${PDF2ZH_GUI_PYTHON:-}" ]]; then
    PYTHON_BIN="$PDF2ZH_GUI_PYTHON"
else
    PYTHON_BIN=""
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 \
            && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
            PYTHON_BIN="$(command -v "$candidate")"
            break
        fi
    done
fi

if [[ -z "$PYTHON_BIN" ]]; then
    echo "未找到 Python 3.11+。请安装 Python，或设置 PDF2ZH_GUI_PYTHON。" >&2
    exit 1
fi
if ! command -v git >/dev/null 2>&1; then
    echo "未找到 Git。请先安装 git。" >&2
    exit 1
fi

mkdir -p "$(dirname "$APP_DIR")"
if [[ -d "$APP_DIR/.git" ]]; then
    echo "更新已有安装：$APP_DIR"
    if git -C "$APP_DIR" diff --quiet && git -C "$APP_DIR" diff --cached --quiet; then
        git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
        git -C "$APP_DIR" merge --ff-only "origin/$BRANCH"
    else
        echo "检测到本地修改，保留当前文件并跳过 Git 更新。" >&2
    fi
else
    if [[ -e "$APP_DIR" ]]; then
        echo "安装目录已存在但不是 Git 仓库：$APP_DIR" >&2
        exit 1
    fi
    echo "克隆 $REPO_URL ($BRANCH)"
    git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$APP_DIR"
fi

APP_DIR="$(cd "$APP_DIR" && pwd)"
VENV_DIR="$APP_DIR/.venv"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    echo "创建虚拟环境：$VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

echo "安装 Python 依赖（首次约需几分钟）..."
"$VENV_DIR/bin/python" -m pip install --upgrade pip --quiet
"$VENV_DIR/bin/python" "$APP_DIR/scripts/install_dependencies.py"

chmod +x "$APP_DIR/run_pdf2zh.sh"
APPLICATIONS_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$APPLICATIONS_DIR/pdf2zh.desktop"
mkdir -p "$APPLICATIONS_DIR"

write_desktop_file() {
    local target="$1"
    mkdir -p "$(dirname "$target")"
    cat > "$target" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=pdf2zh
Comment=PDF 文献翻译工具
TryExec=$APP_DIR/run_pdf2zh.sh
Exec="$APP_DIR/run_pdf2zh.sh" %F
Path=$APP_DIR
Icon=$APP_DIR/pdf_translate_icon_full.png
Terminal=false
Categories=Office;Utility;
MimeType=application/pdf;
StartupNotify=true
StartupWMClass=pdf2zh
EOF
    chmod +x "$target"
}

write_desktop_file "$DESKTOP_FILE"
if [[ -d "$HOME/Desktop" ]]; then
    write_desktop_file "$HOME/Desktop/pdf2zh.desktop"
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPLICATIONS_DIR" >/dev/null 2>&1 || true
fi

echo
echo "pdf2zh-gui Linux 版本已安装。"
echo "安装目录：$APP_DIR"
echo "桌面入口：$DESKTOP_FILE"
echo "首次启动后，请在 GUI 内添加自己的翻译服务配置。"
