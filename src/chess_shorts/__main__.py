from __future__ import annotations


def main():
    import sys
    from .capture.window import enable_dpi_awareness
    enable_dpi_awareness()
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        raise SystemExit('PySide6 is missing. Run install_windows.ps1 or: python -m pip install -e .')
    from .ui import Studio
    app = QApplication(sys.argv)
    app.setApplicationName('Chess Shorts Visual Analysis Studio')
    window = Studio()
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
