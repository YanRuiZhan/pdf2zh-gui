#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

if [[ -n "${PDF2ZH_GUI_PYTHON:-}" ]]; then
    PYTHON_BIN="$PDF2ZH_GUI_PYTHON"
elif [[ -x "$APP_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$APP_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1 \
    && python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
    PYTHON_BIN="$(command -v python3)"
else
    echo "未找到 Python 3.11+。请设置 PDF2ZH_GUI_PYTHON，或先运行 install.sh。" >&2
    exit 1
fi

export PDF2ZH_LAYOUT_SCALE="${PDF2ZH_LAYOUT_SCALE:-1.5}"
exec env -u PYTHONPATH PYTHONNOUSERSITE=1 "$PYTHON_BIN" "$APP_DIR/pdf2zh_gui.py" "$@"
