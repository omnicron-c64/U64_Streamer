#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

python="${PYTHON:-python3}"
"$python" -c 'import sys, tkinter; sys.exit(not ((3, 10) <= sys.version_info[:2] <= (3, 13)))' || {
    echo "Use Python 3.10–3.13 with Tkinter, or set PYTHON to a compatible interpreter." >&2
    exit 1
}
command -v objdump >/dev/null || {
    echo "Install the binutils package (objdump is required by PyInstaller)." >&2
    exit 1
}

venv=".build-venv-linux"
if [[ ! -x "$venv/bin/python" ]]; then
    "$python" -m venv "$venv"
fi
build_python="$venv/bin/python"
"$build_python" -m pip install -r requirements-build.txt
"$build_python" -m PyInstaller --noconfirm --clean --onedir --name UCI-Capture \
    --copy-metadata av --copy-metadata pygame --copy-metadata numpy \
    --specpath build --workpath build/linux --distpath dist/linux uci_capture.py

bundle="dist/linux/UCI-Capture"
cp README.md THIRD_PARTY.md U64_Stream_Capture_Palette.md LICENSE "$bundle/"
"$bundle/UCI-Capture" --self-test
archive="dist/UCI-Capture-linux-$(uname -m).tar.gz"
tar -czf "$archive" -C dist/linux UCI-Capture
sha256sum "$archive"
