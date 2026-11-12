"""Application entry point (palette + event loop). Run with:  python -m zvcard.gui"""
from __future__ import annotations

import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def dark_palette() -> QPalette:
    p = QPalette()
    roles = {QPalette.ColorRole.Window: (37, 37, 38), QPalette.ColorRole.WindowText: (225, 225, 225),
             QPalette.ColorRole.Base: (30, 30, 30), QPalette.ColorRole.AlternateBase: (45, 45, 48),
             QPalette.ColorRole.Text: (225, 225, 225), QPalette.ColorRole.Button: (55, 55, 58),
             QPalette.ColorRole.ButtonText: (225, 225, 225), QPalette.ColorRole.Highlight: (38, 110, 190),
             QPalette.ColorRole.HighlightedText: (255, 255, 255), QPalette.ColorRole.ToolTipBase: (50, 50, 50),
             QPalette.ColorRole.ToolTipText: (225, 225, 225)}
    for role, rgb in roles.items():
        p.setColor(role, QColor(*rgb))
    return p


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    win = MainWindow()
    win.show()
    return app.exec()
