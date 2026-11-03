#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AncelLauncher — Qt UI (PySide6).

  pip install PySide6 pillow
  python AncelLauncher.pyw
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt
    except ImportError:
        print("Нужен PySide6:\n  pip install PySide6")
        sys.exit(1)

    from ui_qt.main_window import MainWindow, app_icon

    # High-DPI
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    if sys.platform == "win32":
        # отдельный AppUserModelID, иначе в панели задач будет иконка Python
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AncelLauncher.App")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("AncelLauncher")
    app.setStyle("Fusion")
    app.setWindowIcon(app_icon())  # иконка у главного окна и всех диалогов
    from ui_qt.theme import apply_app_palette
    apply_app_palette(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
