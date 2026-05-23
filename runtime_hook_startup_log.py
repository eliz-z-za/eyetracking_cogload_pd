# Runtime hook: выполняется сразу после старта exe (до experiment.py).
# Пишет в vibe_startup.log рядом с exe — чтобы понять, доходит ли запуск до Python.
import sys
import os
import time

if getattr(sys, "frozen", False):
    try:
        base = os.path.dirname(sys.executable)
        log_path = os.path.join(base, "vibe_startup.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} bootloader started (Python running)\n")
    except Exception:
        pass
