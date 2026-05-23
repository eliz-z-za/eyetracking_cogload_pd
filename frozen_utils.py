# ===== frozen_utils.py =====
# Пути для режима PyInstaller (frozen exe) и обычного запуска

import sys
import os


def get_base_dir():
    """Корневая папка приложения (для записи data/, конфигов)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_resource_dir():
    """Папка с ресурсами (templates, static, paintings) — при frozen это _MEIPASS."""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def is_frozen():
    return getattr(sys, 'frozen', False)
