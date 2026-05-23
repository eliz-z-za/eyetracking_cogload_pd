"""
Генерация оверлеев взгляда для записанного сеанса эксперимента.
Запускается вручную после завершения записи, чтобы не замедлять смену участников.

Реализация рендеринга — в overlay.py (extract_trial_gaze, make_overlay).
Этот скрипт находит нужные файлы и вызывает эти функции.

Использование:
    python generate_overlays.py <participant_id> [data_dir]

Генерирует до 3 оверлеев:
    - screen_<pid>_pd_b1_*_overlay.mp4  (PD-блок 1)
    - screen_<pid>_pd_b2_*_overlay.mp4  (PD-блок 2)
    - screen_<pid>_paintings_*_overlay.mp4  (фаза картин)
"""

import sys
import os
import glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def find_gaze_file(pid, data_dir):
    """Основной gaze-файл участника (не trial-экстракты)."""
    pattern = os.path.join(data_dir, f"gaze_{pid}_????????_??????.tsv")
    candidates = [
        f for f in glob.glob(pattern)
        if '_trial' not in os.path.basename(f)
        and '_pd_b' not in os.path.basename(f)
        and '_paintings' not in os.path.basename(f)
    ]
    if not candidates:
        return None
    if len(candidates) > 1:
        print(f"[WARN] Несколько gaze-файлов: {candidates}, берём последний")
    return sorted(candidates)[-1]


def find_events_file(pid, data_dir):
    pattern = os.path.join(data_dir, f"events_{pid}_*.csv")
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def find_pd_block_info(events_file):
    """Возвращает {block_num: (condition, dataset)} из events CSV."""
    if not events_file or not os.path.isfile(events_file):
        return {}
    import pandas as pd
    df = pd.read_csv(events_file)
    block_info = {}
    for ev in df.get('event', []):
        ev = str(ev)
        if not ev.startswith('PD_BLOCK_START_B'):
            continue
        # PD_BLOCK_START_B1_FLAT_ALPHA
        parts = ev.split('_')
        try:
            block_num = int(parts[3][1:])   # B1 -> 1
            condition = parts[4].lower()     # FLAT -> flat
            dataset   = parts[5].lower()     # ALPHA -> alpha
            block_info[block_num] = (condition, dataset)
        except (IndexError, ValueError):
            pass
    return block_info


def generate_overlays(pid, data_dir):
    from overlay import extract_trial_gaze, make_overlay

    gaze_file = find_gaze_file(pid, data_dir)
    if not gaze_file:
        print(f"[ERROR] Gaze-файл не найден для участника '{pid}' в '{data_dir}'")
        sys.exit(1)
    print(f"[OK] Gaze-файл: {gaze_file}")

    events_file = find_events_file(pid, data_dir)
    block_info = find_pd_block_info(events_file)
    if not block_info:
        print("[WARN] Информация о PD-блоках не найдена в events CSV")

    # --- PD-блоки (логика из post_process_pd в experiment.py) ---
    for block_num, (condition, dataset) in sorted(block_info.items()):
        pattern = os.path.join(data_dir, f"screen_{pid}_pd_b{block_num}_*.mp4")
        videos = [f for f in glob.glob(pattern) if '_overlay' not in f]
        if not videos:
            print(f"[WARN] Видео PD-блока {block_num} не найдено ({pattern})")
            continue
        video_path = sorted(videos)[-1]

        marker_start = f"PD_BLOCK_START_B{block_num}_{condition.upper()}_{dataset.upper()}"
        marker_end   = f"PD_BLOCK_END_B{block_num}"
        trial_gaze   = os.path.join(data_dir, f"gaze_{pid}_pd_b{block_num}_trial.tsv")

        print(f"\n--- PD Block {block_num} ({condition}/{dataset}) ---")
        extracted = extract_trial_gaze(
            gaze_file, marker_start, marker_end, trial_gaze, fallback_to_eof=True
        )
        if extracted:
            make_overlay(video_path, trial_gaze)
        else:
            print(f"[WARN] Не удалось извлечь gaze для блока {block_num}")

    # --- Фаза картин (логика из post_process_paintings в experiment.py) ---
    pattern = os.path.join(data_dir, f"screen_{pid}_paintings_*.mp4")
    paintings_videos = [f for f in glob.glob(pattern) if '_overlay' not in f]
    if paintings_videos:
        video_path = sorted(paintings_videos)[-1]
        trial_gaze = os.path.join(data_dir, f"gaze_{pid}_paintings_trial.tsv")
        print("\n--- Фаза картин ---")
        extracted = extract_trial_gaze(
            gaze_file,
            "PAINTINGS_PHASE_START",
            "PAINTINGS_PHASE_END",
            trial_gaze,
        )
        if extracted:
            make_overlay(video_path, trial_gaze)
        else:
            print("[WARN] Не удалось извлечь gaze для картин")
    else:
        print("\n[INFO] Видео картин не найдено — пропуск")

    print("\n[DONE] Генерация оверлеев завершена")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python generate_overlays.py <participant_id> [data_dir]")
        sys.exit(1)
    pid = sys.argv[1]
    data_dir = sys.argv[2] if len(sys.argv) > 2 else "data"
    generate_overlays(pid, data_dir)
