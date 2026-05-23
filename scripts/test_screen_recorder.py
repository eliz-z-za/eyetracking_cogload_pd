"""Test screen recording (ffmpeg + mss) without running the full experiment.

Run: python test_screen_recorder.py

Records 2–3 seconds of the configured monitor, then verifies the output file
exists, has content, and is readable as video (OpenCV). Use this to confirm
recording works before conducting an experiment.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATA_DIR, VIDEO_FPS, VIDEO_MONITOR


def main():
    print("=== Проверка записи экрана (ffmpeg + mss) ===\n")

    # 1. ffmpeg
    try:
        from screen_recorder import _find_ffmpeg, _verify_ffmpeg_executable
        ffmpeg = _find_ffmpeg()
        print(f"[OK] ffmpeg: {ffmpeg}")
        _verify_ffmpeg_executable(ffmpeg)
        print("[OK] ffmpeg -version успешен")
    except Exception as e:
        print(f"[FAIL] ffmpeg: {e}")
        return 1

    # 2. monitor
    try:
        import mss
        with mss.mss() as sct:
            mon = sct.monitors[VIDEO_MONITOR]
        w, h = mon["width"], mon["height"]
        print(f"[OK] Монитор {VIDEO_MONITOR}: {w}x{h}")
    except Exception as e:
        print(f"[FAIL] Монитор: {e}")
        return 1

    # 3. output path
    os.makedirs(DATA_DIR, exist_ok=True)
    output_path = os.path.join(DATA_DIR, "test_recording.mp4")
    duration_sec = 2.5

    # 4. record
    try:
        from screen_recorder import ScreenRecorder, verify_recording
        rec = ScreenRecorder()
        rec.start_recording(output_path, VIDEO_FPS, mon)
        print(f"[OK] Запись запущена на {duration_sec} с...")
        time.sleep(duration_sec)
        rec.stop_recording()
        if not rec.was_successful():
            print("[FAIL] ffmpeg завершился с ошибкой")
            return 1
        print("[OK] Запись остановлена")
    except Exception as e:
        print(f"[FAIL] Запись: {e}")
        return 1

    time.sleep(0.5)

    # 5. verify file
    ok, msg = verify_recording(output_path)
    if not ok:
        print(f"[FAIL] Проверка файла: {msg}")
        return 1

    # 6. optional: OpenCV details
    try:
        import cv2
        cap = cv2.VideoCapture(output_path)
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        print(f"[OK] Файл: {output_path}")
        print(f"     Размер: {size_mb:.2f} MB, кадров: {frames}, FPS: {fps:.1f}")
    except ImportError:
        print(f"[OK] Файл: {output_path}")

    print("\n=== Запись экрана работает. Можно запускать эксперимент. ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
