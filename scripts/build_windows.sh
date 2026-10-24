#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pip install -r requirements.txt
rm -rf build dist
python3 -m PyInstaller AncelLauncher.spec --noconfirm
echo "Done: dist/AncelLauncher/AncelLauncher.exe (on Windows target)"
