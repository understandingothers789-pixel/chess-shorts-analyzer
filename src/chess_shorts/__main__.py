from __future__ import annotations


def main():
    import sys
    import logging
    from logging.handlers import RotatingFileHandler
    from pathlib import Path
    log_dir = Path.home() / 'ChessShorts' / 'logs'
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_dir / 'activity.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logging.basicConfig(level=logging.INFO, handlers=[handler])
        logging.info('Application started')
    except OSError:
        logging.basicConfig(level=logging.INFO)
    from .capture.window import enable_dpi_awareness
    enable_dpi_awareness()
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        raise SystemExit('PySide6 is missing. Run install_windows.ps1 or: python -m pip install -e .')
    from PySide6.QtGui import QIcon
    if sys.platform == 'win32':
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('ChessShorts.VisualAnalysisStudio')
        except (AttributeError, OSError):
            pass
    from .ui import Studio
    app = QApplication(sys.argv)
    app.setApplicationName('Chess Shorts Visual Analysis Studio')
    app.setWindowIcon(QIcon(str(Path(__file__).parent / 'assets' / 'app.ico')))
    window = Studio()
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
