"""Cursor-only recorder: writes mouse position (TIME, CX, CY, USER) to TSV.

Used in CURSOR_ONLY_MODE to verify overlay time sync without an eye tracker.
"""

import os
import sys
import threading
import time

from config import VIDEO_MONITOR

# Windows: get cursor position via ctypes
def _get_cursor_pos():
    if sys.platform != "win32":
        return None, None
    try:
        import ctypes
        from ctypes import wintypes
        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]
        pt = POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return pt.x, pt.y
    except Exception:
        pass
    return None, None


def _get_monitor_bounds():
    """Return (left, top, width, height) for VIDEO_MONITOR."""
    import mss
    with mss.mss() as sct:
        mon = sct.monitors[VIDEO_MONITOR]
        return mon["left"], mon["top"], mon["width"], mon["height"]


class CursorRecorder:
    """Records mouse position to a TSV with columns TIME, CX, CY, USER.

    Compatible with experiment.py usage: start_recording(), log(msg),
    stop_recording(), close(). No calibration methods.
    """

    CURSOR_SAMPLE_HZ = 60.0

    def __init__(self, logfile_path):
        self._logfile_path = os.path.abspath(logfile_path)
        self._file = None
        self._thread = None
        self._stop_event = threading.Event()
        self._user_msg = "0"
        self._user_lock = threading.Lock()
        self._left = self._top = self._width = self._height = 0

    def start_recording(self):
        if self._file is not None:
            raise RuntimeError("Recording already in progress")
        if sys.platform != "win32":
            raise RuntimeError("Cursor-only recording is supported only on Windows (GetCursorPos).")
        self._stop_event.clear()
        self._left, self._top, self._width, self._height = _get_monitor_bounds()
        if self._width <= 0 or self._height <= 0:
            raise RuntimeError("Invalid monitor bounds for cursor recording")
        out_dir = os.path.dirname(self._logfile_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        write_header = not os.path.isfile(self._logfile_path) or os.path.getsize(self._logfile_path) == 0
        self._file = open(self._logfile_path, "a", encoding="utf-8")
        if write_header:
            self._file.write("TIME\tCX\tCY\tUSER\n")
        self._file.flush()
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()

    def log(self, msg):
        with self._user_lock:
            self._user_msg = str(msg)

    def stop_recording(self):
        if self._file is None:
            return
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        try:
            if self._file is not None:
                self._file.close()
        except OSError:
            pass
        self._file = None
        self._thread = None

    def close(self):
        self.stop_recording()

    def calibrate(self):
        """No-op for compatibility with experiment flow (recalibration between trials)."""
        pass

    def calibrate_result_summary(self):
        """Stub for type compatibility; not used in cursor-only mode."""
        return None, 0

    def _sample_loop(self):
        interval = 1.0 / self.CURSOR_SAMPLE_HZ
        while not self._stop_event.is_set():
            t0 = time.perf_counter()
            x, y = _get_cursor_pos()
            with self._user_lock:
                user = self._user_msg
            if x is not None and y is not None and self._file is not None:
                cx = (x - self._left) / self._width
                cy = (y - self._top) / self._height
                cx = max(0.0, min(1.0, cx))
                cy = max(0.0, min(1.0, cy))
                try:
                    self._file.write(f"{time.time():.6f}\t{cx:.6f}\t{cy:.6f}\t{user}\n")
                    self._file.flush()
                except (OSError, ValueError):
                    break
            elapsed = time.perf_counter() - t0
            sleep_dur = interval - elapsed
            if sleep_dur > 0:
                self._stop_event.wait(timeout=sleep_dur)
            else:
                self._stop_event.wait(timeout=0.001)
