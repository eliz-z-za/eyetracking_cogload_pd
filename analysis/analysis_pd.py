# ===== analysis_pd.py =====
# Анализ данных айтрекинга для PD-эксперимента:
# метрики фиксаций, саккад, зрачка и статических AOI per task.
#
# Использование:
#   python analysis_pd.py <participant_id> [data_dir]
# Пример:
#   python analysis_pd.py 8769876987 "data/data second run"
#
# Выходной файл: data_dir/pd_<participant_id>_metrics.xlsx
#   Лист per_task   — одна строка на задание (12 заданий)
#   Лист per_block  — средние по блоку+условию
#   Лист summary    — средние по условию × типу задачи

import os
import glob
import json
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from frozen_utils import get_resource_dir, is_frozen
from analysis import load_gaze_data, extract_trial_data
from analysis_paintings import _fixations_from_trial, _saccades_from_fixations
from pupil_preprocessing import (
    DEFAULT_PARAMS, clean_gp3_trace,
    baseline_stats, task_pupil_stats,
)

# ------------------------------------------------------------------ #
# Константы для статических AOI (нормализованные координаты GP3, 0-1)
# ------------------------------------------------------------------ #
SCREEN_W = 1920
SCREEN_H = 1080
TASK_BAR_H_PX = 56   # высота task bar в пикселях
SIDEBAR_W_PX  = 72   # ширина sidebar в пикселях

_TASK_BAR_Y  = TASK_BAR_H_PX / SCREEN_H   # ≈ 0.0519
_SIDEBAR_X   = SIDEBAR_W_PX  / SCREEN_W   # ≈ 0.0375


# ------------------------------------------------------------------ #
# Загрузка метаданных заданий
# ------------------------------------------------------------------ #

def _tasks_json_path():
    base = get_resource_dir() if is_frozen() else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, 'static', 'data', 'tasks.json')


def load_tasks_meta(tasks_json=None):
    """Возвращает dict: task_id → {'type': ..., 'dataset': ...} для всех заданий."""
    path = tasks_json or _tasks_json_path()
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    meta = {}
    for task in data.get('alpha', []):
        meta[task['id']] = {'type': task['type'], 'dataset': 'alpha'}
    for task in data.get('beta', []):
        meta[task['id']] = {'type': task['type'], 'dataset': 'beta'}
    return meta


# ------------------------------------------------------------------ #
# Загрузка NASA-TLX данных
# ------------------------------------------------------------------ #

def load_nasa_tlx_scores(participant_id, data_dir='data'):
    """Загружает NASA-TLX JSON-файлы для участника (блоки 1 и 2).

    Возвращает list of dicts, каждый с ключами:
      participant, block_num,
      score_mental, score_temporal, score_performance, score_effort, score_frustration,
      weight_mental, ..., weighted_mental, ...,
      tlx_weighted_score, tlx_unweighted_mean,
      pair_mental_vs_temporal, ... (10 пар)
    """
    DIMS = ('mental', 'temporal', 'performance', 'effort', 'frustration')
    PAIR_KEYS = [
        'mental_vs_temporal', 'mental_vs_performance', 'mental_vs_effort',
        'mental_vs_frustration', 'temporal_vs_performance', 'temporal_vs_effort',
        'temporal_vs_frustration', 'performance_vs_effort', 'performance_vs_frustration',
        'effort_vs_frustration',
    ]
    rows = []
    for block_num in (1, 2):
        pattern = os.path.join(
            data_dir,
            f'pd_{participant_id}_nasa_tlx_b{block_num}_*.json'
        )
        files = sorted(glob.glob(pattern))
        if not files:
            print(f'  [WARN] NASA-TLX b{block_num}: файл не найден ({pattern})')
            continue
        with open(files[-1], encoding='utf-8') as f:
            data = json.load(f)

        row = {'participant': participant_id, 'block_num': block_num}

        scores = data.get('scores', {})
        for dim in DIMS:
            row[f'score_{dim}'] = scores.get(dim, np.nan)

        weights = data.get('weights', {})
        for dim in DIMS:
            row[f'weight_{dim}'] = weights.get(dim, np.nan)

        for dim in DIMS:
            s = row[f'score_{dim}']
            w = row[f'weight_{dim}']
            row[f'weighted_{dim}'] = (s * w
                                      if (not np.isnan(s) and not np.isnan(w))
                                      else np.nan)

        weighted_vals = [row[f'weighted_{d}'] for d in DIMS
                         if not np.isnan(row.get(f'weighted_{d}', np.nan))]
        row['tlx_weighted_score'] = (sum(weighted_vals) / 15
                                     if len(weighted_vals) == len(DIMS)
                                     else np.nan)

        score_vals = [row[f'score_{d}'] for d in DIMS
                      if not np.isnan(row.get(f'score_{d}', np.nan))]
        row['tlx_unweighted_mean'] = np.mean(score_vals) if score_vals else np.nan

        pairs = data.get('pairs', {})
        for pk in PAIR_KEYS:
            row[f'pair_{pk}'] = pairs.get(pk, '')

        rows.append(row)
    return rows


# ------------------------------------------------------------------ #
# Парсинг событий
# ------------------------------------------------------------------ #

def parse_pd_events(events_csv):
    """Читает events CSV → dict: task_id → метаданные задания.

    Возвращаемый dict для каждого task_id:
      block_num, condition, completion_ms, clicks, start_wall, end_wall
    """
    df = pd.read_csv(events_csv)
    tasks = {}
    condition_map = {}  # block_num (int) → condition str ('flat' | 'sections')

    for _, row in df.iterrows():
        ev       = str(row['event'])
        provider = str(row.get('provider', ''))
        wall     = float(row['wall_time'])

        # PD_BLOCK_START_B1_FLAT_ALPHA → block 1, condition 'flat'
        m = re.match(r'PD_BLOCK_START_B(\d+)_(\w+?)_\w+', ev)
        if m:
            condition_map[int(m.group(1))] = m.group(2).lower()
            continue

        # PD_TASK_START_A1
        m = re.match(r'PD_TASK_START_(\w+)', ev)
        if m:
            tid = m.group(1)
            tasks.setdefault(tid, {})['start_wall'] = wall
            bm = re.match(r'pd_block(\d+)', provider)
            if bm:
                bn = int(bm.group(1))
                tasks[tid]['block_num']  = bn
                tasks[tid]['condition']  = condition_map.get(bn, '')
            continue

        # PD_TASK_END_A1_ct10703ms_clicks1
        m = re.match(r'PD_TASK_END_(\w+?)_ct(\d+)ms_clicks(\d+)', ev)
        if m:
            tid = m.group(1)
            tasks.setdefault(tid, {})
            tasks[tid]['end_wall']      = wall
            tasks[tid]['completion_ms'] = int(m.group(2))
            tasks[tid]['clicks']        = int(m.group(3))
            continue

    return tasks


# ------------------------------------------------------------------ #
# Извлечение гейза по маркерам
# ------------------------------------------------------------------ #

def extract_task_gaze(block_df, task_id):
    """Строки гейза между PD_TASK_START_{id} и PD_TASK_END_{id}."""
    df = extract_trial_data(block_df, f'PD_TASK_START_{task_id}', f'PD_TASK_END_{task_id}')
    return df if df is not None else pd.DataFrame()


def extract_iti_gaze(block_df, task_id):
    """Строки гейза для ITI-baseline перед заданием."""
    df = extract_trial_data(block_df, f'PD_ITI_START_{task_id}', f'PD_ITI_END_{task_id}')
    return df if df is not None else pd.DataFrame()


# ------------------------------------------------------------------ #
# Baseline зрачка (ITI)
# ------------------------------------------------------------------ #

def compute_iti_pupil_baseline(iti_df, params=None):
    """Базовый уровень зрачка в ITI через пайплайн pupil_preprocessing.

    Совместим с legacy-форматом (те же ключи), плюс QC-поля.
    baseline_l/r_mean/std — NaN: новый пайплайн работает с бинокулярным
    усреднением (at-least-one-eye-valid); per-eye stats не вычисляются
    в clean_gp3_trace. Поля сохранены для совместимости с legacy-кодом.
    """
    if iti_df is None or len(iti_df) == 0:
        return {}

    cleaned = clean_gp3_trace(iti_df, params=params, drop_first_sec=0.5)
    stats = baseline_stats(cleaned, params=params)
    return {
        'baseline_avg_mean':          stats.get('baseline_avg_mean', np.nan),
        'baseline_avg_std':           stats.get('baseline_avg_std',  np.nan),
        # per-eye: NaN — новый пайплайн использует бинокулярное усреднение
        'baseline_l_mean':            np.nan,
        'baseline_l_std':             np.nan,
        'baseline_r_mean':            np.nan,
        'baseline_r_std':             np.nan,
        'baseline_pct_unrecoverable': stats.get('baseline_pct_unrecoverable', np.nan),
        'baseline_n_valid':           stats.get('baseline_n_valid', np.nan),
    }


# ------------------------------------------------------------------ #
# Основные метрики задания
# ------------------------------------------------------------------ #

def compute_task_metrics(task_df, iti_baseline, task_meta, params=None):
    """Все метрики для одного задания. Возвращает плоский dict."""
    metrics = {
        'participant':    task_meta.get('participant', ''),
        'task_id':        task_meta.get('task_id', ''),
        'block_num':      task_meta.get('block_num', ''),
        'condition':      task_meta.get('condition', ''),
        'task_type':      task_meta.get('type', ''),
        'dataset':        task_meta.get('dataset', ''),
        'completion_ms':  task_meta.get('completion_ms', np.nan),
        'clicks':         task_meta.get('clicks', np.nan),
    }

    if task_df is None or len(task_df) == 0:
        return metrics

    # --- Длительность ---
    duration = 0.0
    if 'TIME' in task_df.columns and len(task_df) > 1:
        duration = task_df['TIME'].iloc[-1] - task_df['TIME'].iloc[0]
    metrics['viewing_duration_sec'] = duration

    # --- Фиксации и саккады ---
    fix  = _fixations_from_trial(task_df)
    sacc = _saccades_from_fixations(fix)

    metrics['fixation_count']          = len(fix)
    metrics['fixation_duration_mean']  = fix['duration'].mean()   if len(fix) else np.nan
    metrics['fixation_duration_median']= fix['duration'].median() if len(fix) else np.nan
    metrics['fixation_duration_std']   = fix['duration'].std()    if len(fix) else np.nan
    metrics['fixation_duration_total'] = fix['duration'].sum()    if len(fix) else 0.0
    metrics['fixation_rate_per_sec']   = len(fix) / duration      if duration > 0 else np.nan
    metrics['saccade_count']           = len(sacc)
    metrics['saccade_amplitude_mean']  = sacc['amplitude'].mean() if len(sacc) else np.nan
    metrics['scanpath_length']         = sacc['amplitude'].sum()  if len(sacc) else 0.0

    # --- Зрачок (новый пайплайн) ---
    cleaned     = clean_gp3_trace(task_df, params=params, drop_first_sec=0)
    pupil_stats = task_pupil_stats(cleaned, iti_baseline or {}, params=params)
    metrics['pupil_mean_avg']         = pupil_stats.get('pupil_mean_avg',         np.nan)
    metrics['pupil_subtractive_avg']  = pupil_stats.get('pupil_subtractive_avg',  np.nan)
    metrics['pupil_z_avg']            = pupil_stats.get('pupil_z_avg',            np.nan)
    metrics['pupil_pct_change_avg']   = pupil_stats.get('pupil_pct_change_avg',   np.nan)
    metrics['task_pct_unrecoverable'] = pupil_stats.get('task_pct_unrecoverable', np.nan)
    metrics['task_n_valid']           = pupil_stats.get('task_n_valid',           np.nan)
    metrics['baseline_avg_mean']      = (iti_baseline or {}).get('baseline_avg_mean', np.nan)
    metrics['baseline_avg_std']       = (iti_baseline or {}).get('baseline_avg_std',  np.nan)
    # per-eye: простое среднее по valid-флагу (без speed filter), для совместимости
    lpd = task_df[task_df['LPV'] == 1]['LPD'] if 'LPV' in task_df.columns else pd.Series(dtype=float)
    rpd = task_df[task_df['RPV'] == 1]['RPD'] if 'RPV' in task_df.columns else pd.Series(dtype=float)
    metrics['pupil_mean_left']        = lpd.mean() if len(lpd) else np.nan
    metrics['pupil_mean_right']       = rpd.mean() if len(rpd) else np.nan
    # pupil_pct_change_left/right → NaN: per-eye baseline недоступен в новом pipeline
    metrics['pupil_pct_change_left']  = np.nan
    metrics['pupil_pct_change_right'] = np.nan

    # --- Статические AOI ---
    # task bar: верхняя полоса (y < TASK_BAR_H/SCREEN_H)
    # sidebar:  левая полоса  (x < SIDEBAR_W/SCREEN_W), ниже task bar
    # content:  всё остальное
    valid_fp = task_df[task_df['FPOGV'] == 1].copy() if 'FPOGV' in task_df.columns else pd.DataFrame()
    if len(valid_fp) > 0:
        tb_mask = valid_fp['FPOGY'] < _TASK_BAR_Y
        sb_mask = (~tb_mask) & (valid_fp['FPOGX'] < _SIDEBAR_X)
        ct_mask = ~tb_mask & ~sb_mask

        # временно́й шаг (медиана интервала между сэмплами)
        if 'TIME' in valid_fp.columns and len(valid_fp) > 1:
            dt = valid_fp['TIME'].diff().median()
            dt = dt if (pd.notna(dt) and 0 < dt < 1.0) else 1 / 60
        else:
            dt = 1 / 60

        n_total = len(valid_fp)
        metrics['aoi_taskbar_dwell_s'] = tb_mask.sum() * dt
        metrics['aoi_sidebar_dwell_s'] = sb_mask.sum() * dt
        metrics['aoi_content_dwell_s'] = ct_mask.sum() * dt
        metrics['aoi_taskbar_pct'] = tb_mask.sum() / n_total * 100 if n_total else np.nan
        metrics['aoi_sidebar_pct'] = sb_mask.sum() / n_total * 100 if n_total else np.nan
        metrics['aoi_content_pct'] = ct_mask.sum() / n_total * 100 if n_total else np.nan

        # количество фиксаций per AOI
        if len(fix) > 0:
            ftb = fix[fix['y'] < _TASK_BAR_Y]
            fsb = fix[(fix['y'] >= _TASK_BAR_Y) & (fix['x'] < _SIDEBAR_X)]
            fct = fix[(fix['y'] >= _TASK_BAR_Y) & (fix['x'] >= _SIDEBAR_X)]
            metrics['aoi_taskbar_fix_count'] = len(ftb)
            metrics['aoi_sidebar_fix_count'] = len(fsb)
            metrics['aoi_content_fix_count'] = len(fct)

    return metrics


# ------------------------------------------------------------------ #
# Анализ одного участника
# ------------------------------------------------------------------ #

def analyze_participant(participant_id, data_dir='data', tasks_json=None):
    """Полный PD-анализ одного участника. Возвращает list of dicts (per-task)."""
    print(f"\nPD анализ: участник {participant_id}")

    events_files = sorted(glob.glob(os.path.join(data_dir, f'events_{participant_id}_*.csv')))
    if not events_files:
        print('  [ERR] events CSV не найден')
        return []
    events_csv = events_files[-1]
    print(f'  Events: {os.path.basename(events_csv)}')

    task_events = parse_pd_events(events_csv)
    tasks_meta  = load_tasks_meta(tasks_json)

    rows = []

    for block_num in (1, 2):
        gaze_path = os.path.join(data_dir, f'gaze_{participant_id}_pd_b{block_num}_trial.tsv')
        if not os.path.isfile(gaze_path):
            print(f'  [SKIP] Блок {block_num}: файл гейза не найден ({os.path.basename(gaze_path)})')
            continue

        print(f'  Блок {block_num}: {os.path.basename(gaze_path)}')
        block_df = load_gaze_data(gaze_path)

        block_tasks = {
            tid: ev for tid, ev in task_events.items()
            if ev.get('block_num') == block_num
        }
        if not block_tasks:
            print(f'    ⚠ Нет заданий для блока {block_num} в events CSV')
            continue

        for task_id, ev_meta in block_tasks.items():
            task_df  = extract_task_gaze(block_df, task_id)
            iti_df   = extract_iti_gaze(block_df, task_id)
            baseline = compute_iti_pupil_baseline(iti_df)

            task_meta = {
                'participant':   participant_id,
                'task_id':       task_id,
                'block_num':     block_num,
                'condition':     ev_meta.get('condition', ''),
                'completion_ms': ev_meta.get('completion_ms', np.nan),
                'clicks':        ev_meta.get('clicks', np.nan),
                **tasks_meta.get(task_id, {'type': '', 'dataset': ''}),
            }

            metrics = compute_task_metrics(
                task_df if len(task_df) > 0 else None,
                baseline,
                task_meta,
            )
            rows.append(metrics)

            n_fix = metrics.get('fixation_count', '?')
            print(f'    {task_id} ({task_meta.get("type","?")}): '
                  f'{ev_meta.get("completion_ms","?")} ms, '
                  f'{ev_meta.get("clicks","?")} clicks, '
                  f'{n_fix} fix')

    return rows


# ------------------------------------------------------------------ #
# Агрегация и сохранение
# ------------------------------------------------------------------ #

def _per_block_summary(per_task_df):
    group_keys = ['participant', 'block_num', 'condition']
    numeric = [c for c in per_task_df.select_dtypes(include='number').columns if c not in group_keys]
    return (per_task_df
            .groupby(group_keys)[numeric]
            .mean()
            .reset_index())


def _condition_summary(per_task_df):
    group_keys = ['condition', 'task_type']
    numeric = [c for c in per_task_df.select_dtypes(include='number').columns if c not in group_keys]
    agg = (per_task_df
           .groupby(group_keys)[numeric]
           .agg(['mean', 'std'])
           .reset_index())
    # Flatten MultiIndex columns: ('completion_ms', 'mean') → 'completion_ms_mean'
    agg.columns = ['_'.join(filter(None, col)) if isinstance(col, tuple) else col
                   for col in agg.columns]
    return agg


def save_results(rows, participant_id, data_dir='data', tlx_rows=None):
    """Сохраняет метрики в pd_{participant_id}_metrics.xlsx."""
    if not rows:
        print('  [WARN] Нет данных для сохранения.')
        return None

    per_task = pd.DataFrame(rows)
    per_block = _per_block_summary(per_task)
    summary   = _condition_summary(per_task)

    out_path = os.path.join(data_dir, f'pd_{participant_id}_metrics.xlsx')
    with pd.ExcelWriter(out_path, engine='openpyxl') as writer:
        per_task.to_excel(writer, sheet_name='per_task',  index=False)
        per_block.to_excel(writer, sheet_name='per_block', index=False)
        summary.to_excel(writer,   sheet_name='summary',   index=False)
        if tlx_rows:
            pd.DataFrame(tlx_rows).to_excel(writer, sheet_name='nasa_tlx', index=False)

    print(f'  [OK] Сохранено: {out_path}')
    return out_path


def run(participant_id, data_dir='data', tasks_json=None):
    rows     = analyze_participant(participant_id, data_dir, tasks_json)
    tlx_rows = load_nasa_tlx_scores(participant_id, data_dir)
    return save_results(rows, participant_id, data_dir, tlx_rows=tlx_rows)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python analysis_pd.py <participant_id> [data_dir]')
        sys.exit(1)
    _pid  = sys.argv[1]
    _ddir = sys.argv[2] if len(sys.argv) > 2 else 'data'
    run(_pid, _ddir)


# ------------------------------------------------------------------ #
# LEGACY v1: исходные функции до интеграции pupil_preprocessing.
# Используются для sensitivity-анализа (сравнение старых и новых метрик).
# ------------------------------------------------------------------ #

def compute_iti_pupil_baseline_legacy(iti_df):
    """Среднее и стд зрачка в ITI (с фильтром скоростных скачков). LEGACY v1.

    Возвращает dict с ключами baseline_avg_mean, baseline_avg_std,
    baseline_l_mean, baseline_r_mean или пустой dict.
    """
    if iti_df is None or len(iti_df) == 0:
        return {}

    iti = iti_df.copy()

    # отбрасываем первые 0.5 с (стабилизация зрачка)
    if 'TIME' in iti.columns and len(iti) > 1:
        t0 = iti['TIME'].iloc[0]
        iti = iti[iti['TIME'] >= t0 + 0.5]
    if len(iti) == 0:
        return {}

    # фильтр скоростных скачков: убираем сэмплы с |ΔD| > 0.5 мм
    for col in ('LPD', 'RPD'):
        if col in iti.columns:
            speed = iti[col].diff().abs()
            iti = iti[speed.isna() | (speed <= 0.5)]
    if len(iti) == 0:
        return {}

    lpd = iti[iti['LPV'] == 1]['LPD'] if 'LPV' in iti.columns else pd.Series(dtype=float)
    rpd = iti[iti['RPV'] == 1]['RPD'] if 'RPV' in iti.columns else pd.Series(dtype=float)

    if 'LPV' in iti.columns and 'RPV' in iti.columns:
        both = iti[(iti['LPV'] == 1) & (iti['RPV'] == 1)]
        avg  = (both['LPD'] + both['RPD']) / 2 if len(both) > 0 else pd.Series(dtype=float)
    else:
        avg = pd.Series(dtype=float)

    return {
        'baseline_l_mean':   lpd.mean() if len(lpd) else np.nan,
        'baseline_l_std':    lpd.std()  if len(lpd) else np.nan,
        'baseline_r_mean':   rpd.mean() if len(rpd) else np.nan,
        'baseline_r_std':    rpd.std()  if len(rpd) else np.nan,
        'baseline_avg_mean': avg.mean() if len(avg) else np.nan,
        'baseline_avg_std':  avg.std()  if len(avg) else np.nan,
    }


def compute_task_metrics_legacy(task_df, iti_baseline, task_meta):
    """Все метрики для одного задания. LEGACY v1 — до интеграции pupil_preprocessing."""
    metrics = {
        'participant':    task_meta.get('participant', ''),
        'task_id':        task_meta.get('task_id', ''),
        'block_num':      task_meta.get('block_num', ''),
        'condition':      task_meta.get('condition', ''),
        'task_type':      task_meta.get('type', ''),
        'dataset':        task_meta.get('dataset', ''),
        'completion_ms':  task_meta.get('completion_ms', np.nan),
        'clicks':         task_meta.get('clicks', np.nan),
    }

    if task_df is None or len(task_df) == 0:
        return metrics

    duration = 0.0
    if 'TIME' in task_df.columns and len(task_df) > 1:
        duration = task_df['TIME'].iloc[-1] - task_df['TIME'].iloc[0]
    metrics['viewing_duration_sec'] = duration

    fix  = _fixations_from_trial(task_df)
    sacc = _saccades_from_fixations(fix)

    metrics['fixation_count']          = len(fix)
    metrics['fixation_duration_mean']  = fix['duration'].mean()   if len(fix) else np.nan
    metrics['fixation_duration_median']= fix['duration'].median() if len(fix) else np.nan
    metrics['fixation_duration_std']   = fix['duration'].std()    if len(fix) else np.nan
    metrics['fixation_duration_total'] = fix['duration'].sum()    if len(fix) else 0.0
    metrics['fixation_rate_per_sec']   = len(fix) / duration      if duration > 0 else np.nan
    metrics['saccade_count']           = len(sacc)
    metrics['saccade_amplitude_mean']  = sacc['amplitude'].mean() if len(sacc) else np.nan
    metrics['scanpath_length']         = sacc['amplitude'].sum()  if len(sacc) else 0.0

    lpd = task_df[task_df['LPV'] == 1]['LPD'] if 'LPV' in task_df.columns else pd.Series(dtype=float)
    rpd = task_df[task_df['RPV'] == 1]['RPD'] if 'RPV' in task_df.columns else pd.Series(dtype=float)

    if 'LPV' in task_df.columns and 'RPV' in task_df.columns:
        both      = task_df[(task_df['LPV'] == 1) & (task_df['RPV'] == 1)]
        avg_pupil = (both['LPD'] + both['RPD']) / 2 if len(both) > 0 else pd.Series(dtype=float)
    else:
        avg_pupil = pd.Series(dtype=float)

    mean_l   = lpd.mean()       if len(lpd)       else np.nan
    mean_r   = rpd.mean()       if len(rpd)       else np.nan
    mean_avg = avg_pupil.mean() if len(avg_pupil) else np.nan

    metrics['pupil_mean_left']  = mean_l
    metrics['pupil_mean_right'] = mean_r
    metrics['pupil_mean_avg']   = mean_avg

    if iti_baseline:
        ba_mean = iti_baseline.get('baseline_avg_mean', np.nan)
        ba_std  = iti_baseline.get('baseline_avg_std',  np.nan)
        bl_mean = iti_baseline.get('baseline_l_mean',   np.nan)
        br_mean = iti_baseline.get('baseline_r_mean',   np.nan)

        def _pct(val, ref):
            return (val - ref) / ref * 100 if (ref and not np.isnan(ref) and ref != 0 and not np.isnan(val)) else np.nan

        def _z(val, mu, sigma):
            return (val - mu) / sigma if (sigma and not np.isnan(sigma) and sigma != 0 and not np.isnan(val)) else np.nan

        metrics['pupil_pct_change_avg']   = _pct(mean_avg, ba_mean)
        metrics['pupil_pct_change_left']  = _pct(mean_l,   bl_mean)
        metrics['pupil_pct_change_right'] = _pct(mean_r,   br_mean)
        metrics['pupil_z_avg']            = _z(mean_avg, ba_mean, ba_std)

    valid_fp = task_df[task_df['FPOGV'] == 1].copy() if 'FPOGV' in task_df.columns else pd.DataFrame()
    if len(valid_fp) > 0:
        tb_mask = valid_fp['FPOGY'] < _TASK_BAR_Y
        sb_mask = (~tb_mask) & (valid_fp['FPOGX'] < _SIDEBAR_X)
        ct_mask = ~tb_mask & ~sb_mask

        if 'TIME' in valid_fp.columns and len(valid_fp) > 1:
            dt = valid_fp['TIME'].diff().median()
            dt = dt if (pd.notna(dt) and 0 < dt < 1.0) else 1 / 60
        else:
            dt = 1 / 60

        n_total = len(valid_fp)
        metrics['aoi_taskbar_dwell_s'] = tb_mask.sum() * dt
        metrics['aoi_sidebar_dwell_s'] = sb_mask.sum() * dt
        metrics['aoi_content_dwell_s'] = ct_mask.sum() * dt
        metrics['aoi_taskbar_pct'] = tb_mask.sum() / n_total * 100 if n_total else np.nan
        metrics['aoi_sidebar_pct'] = sb_mask.sum() / n_total * 100 if n_total else np.nan
        metrics['aoi_content_pct'] = ct_mask.sum() / n_total * 100 if n_total else np.nan

        if len(fix) > 0:
            ftb = fix[fix['y'] < _TASK_BAR_Y]
            fsb = fix[(fix['y'] >= _TASK_BAR_Y) & (fix['x'] < _SIDEBAR_X)]
            fct = fix[(fix['y'] >= _TASK_BAR_Y) & (fix['x'] >= _SIDEBAR_X)]
            metrics['aoi_taskbar_fix_count'] = len(ftb)
            metrics['aoi_sidebar_fix_count'] = len(fsb)
            metrics['aoi_content_fix_count'] = len(fct)

    return metrics
