"""Screen recorder based on mss (capture) + ffmpeg (encoding).

Produces videos with correct, constant FPS — each frame is timed to
match real wall-clock time. Uses ffmpeg for encoding (imageio-ffmpeg
or system ffmpeg).
"""

import os
import subprocess
import threading
import time

import mss
import numpy as np

# Reasonable FPS range for validation
FPS_MIN, FPS_MAX = 1, 120


def _find_ffmpeg():
    """Return path to the ffmpeg binary."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    import shutil
    path = shutil.which("ffmpeg")
    if path:
        return path
    raise FileNotFoundError(
        "ffmpeg not found. Install imageio-ffmpeg (`pip install imageio-ffmpeg`) "
        "or add ffmpeg to PATH."
    )


def _verify_ffmpeg_executable(ffmpeg_path):
    """Check that ffmpeg runs and returns 0. Raises RuntimeError on failure."""
    try:
        result = subprocess.run(
            [ffmpeg_path, "-version"],
            capture_output=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg -version timed out")
    except OSError as e:
        raise RuntimeError(f"ffmpeg not executable: {e}")
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg -version returned {result.returncode}. stderr: {result.stderr.decode(errors='replace')}"
        )


def _validate_monitor(monitor):
    """Validate mss-style monitor dict. Raises ValueError on invalid."""
    required = ("left", "top", "width", "height")
    for k in required:
        if k not in monitor:
            raise ValueError(f"monitor missing key: {k}")
    w, h = monitor["width"], monitor["height"]
    if not (w > 0 and h > 0):
        raise ValueError(f"monitor width and height must be positive, got {w}x{h}")


def _validate_fps(fps):
    """Validate FPS in reasonable range. Raises ValueError on invalid."""
    if not (FPS_MIN <= fps <= FPS_MAX):
        raise ValueError(f"fps must be in [{FPS_MIN}, {FPS_MAX}], got {fps}")


def verify_recording(output_path):
    """Check that a recording file exists, has size, and is readable as video.

    Returns (success: bool, message: str).
    """
    if not output_path or not os.path.isfile(output_path):
        return False, "file does not exist"
    if os.path.getsize(output_path) <= 0:
        return False, "file is empty"
    try:
        import cv2
        cap = cv2.VideoCapture(output_path)
        if not cap.isOpened():
            cap.release()
            return False, "video could not be opened (OpenCV)"
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        if frame_count <= 0:
            return False, "video has no frames"
        if not (0.1 <= fps <= 120):
            return False, f"video FPS out of range: {fps}"
    except ImportError:
        pass  # OpenCV optional for this check
    return True, "OK"


class ScreenRecorder:
    """Screen recorder using mss (capture) and ffmpeg (encoding).

    Uses mss to grab frames and pipes raw pixels to ffmpeg for encoding.
    The target FPS is enforced via sleep between captures, so the output
    video's metadata FPS matches real elapsed time.
    """

    def __init__(self):
        self._proc = None
        self._thread = None
        self._stop_event = threading.Event()
        self._ffmpeg = _find_ffmpeg()
        self._output_path = None
        self._last_success = None

    def start_recording(self, output_path, fps, monitor):
        """Begin recording *monitor* to *output_path* at *fps*.

        *monitor* is an mss-style dict with 'left', 'top', 'width', 'height'.
        Compatible with ``mss.mss().monitors[N]``.

        Raises ValueError for invalid parameters, RuntimeError if ffmpeg fails.
        """
        if self._proc is not None:
            raise RuntimeError("Recording already in progress")

        _verify_ffmpeg_executable(self._ffmpeg)
        _validate_monitor(monitor)
        _validate_fps(fps)

        output_path = os.path.abspath(output_path)
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        if not os.path.isdir(out_dir):
            raise ValueError(f"output directory does not exist or is not writable: {out_dir}")

        self._stop_event.clear()
        self._output_path = output_path
        self._last_success = None
        w, h = monitor["width"], monitor["height"]
        self._fps = fps
        self._frame_interval = 1.0 / fps
        self._expected_bytes = w * h * 4  # BGRA

        cmd = [
            self._ffmpeg,
            "-y",
            "-f", "rawvideo",
            "-pix_fmt", "bgra",
            "-s", f"{w}x{h}",
            "-r", str(fps),
            "-i", "pipe:0",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            output_path,
        ]

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if self._proc.poll() is not None:
            self._proc = None
            raise RuntimeError("ffmpeg process exited immediately after start")

        self._thread = threading.Thread(
            target=self._capture_loop,
            args=(monitor,),
            daemon=True,
        )
        self._thread.start()

    def stop_recording(self):
        """Stop recording and wait for ffmpeg to finish writing.

        Returns True if ffmpeg exited with code 0, False otherwise.
        """
        if self._proc is None:
            return False
        self._stop_event.set()
        self._thread.join(timeout=10)
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
            self._last_success = False
            print("[VIDEO] ffmpeg did not exit within timeout; process killed")
        else:
            self._last_success = self._proc.returncode == 0
            if self._proc.returncode != 0:
                print(f"[VIDEO] ffmpeg exited with code {self._proc.returncode}")
        self._proc = None
        self._thread = None
        return self._last_success is True

    def was_successful(self):
        """True if the last stop_recording() reported ffmpeg exit code 0."""
        return self._last_success is True

    def get_last_output_path(self):
        """Path passed to start_recording() for the last run, or None."""
        return self._output_path

    def _capture_loop(self, monitor):
        w, h = monitor["width"], monitor["height"]
        with mss.mss() as sct:
            next_time = time.perf_counter()
            while not self._stop_event.is_set():
                frame = sct.grab(monitor)
                raw = np.asarray(frame)
                if raw.nbytes != self._expected_bytes:
                    print(f"[VIDEO] frame size mismatch: expected {self._expected_bytes}, got {raw.nbytes}")
                try:
                    self._proc.stdin.write(raw.tobytes())
                except (BrokenPipeError, OSError) as e:
                    print(f"[VIDEO] capture loop pipe error: {e}")
                    break

                next_time += self._frame_interval
                sleep_dur = next_time - time.perf_counter()
                if sleep_dur > 0:
                    time.sleep(sleep_dur)
                else:
                    next_time = time.perf_counter()
