# ===== analysis_paintings.py =====
# Анализ данных айтрекинга для фазы картин:
# тепловые карты, траектории, метрики фиксаций и саккад, XLS-экспорт.

import os
import glob
from typing import cast
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from scipy.ndimage import gaussian_filter
from PIL import Image

from analysis import load_gaze_data, extract_trial_data


def extract_painting_trial(df, painting_name):
    """Извлекает строки между маркерами PAINTING_DISPLAYED_{name} и PAINTING_VIEWED_{name}."""
    safe_upper = painting_name.replace(' ', '_').upper()
    marker_start = f"PAINTING_DISPLAYED_{safe_upper}"
    marker_end = f"PAINTING_VIEWED_{safe_upper}"

    user_upper = df['USER'].astype(str).str.upper()
    start_rows = df.index[user_upper == marker_start]
    end_rows = df.index[user_upper == marker_end]

    if len(start_rows) == 0 or len(end_rows) == 0:
        return pd.DataFrame()

    return df.loc[start_rows[0]:end_rows[0]].copy()


def screen_to_image_coords(gaze_x, gaze_y, bbox, img_w, img_h):
    """Преобразует нормализованные координаты экрана GP3 в пиксели картины.

    Корректно обрабатывает object-fit: contain (letterboxing): bbox записывает
    CSS-бокс элемента (например, весь экран 1920×1080), а реальная область
    отрисовки картины меньше. Натуральные размеры img_w/img_h используются для
    вычисления настоящего content-rect через aspect-ratio fitting.

    Для object-fit: fill (bbox['fit_mode'] == 'fill') элемент = контент,
    коррекция не применяется.

    gaze_x, gaze_y: 0-1 нормализованные координаты (от GP3)
    bbox: dict с img_x, img_y, img_w, img_h (CSS-элемент), screen_w, screen_h,
          опционально fit_mode ('contain' по умолчанию | 'fill')
    img_w, img_h: натуральные размеры изображения в пикселях (из PIL)

    Возвращает: (px, py) в пикселях изображения, или (NaN, NaN) если вне картины.
    """
    if bbox['img_w'] <= 0 or bbox['img_h'] <= 0 or img_w <= 0 or img_h <= 0:
        return np.nan, np.nan

    sx = gaze_x * bbox['screen_w']
    sy = gaze_y * bbox['screen_h']

    fit_mode = bbox.get('fit_mode', 'contain')
    if fit_mode == 'fill':
        # object-fit: fill — изображение растянуто на весь элемент, коррекция не нужна
        rel_x = (sx - bbox['img_x']) / bbox['img_w']
        rel_y = (sy - bbox['img_y']) / bbox['img_h']
    else:
        # object-fit: contain — вычисляем реальный content-rect через aspect ratio
        elem_w = bbox['img_w']
        elem_h = bbox['img_h']
        elem_x = bbox['img_x']
        elem_y = bbox['img_y']

        natural_aspect = img_w / img_h
        elem_aspect = elem_w / elem_h

        if natural_aspect > elem_aspect:
            # Ограничено по ширине: картина заполняет ширину, полосы сверху/снизу
            content_w = elem_w
            content_h = elem_w / natural_aspect
            content_x = elem_x
            content_y = elem_y + (elem_h - content_h) / 2.0
        else:
            # Ограничено по высоте: картина заполняет высоту, полосы слева/справа
            content_h = elem_h
            content_w = elem_h * natural_aspect
            content_x = elem_x + (elem_w - content_w) / 2.0
            content_y = elem_y

        rel_x = (sx - content_x) / content_w
        rel_y = (sy - content_y) / content_h

    if rel_x < 0 or rel_x > 1 or rel_y < 0 or rel_y > 1:
        return np.nan, np.nan

    return rel_x * img_w, rel_y * img_h


def _fixations_from_trial(trial_df, bbox=None, img_w=None, img_h=None):
    """Возвращает DataFrame уникальных фиксаций (id, x, y, duration) из trial_df.

    Если bbox передан, координаты x/y преобразуются в пиксели изображения через
    screen_to_image_coords и помечаются как 'in_image'. Фиксации вне картины исключаются.
    Без bbox координаты остаются нормализованными (0-1).
    """
    if 'FPOGV' not in trial_df.columns:
        return pd.DataFrame()
    valid = trial_df[trial_df['FPOGV'] == 1]
    if len(valid) == 0:
        return pd.DataFrame()

    fix = valid.groupby('FPOGID').agg(
        x=('FPOGX', 'mean'),
        y=('FPOGY', 'mean'),
        duration=('FPOGD', 'max'),
    ).reset_index()

    if bbox and img_w and img_h:
        px_coords = fix.apply(
            lambda row: screen_to_image_coords(row['x'], row['y'], bbox, img_w, img_h),
            axis=1, result_type='expand',
        )
        px_coords.columns = ['px', 'py']
        fix = fix.join(px_coords)
        fix = fix.dropna(subset=['px', 'py']).reset_index(drop=True)

    return fix


def _saccades_from_fixations(fix_df):
    """Рассчитывает саккады (амплитуду, длительность proxy) из последовательных фиксаций."""
    if len(fix_df) < 2:
        return pd.DataFrame()

    fix_sorted = fix_df.sort_values('FPOGID').reset_index(drop=True)
    dx = fix_sorted['x'].diff()
    dy = fix_sorted['y'].diff()
    amplitude = np.sqrt(dx ** 2 + dy ** 2).iloc[1:]

    saccades = pd.DataFrame({
        'from_fix': fix_sorted['FPOGID'].iloc[:-1].values,
        'to_fix': fix_sorted['FPOGID'].iloc[1:].values,
        'amplitude': amplitude.values,
        'from_x': fix_sorted['x'].iloc[:-1].values,
        'from_y': fix_sorted['y'].iloc[:-1].values,
        'to_x': fix_sorted['x'].iloc[1:].values,
        'to_y': fix_sorted['y'].iloc[1:].values,
    })
    return saccades


def extract_paintings_baseline_pupil(df):
    """Извлекает среднее значение зрачка из baseline-периода фазы картин.

    Возвращает dict: baseline_mean_left, baseline_mean_right, baseline_mean_avg
    или пустой dict, если baseline не найден.
    """
    baseline_raw = extract_trial_data(df, "PAINTINGS_BASELINE_START", "PAINTINGS_BASELINE_END")
    if len(baseline_raw) == 0:
        return {}
    if isinstance(baseline_raw, pd.DataFrame):
        baseline_df = baseline_raw
    else:
        baseline_df = pd.DataFrame(baseline_raw)

    if 'TIME' in baseline_df.columns and len(baseline_df) > 1:
        t0 = baseline_df['TIME'].iloc[0]
        baseline_df = baseline_df[baseline_df['TIME'] >= t0 + 0.5]
    if len(baseline_df) == 0:
        return {}

    for col in ('LPD', 'RPD'):
        if col in baseline_df.columns:
            speed = baseline_df[col].diff().abs()
            baseline_df = baseline_df[speed.isna() | (speed <= 0.5)]

    baseline_df = cast(pd.DataFrame, baseline_df)
    lpd = baseline_df[baseline_df['LPV'] == 1]['LPD'] if 'LPV' in baseline_df.columns else pd.Series(dtype=float)
    rpd = baseline_df[baseline_df['RPV'] == 1]['RPD'] if 'RPV' in baseline_df.columns else pd.Series(dtype=float)

    both_valid = baseline_df[(baseline_df.get('LPV', 0) == 1) & (baseline_df.get('RPV', 0) == 1)] if 'LPV' in baseline_df.columns and 'RPV' in baseline_df.columns else pd.DataFrame()
    avg = ((both_valid['LPD'] + both_valid['RPD']) / 2).mean() if len(both_valid) > 0 else np.nan

    return {
        'baseline_mean_left': lpd.mean() if len(lpd) > 0 else np.nan,
        'baseline_mean_right': rpd.mean() if len(rpd) > 0 else np.nan,
        'baseline_mean_avg': avg,
    }


def compute_painting_metrics(trial_df, baseline=None):
    """Рассчитывает сводные метрики для одной картины."""
    fix = _fixations_from_trial(trial_df)
    sacc = _saccades_from_fixations(fix)

    duration = 0.0
    if 'TIME' in trial_df.columns and len(trial_df) > 1:
        duration = trial_df['TIME'].iloc[-1] - trial_df['TIME'].iloc[0]

    # Pupil metrics
    lpd_valid = trial_df[trial_df['LPV'] == 1]['LPD'] if 'LPV' in trial_df.columns else pd.Series(dtype=float)
    rpd_valid = trial_df[trial_df['RPV'] == 1]['RPD'] if 'RPV' in trial_df.columns else pd.Series(dtype=float)
    both_valid = trial_df[(trial_df['LPV'] == 1) & (trial_df['RPV'] == 1)] if 'LPV' in trial_df.columns and 'RPV' in trial_df.columns else pd.DataFrame()
    pupil_avg = (both_valid['LPD'] + both_valid['RPD']) / 2 if len(both_valid) > 0 else pd.Series(dtype=float)

    mean_left = lpd_valid.mean() if len(lpd_valid) > 0 else np.nan
    mean_right = rpd_valid.mean() if len(rpd_valid) > 0 else np.nan
    mean_avg = pupil_avg.mean() if len(pupil_avg) > 0 else np.nan

    metrics = {
        'viewing_duration_sec': duration,
        'fixation_count': len(fix),
        'fixation_duration_mean': fix['duration'].mean() if len(fix) else np.nan,
        'fixation_duration_median': fix['duration'].median() if len(fix) else np.nan,
        'fixation_duration_std': fix['duration'].std() if len(fix) else np.nan,
        'fixation_duration_total': fix['duration'].sum() if len(fix) else 0.0,
        'saccade_count': len(sacc),
        'saccade_amplitude_mean': sacc['amplitude'].mean() if len(sacc) else np.nan,
        'saccade_amplitude_std': sacc['amplitude'].std() if len(sacc) else np.nan,
        'scanpath_length': sacc['amplitude'].sum() if len(sacc) else 0.0,
        'pupil_mean_left': mean_left,
        'pupil_mean_right': mean_right,
        'pupil_mean_avg': mean_avg,
    }

    if baseline:
        ba = baseline.get('baseline_mean_avg', np.nan)
        bl = baseline.get('baseline_mean_left', np.nan)
        br = baseline.get('baseline_mean_right', np.nan)
        metrics['pupil_baseline_mean'] = ba
        metrics['pupil_change_left'] = (mean_left - bl) / bl * 100 if bl and not np.isnan(bl) and bl != 0 else np.nan
        metrics['pupil_change_right'] = (mean_right - br) / br * 100 if br and not np.isnan(br) and br != 0 else np.nan
        metrics['pupil_change_avg'] = (mean_avg - ba) / ba * 100 if ba and not np.isnan(ba) and ba != 0 else np.nan

    return metrics


def generate_heatmap(trial_df, painting_path, output_path, sigma=30, bbox=None):
    """Генерирует тепловую карту фиксаций поверх картины и сохраняет в файл."""
    img = Image.open(painting_path)
    img_w, img_h = img.size

    fix = _fixations_from_trial(trial_df, bbox=bbox, img_w=img_w, img_h=img_h)
    if len(fix) == 0:
        return False

    fix_px = fix.copy()
    if 'px' in fix_px.columns:
        fix_px['px'] = fix_px['px'].clip(0, img_w - 1).astype(int)
        fix_px['py'] = fix_px['py'].clip(0, img_h - 1).astype(int)
    else:
        fix_px['px'] = (fix_px['x'] * img_w).clip(0, img_w - 1).astype(int)
        fix_px['py'] = (fix_px['y'] * img_h).clip(0, img_h - 1).astype(int)

    heatmap = np.zeros((img_h, img_w), dtype=np.float64)
    px = fix_px['px'].to_numpy(dtype=np.intp, copy=False)
    py = fix_px['py'].to_numpy(dtype=np.intp, copy=False)
    duration = np.nan_to_num(np.asarray(fix_px['duration'], dtype=np.float64), nan=0.0)
    for y, x, d in zip(py, px, duration):
        heatmap[y, x] += d

    heatmap = gaussian_filter(heatmap, sigma=sigma)
    if heatmap.max() > 0:
        heatmap /= heatmap.max()

    fig, ax = plt.subplots(figsize=(img_w / 100, img_h / 100), dpi=100)
    ax.imshow(img)
    ax.imshow(heatmap, cmap='jet', alpha=0.4, extent=(0.0, float(img_w), float(img_h), 0.0))
    ax.set_axis_off()
    plt.tight_layout(pad=0)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.savefig(output_path, bbox_inches='tight', pad_inches=0, dpi=150)
    plt.close(fig)
    return True


def generate_trajectory(trial_df, painting_path, output_path, bbox=None):
    """Генерирует траекторию движения глаз поверх картины."""
    img = Image.open(painting_path)
    img_w, img_h = img.size

    fix = _fixations_from_trial(trial_df, bbox=bbox, img_w=img_w, img_h=img_h)
    if len(fix) == 0:
        return False

    fix_sorted = fix.sort_values('FPOGID').reset_index(drop=True)
    if 'px' in fix_sorted.columns:
        xs = fix_sorted['px'].values
        ys = fix_sorted['py'].values
    else:
        xs = (fix_sorted['x'] * img_w).values
        ys = (fix_sorted['y'] * img_h).values
    xs = np.asarray(xs, dtype=np.float64)
    ys = np.asarray(ys, dtype=np.float64)

    fig, ax = plt.subplots(figsize=(img_w / 100, img_h / 100), dpi=100)
    ax.imshow(img)

    ax.plot(xs, ys, '-', color='lime', linewidth=1.0, alpha=0.7)

    durations = np.asarray(fix_sorted['duration'], dtype=np.float64)
    sizes = np.clip(durations * 500.0, 20.0, 600.0)
    ax.scatter(xs, ys, s=sizes, c='red', alpha=0.6, edgecolors='white', linewidths=0.5, zorder=5)

    for i, (x, y) in enumerate(zip(xs, ys)):
        ax.text(x, y, str(i + 1), fontsize=6, ha='center', va='center', color='white', zorder=6)

    ax.set_xlim(0, img_w)
    ax.set_ylim(img_h, 0)
    ax.set_axis_off()
    plt.tight_layout(pad=0)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.savefig(output_path, bbox_inches='tight', pad_inches=0, dpi=150)
    plt.close(fig)
    return True


def export_painting_metrics_xls(participant_id, all_metrics, output_path):
    """Экспортирует метрики по каждой картине в XLSX (один лист на картину + сводный)."""
    rows = []
    for m in all_metrics:
        rows.append({
            'participant': participant_id,
            **m,
        })

    summary_df = pd.DataFrame(rows)

    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        summary_df.to_excel(writer, sheet_name='Summary', index=False)

        for m in all_metrics:
            name = m.get('painting', 'unknown')
            sheet_name = os.path.splitext(name)[0][:31]  # Excel limit: 31 chars
            single = pd.DataFrame([{
                'participant': participant_id,
                **m,
            }])
            single.to_excel(writer, sheet_name=sheet_name, index=False)

    print(f"[POST-P] XLS сохранён: {output_path}")


def export_fixations_xls(participant_id, painting_fixations, output_path):
    """Экспортирует фиксации (координаты, длительность) по каждой картине в XLSX."""
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        for painting_name, fix_df in painting_fixations.items():
            sheet_name = os.path.splitext(painting_name)[0][:31]
            out = fix_df.copy()
            out.insert(0, 'participant', participant_id)
            out.to_excel(writer, sheet_name=sheet_name, index=False)

    print(f"[POST-P] Фиксации XLS сохранены: {output_path}")


def export_painting_gaze_tsv(trial_df, painting_name, bbox, img_w, img_h,
                             participant_id, data_dir):
    """Экспортирует посэмпловые данные взгляда для одной картины в TSV.

    Координаты BPOGX/BPOGY преобразуются в пиксели картины (painting_x, painting_y).
    Если взгляд вне границ картины или невалиден — painting_x/painting_y = NaN,
    in_painting = False.

    Файл сохраняется в {data_dir}/paintings_{participant_id}_gaze/{painting_name}.tsv
    Возвращает путь к файлу или None при пустых данных.
    """
    if len(trial_df) == 0:
        return None

    out = trial_df.copy()

    # relative_time: секунды от начала показа картины
    if 'TIME' in out.columns and len(out) > 0:
        out['relative_time'] = out['TIME'] - out['TIME'].iloc[0]
    else:
        out['relative_time'] = np.nan

    # Векторизованное преобразование BPOG → пиксели картины
    if bbox and img_w and img_h and 'BPOGX' in out.columns:
        if bbox['img_w'] <= 0 or bbox['img_h'] <= 0:
            out['painting_x'] = np.nan
            out['painting_y'] = np.nan
            out['in_painting'] = False
        else:
            sx = out['BPOGX'] * bbox['screen_w']
            sy = out['BPOGY'] * bbox['screen_h']

            fit_mode = bbox.get('fit_mode', 'contain')
            if fit_mode == 'fill':
                rel_x = (sx - bbox['img_x']) / bbox['img_w']
                rel_y = (sy - bbox['img_y']) / bbox['img_h']
            else:
                # object-fit: contain — вычисляем content-rect через aspect ratio
                elem_w = bbox['img_w']
                elem_h = bbox['img_h']
                elem_x = bbox['img_x']
                elem_y = bbox['img_y']
                natural_aspect = img_w / img_h
                elem_aspect = elem_w / elem_h
                if natural_aspect > elem_aspect:
                    content_w = elem_w
                    content_h = elem_w / natural_aspect
                    content_x = float(elem_x)
                    content_y = elem_y + (elem_h - content_h) / 2.0
                else:
                    content_h = elem_h
                    content_w = elem_h * natural_aspect
                    content_x = elem_x + (elem_w - content_w) / 2.0
                    content_y = float(elem_y)
                rel_x = (sx - content_x) / content_w
                rel_y = (sy - content_y) / content_h

            inside = (rel_x >= 0) & (rel_x <= 1) & (rel_y >= 0) & (rel_y <= 1)
            if 'BPOGV' in out.columns:
                inside = inside & (out['BPOGV'] == 1)

            out['painting_x'] = np.where(inside, rel_x * img_w, np.nan)
            out['painting_y'] = np.where(inside, rel_y * img_h, np.nan)
            out['in_painting'] = inside
    else:
        out['painting_x'] = np.nan
        out['painting_y'] = np.nan
        out['in_painting'] = False

    # Порядок колонок: новые → оригинальные полезные → маркер
    gaze_cols = ['BPOGX', 'BPOGY', 'BPOGV']
    fix_cols = ['FPOGX', 'FPOGY', 'FPOGD', 'FPOGID', 'FPOGV']
    pupil_cols = ['LPD', 'RPD', 'LPV', 'RPV', 'LPUPILD', 'RPUPILD', 'LPUPILV', 'RPUPILV']
    marker_cols = ['USER']

    columns = ['relative_time', 'TIME', 'painting_x', 'painting_y', 'in_painting']
    for group in (gaze_cols, fix_cols, pupil_cols, marker_cols):
        columns.extend(c for c in group if c in out.columns)

    out = out[[c for c in columns if c in out.columns]]

    gaze_dir = os.path.join(data_dir, f"paintings_{participant_id}_gaze")
    os.makedirs(gaze_dir, exist_ok=True)
    base = os.path.splitext(painting_name)[0]
    tsv_path = os.path.join(gaze_dir, f"{base}.tsv")
    out.to_csv(tsv_path, sep='\t', index=False)
    return tsv_path


def analyze_paintings_participant(
    gaze_file, participant_id, painting_order, painting_ratings,
    paintings_dir, data_dir, painting_bboxes=None,
    painting_decision_times=None,
):
    """Полный анализ фазы картин для одного участника."""
    print(f"\n[POST-P] Анализ картин для участника {participant_id}")
    df = load_gaze_data(gaze_file)

    # Extract paintings baseline
    baseline = extract_paintings_baseline_pupil(df)
    if not baseline:
        print("  [WARN] Baseline для фазы картин не найден, pupil_change не будет вычислен")

    heatmap_dir = os.path.join(data_dir, f"paintings_{participant_id}_heatmaps")
    traj_dir = os.path.join(data_dir, f"paintings_{participant_id}_trajectories")
    os.makedirs(heatmap_dir, exist_ok=True)
    os.makedirs(traj_dir, exist_ok=True)

    all_metrics = []
    all_fixations = {}

    for idx, filename in enumerate(painting_order):
        trial_df = extract_painting_trial(df, filename)
        if len(trial_df) == 0:
            print(f"  {filename}: нет данных, пропуск")
            continue

        metrics = compute_painting_metrics(trial_df, baseline=baseline if baseline else None)
        metrics['painting'] = filename
        metrics['order'] = idx + 1
        metrics['rating'] = painting_ratings.get(filename, '?')
        if painting_decision_times:
            metrics['decision_time_ms'] = painting_decision_times.get(filename)
        all_metrics.append(metrics)

        painting_path = os.path.join(paintings_dir, filename)
        if not os.path.isfile(painting_path):
            print(f"  {filename}: файл картины не найден, пропуск визуализаций")
            continue

        bbox = painting_bboxes.get(filename) if painting_bboxes else None
        base = os.path.splitext(filename)[0]
        hm_ok = generate_heatmap(
            trial_df, painting_path,
            os.path.join(heatmap_dir, f"{base}_heatmap.png"),
            bbox=bbox,
        )
        tr_ok = generate_trajectory(
            trial_df, painting_path,
            os.path.join(traj_dir, f"{base}_trajectory.png"),
            bbox=bbox,
        )

        img = Image.open(painting_path)
        img_w, img_h = img.size
        fix = _fixations_from_trial(trial_df, bbox=bbox, img_w=img_w, img_h=img_h)
        if len(fix) > 0:
            all_fixations[filename] = fix

        gaze_tsv = export_painting_gaze_tsv(
            trial_df, filename, bbox, img_w, img_h, participant_id, data_dir,
        )

        status = []
        if hm_ok:
            status.append("heatmap")
        if tr_ok:
            status.append("trajectory")
        if gaze_tsv:
            status.append("gaze_tsv")
        status.append(f"{metrics['fixation_count']} fix")
        print(f"  {filename}: {', '.join(status)}")

    if all_metrics:
        xls_path = os.path.join(data_dir, f"paintings_{participant_id}_metrics.xlsx")
        try:
            export_painting_metrics_xls(participant_id, all_metrics, xls_path)
        except ModuleNotFoundError as exc:
            if exc.name == 'openpyxl':
                print("  [WARN] openpyxl не установлен, XLS-экспорт метрик пропущен")
            else:
                raise

    if all_fixations:
        fix_xls_path = os.path.join(data_dir, f"paintings_{participant_id}_fixations.xlsx")
        try:
            export_fixations_xls(participant_id, all_fixations, fix_xls_path)
        except ModuleNotFoundError as exc:
            if exc.name == 'openpyxl':
                print("  [WARN] openpyxl не установлен, XLS-экспорт фиксаций пропущен")
            else:
                raise

    print(f"[POST-P] Анализ картин завершён: {len(all_metrics)} из {len(painting_order)}")
    return all_metrics


def _read_first_csv(data_dir, pattern):
    matches = sorted(glob.glob(os.path.join(data_dir, pattern)))
    if not matches:
        raise FileNotFoundError(f"Не найден файл по шаблону: {pattern} в {data_dir}")
    return matches[0]


def _load_order(order_csv, limit=None):
    df = pd.read_csv(order_csv)
    if 'filename' not in df.columns:
        raise ValueError(f"В order-файле нет колонки filename: {order_csv}")
    order = df['filename'].dropna().astype(str).tolist()
    return order[:limit] if limit else order


def _load_ratings(ratings_csv):
    df = pd.read_csv(ratings_csv)
    if 'filename' not in df.columns:
        return {}, {}
    rating_col = 'rating' if 'rating' in df.columns else None
    if rating_col is None:
        return {}, {}
    ratings = dict(zip(df['filename'].astype(str), df[rating_col]))
    decision_times = {}
    if 'decision_time_ms' in df.columns:
        for _, row in df.iterrows():
            val = row['decision_time_ms']
            if pd.notna(val):
                decision_times[str(row['filename'])] = float(val)
    return ratings, decision_times


def _load_bboxes(bboxes_csv):
    df = pd.read_csv(bboxes_csv)
    required = ['filename', 'img_x', 'img_y', 'img_w', 'img_h', 'screen_w', 'screen_h']
    if any(col not in df.columns for col in required):
        return {}
    for col in required[1:]:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=required)

    out = {}
    for row in df.to_dict(orient='records'):
        name = str(row.get('filename'))
        out[name] = {
            'img_x': float(row.get('img_x', 0.0)),
            'img_y': float(row.get('img_y', 0.0)),
            'img_w': float(row.get('img_w', 0.0)),
            'img_h': float(row.get('img_h', 0.0)),
            'screen_w': float(row.get('screen_w', 0.0)),
            'screen_h': float(row.get('screen_h', 0.0)),
            'fit_mode': str(row.get('fit_mode', 'contain')),
        }
    return out


def run_offline_paintings_analysis(data_dir='data/data_1103', paintings_dir='paintings', limit=10):
    """Офлайн-запуск анализа фазы картин по сохранённым данным участника."""
    gaze_file = _read_first_csv(data_dir, 'gaze_*_paintings_trial.tsv')
    order_csv = _read_first_csv(data_dir, 'paintings_*_order_*.csv')
    ratings_csv = _read_first_csv(data_dir, 'paintings_*_ratings_*.csv')
    bboxes_csv = _read_first_csv(data_dir, 'paintings_*_bboxes_*.csv')

    participant_id = os.path.basename(gaze_file).replace('gaze_', '').replace('_paintings_trial.tsv', '')
    painting_order = _load_order(order_csv, limit=limit)
    painting_ratings, painting_decision_times = _load_ratings(ratings_csv)
    painting_bboxes = _load_bboxes(bboxes_csv)

    print(f"[POST-P] Офлайн запуск: data_dir={data_dir}, participant={participant_id}, limit={len(painting_order)}")
    return analyze_paintings_participant(
        gaze_file=gaze_file,
        participant_id=participant_id,
        painting_order=painting_order,
        painting_ratings=painting_ratings,
        paintings_dir=paintings_dir,
        data_dir=data_dir,
        painting_bboxes=painting_bboxes if painting_bboxes else None,
        painting_decision_times=painting_decision_times if painting_decision_times else None,
    )


if __name__ == '__main__':
    run_offline_paintings_analysis()
