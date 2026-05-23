# Создаёт видео с оверлеем взгляда и курсора поверх записи экрана

import cv2
import pandas as pd
import numpy as np
import os
import glob

from config import DATA_DIR, OVERLAY_DRAW_INVALID_GAZE


def extract_trial_gaze(gaze_path, marker_start, marker_end, output_path,
                       fallback_to_eof=False):
    """Extracts gaze rows between two USER markers and saves to a per-trial TSV.

    Returns output_path on success, None if start marker not found.
    If marker_end is missing and fallback_to_eof=True, extracts from
    marker_start to end of file (useful when stop_recording() dropped the
    end marker due to a race condition).
    """
    df = pd.read_csv(gaze_path, sep='\t')

    start_rows = df.index[df['USER'] == marker_start]
    end_rows   = df.index[df['USER'] == marker_end]

    if len(start_rows) == 0:
        print(f"[OVERLAY] Начальный маркер не найден: {marker_start}")
        return None

    if len(end_rows) == 0:
        if fallback_to_eof:
            print(f"[OVERLAY] Конечный маркер не найден ({marker_end}), "
                  f"использую конец файла как fallback")
            trial_df = df.loc[start_rows[0]:].copy()
        else:
            print(f"[OVERLAY] Маркеры не найдены: {marker_start} / {marker_end}")
            return None
    else:
        trial_df = df.loc[start_rows[0]:end_rows[0]].copy()

    if trial_df.empty:
        print(f"[OVERLAY] Нет данных между маркерами {marker_start} и {marker_end}")
        return None

    trial_df.to_csv(output_path, sep='\t', index=False)
    print(f"[OVERLAY] Trial gaze ({len(trial_df)} строк) -> {output_path}")
    return output_path


def make_overlay(video_path, gaze_path, output_path=None, sync_info=None, draw_invalid_gaze=None):
    """Render gaze/cursor overlay onto a screen recording.

    sync_info (optional dict) with wall-clock timestamps from experiment.py:
        video_start_wall  – time.time() right after screen recording started
        trial_start_wall  – time.time() right after TRIAL_START marker logged
        video_stop_wall   – time.time() right after screen recording stopped

    When sync_info is provided the real video duration and the offset between
    video start and gaze start are computed from wall-clock.

    When sync_info is None two strategies are tried:
      1. If the video was recorded with the ffmpeg-based ScreenRecorder
         (correct constant FPS), metadata FPS is trusted and used for timing.
      2. For legacy recordings with unreliable FPS metadata we fall back to
         linearly mapping frames onto the gaze time range.
    We auto-detect by comparing total_frames/fps with gaze_duration: if they
    are close (within 20 %) the FPS is trusted; otherwise linear fallback.
    """
    print(f"\n[OVERLAY] Видео: {video_path}")
    print(f"[OVERLAY] Gaze:  {gaze_path}")

    if output_path is None:
        base, ext = os.path.splitext(video_path)
        output_path = base + "_overlay" + ext

    df = pd.read_csv(gaze_path, sep="\t")
    df['TIME'] = pd.to_numeric(df['TIME'], errors='coerce')
    df = df.dropna(subset=['TIME'])

    df['t_sec'] = df['TIME'] - df['TIME'].iloc[0]

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print("[OVERLAY] Не удалось открыть видео")
        return

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    gaze_duration = df['t_sec'].iloc[-1]
    meta_video_dur = total_frames / fps if fps > 0 else 0.0

    # --- Compute time-mapping parameters ---
    if sync_info is not None:
        real_video_dur = sync_info['video_stop_wall'] - sync_info['video_start_wall']
        gaze_offset = sync_info['trial_start_wall'] - sync_info['video_start_wall']
        mode = "sync_info (wall-clock)"
    elif gaze_duration > 0 and abs(meta_video_dur - gaze_duration) / gaze_duration < 0.20:
        # ffmpeg-recorded video: metadata FPS is reliable
        real_video_dur = meta_video_dur
        gaze_offset = 0.0
        mode = "trusted metadata FPS (ffmpeg recording)"
    else:
        # Legacy recording: metadata FPS unreliable — linear stretch
        real_video_dur = gaze_duration
        gaze_offset = 0.0
        mode = "linear fallback (legacy recording)"

    print(f"[OVERLAY] Mode: {mode}")
    print(f"[OVERLAY] FPS(meta)={fps}, size={width}x{height}, frames={total_frames}")
    print(f"[OVERLAY] Gaze duration: {gaze_duration:.2f}s, "
          f"video meta duration: {meta_video_dur:.2f}s, "
          f"real video duration: {real_video_dur:.2f}s, "
          f"gaze_offset: {gaze_offset:.2f}s")
    if draw_invalid_gaze is None:
        draw_invalid_gaze = OVERLAY_DRAW_INVALID_GAZE

    print(f"[OVERLAY] Сохраняю в: {output_path}")
    print(f"[OVERLAY] Невалидные точки: {'ON' if draw_invalid_gaze else 'OFF'}")

    has_cursor = 'CX' in df.columns and 'CY' in df.columns
    frame_idx = 0
    skipped = 0
    drawn_valid = 0
    drawn_invalid = 0
    invalid_available = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        t_real = (frame_idx / total_frames * real_video_dur
                  if total_frames > 0 else 0.0)
        gaze_t = t_real - gaze_offset

        if gaze_t < 0 or gaze_t > gaze_duration:
            out.write(frame)
            frame_idx += 1
            skipped += 1
            continue

        idx = (df['t_sec'] - gaze_t).abs().idxmin()
        row = df.loc[idx]

        fpog_valid = row.get('FPOGV', 0) == 1
        gaze_x = row.get('FPOGX', np.nan)
        gaze_y = row.get('FPOGY', np.nan)
        has_gaze_coords = (
            pd.notna(gaze_x) and pd.notna(gaze_y)
            and 0 <= gaze_x <= 1 and 0 <= gaze_y <= 1
        )
        if has_gaze_coords:
            x_gaze = int(gaze_x * width)
            y_gaze = int(gaze_y * height)
            if fpog_valid:
                # Valid gaze sample: red ring.
                cv2.circle(frame, (x_gaze, y_gaze), 15, (0, 0, 255), 2)
                drawn_valid += 1
            else:
                invalid_available += 1
                if draw_invalid_gaze:
                    # Invalid sample: draw separately to visualize uncertainty.
                    cv2.circle(frame, (x_gaze, y_gaze), 10, (0, 255, 255), 2)
                    drawn_invalid += 1

        if has_cursor:
            if 0 <= row.get('CX', -1) <= 1 and 0 <= row.get('CY', -1) <= 1:
                x_cur = int(row['CX'] * width)
                y_cur = int(row['CY'] * height)
                cv2.circle(frame, (x_cur, y_cur), 8, (0, 255, 0), -1)

        out.write(frame)
        frame_idx += 1
        if frame_idx % 300 == 0:
            print(f"[OVERLAY] {frame_idx}/{total_frames} frames "
                  f"(real={t_real:.1f}s, gaze_t={gaze_t:.1f}s)")

    cap.release()
    out.release()
    print(
        f"[OVERLAY] Готово. {frame_idx} кадров обработано, "
        f"{skipped} без оверлея (вне диапазона gaze), "
        f"валидных точек: {drawn_valid}, "
        f"невалидных найдено: {invalid_available}, "
        f"из них отрисовано: {drawn_invalid}."
    )


def batch_overlay(data_dir=DATA_DIR):
    # Ищем пары файлов: screen_* и gaze_*
    videos = sorted(glob.glob(os.path.join(data_dir, "screen_*.mp4")))
    gazes = sorted(glob.glob(os.path.join(data_dir, "gaze_*.tsv")))

    if not videos or not gazes:
        print("В папке data/ нет видео или TSV.")
        return

    # Простейшее сопоставление: по участнику и (опционально) провайдеру
    for video in videos:
        name = os.path.basename(video)
        # screen_P01_aws_20260217_180000.mp4 -> P01_aws
        core = name.replace("screen_", "").split("_")
        participant = core[0]
        provider = core[1] if len(core) > 2 else None

        # ищем первый gaze-файл с этим participant
        cand = [g for g in gazes if f"gaze_{participant}_" in os.path.basename(g)]
        if not cand:
            print(f"[OVERLAY] Нет gaze-файла для {name}")
            continue

        gaze_path = cand[0]
        make_overlay(video, gaze_path)

if __name__ == "__main__":
    batch_overlay()
