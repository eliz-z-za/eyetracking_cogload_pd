# ===== analysis.py =====
# Общие утилиты для анализа гейза GP3.
# Импортируется из analysis_paintings.py и analysis_pd.py.

import pandas as pd
import numpy as np
from scipy import stats
import os
import glob


def load_gaze_data(filepath):
    """Загружает TSV-файл PyOpenGaze"""
    df = pd.read_csv(filepath, sep='\t')
    
    # Преобразуем типы
    numeric_cols = ['TIME', 'FPOGX', 'FPOGY', 'FPOGD', 'FPOGID',
                    'FPOGV', 'LPD', 'LPS', 'LPV', 'RPD', 'RPS', 'RPV',
                    'BPOGX', 'BPOGY']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    
    return df


def extract_trial_data(df: pd.DataFrame, marker_start: str, marker_end: str) -> pd.DataFrame:
    """Извлекает данные между двумя маркерами"""
    # Найти индексы маркеров
    start_idx = df[df['USER'] == marker_start].index
    end_idx = df[df['USER'] == marker_end].index
    
    if len(start_idx) == 0 or len(end_idx) == 0:
        print(f"  ⚠ Маркеры не найдены: {marker_start} / {marker_end}")
        return pd.DataFrame()
    
    return df.loc[start_idx[0]:end_idx[0]].copy()


def compute_fixation_metrics(trial_df):
    """Вычисляет метрики фиксаций"""
    # Только валидные данные фиксаций
    valid = trial_df[trial_df['FPOGV'] == 1].copy()
    
    if len(valid) == 0:
        return {}
    
    # Уникальные фиксации (по FPOGID)
    fixations = valid.groupby('FPOGID').agg({
        'FPOGX': 'mean',
        'FPOGY': 'mean',
        'FPOGD': 'max',  # длительность фиксации
    }).reset_index()
    
    # Метрики
    metrics = {
        'fixation_count': len(fixations),
        'fixation_duration_mean': fixations['FPOGD'].mean(),
        'fixation_duration_median': fixations['FPOGD'].median(),
        'fixation_duration_std': fixations['FPOGD'].std(),
        'fixation_duration_total': fixations['FPOGD'].sum(),
    }
    
    return metrics


def extract_baseline_pupil(df, provider):
    """Извлекает среднее значение зрачка из baseline-периода.

    Возвращает dict: baseline_mean_left, baseline_mean_right, baseline_mean_avg
    или пустой dict, если baseline не найден.
    """
    marker_start = f"BASELINE_START_{provider.upper()}"
    marker_end = f"BASELINE_END_{provider.upper()}"

    baseline_df = extract_trial_data(df, marker_start, marker_end)
    if len(baseline_df) == 0:
        return {}

    # Отбрасываем первые 0.5с (стабилизация зрачка после смены экрана)
    if 'TIME' in baseline_df.columns and len(baseline_df) > 1:
        t0 = baseline_df['TIME'].iloc[0]
        baseline_df = baseline_df.loc[baseline_df['TIME'] >= t0 + 0.5]
    if len(baseline_df) == 0:
        return {}

    # Speed filter: отбрасываем сэмплы с резким изменением диаметра (> 0.5мм за сэмпл)
    for col, valid_col in [('LPD', 'LPV'), ('RPD', 'RPV')]:
        if col in baseline_df.columns:
            speed = baseline_df[col].diff().abs()
            baseline_df = baseline_df.loc[speed.isna() | (speed <= 0.5)]

    lpd = baseline_df.loc[baseline_df['LPV'] == 1, 'LPD'] if 'LPV' in baseline_df.columns else pd.Series(dtype=float)
    rpd = baseline_df.loc[baseline_df['RPV'] == 1, 'RPD'] if 'RPV' in baseline_df.columns else pd.Series(dtype=float)

    if 'LPV' in baseline_df.columns and 'RPV' in baseline_df.columns:
        both_valid = baseline_df.loc[(baseline_df['LPV'] == 1) & (baseline_df['RPV'] == 1)]
    else:
        both_valid = pd.DataFrame()
    if len(both_valid) > 0:
        avg = ((both_valid['LPD'] + both_valid['RPD']) / 2).mean()
    else:
        avg = np.nan

    return {
        'baseline_mean_left': lpd.mean() if len(lpd) > 0 else np.nan,
        'baseline_mean_right': rpd.mean() if len(rpd) > 0 else np.nan,
        'baseline_mean_avg': avg,
    }


def compute_pupil_metrics(trial_df, baseline=None):
    """Вычисляет метрики зрачка"""
    # Левый зрачок (валидные сэмплы)
    lpd_valid = trial_df[trial_df['LPV'] == 1]['LPD']
    rpd_valid = trial_df[trial_df['RPV'] == 1]['RPD']
    
    # Средний зрачок: попарное среднее для сэмплов с обоими валидными глазами
    both_valid = trial_df[(trial_df['LPV'] == 1) & (trial_df['RPV'] == 1)]
    pupil_avg = (both_valid['LPD'] + both_valid['RPD']) / 2 if len(both_valid) > 0 else pd.Series(dtype=float)
    
    mean_left = lpd_valid.mean() if len(lpd_valid) > 0 else np.nan
    mean_right = rpd_valid.mean() if len(rpd_valid) > 0 else np.nan
    mean_avg = pupil_avg.mean() if len(pupil_avg) > 0 else np.nan

    metrics = {
        'pupil_mean_left': mean_left,
        'pupil_mean_right': mean_right,
        'pupil_mean_avg': mean_avg,
        'pupil_std_left': lpd_valid.std() if len(lpd_valid) > 0 else np.nan,
        'pupil_std_right': rpd_valid.std() if len(rpd_valid) > 0 else np.nan,
    }
    
    if baseline:
        bl = baseline.get('baseline_mean_left', np.nan)
        br = baseline.get('baseline_mean_right', np.nan)
        ba = baseline.get('baseline_mean_avg', np.nan)
        metrics['pupil_baseline_mean'] = ba
        metrics['pupil_change_left'] = (mean_left - bl) / bl * 100 if bl and not np.isnan(bl) and bl != 0 else np.nan
        metrics['pupil_change_right'] = (mean_right - br) / br * 100 if br and not np.isnan(br) and br != 0 else np.nan
        metrics['pupil_change_avg'] = (mean_avg - ba) / ba * 100 if ba and not np.isnan(ba) and ba != 0 else np.nan

    return metrics


def compute_scanpath_length(trial_df):
    """Вычисляет длину сканпути (суммарное расстояние между фиксациями)"""
    valid = trial_df[trial_df['FPOGV'] == 1].copy()
    
    if len(valid) < 2:
        return {'scanpath_length': 0.0}
    
    # Уникальные фиксации
    fixations = valid.groupby('FPOGID').agg({
        'FPOGX': 'mean',
        'FPOGY': 'mean',
    }).reset_index().sort_index()
    
    # Расстояния между последовательными фиксациями
    dx = fixations['FPOGX'].diff()
    dy = fixations['FPOGY'].diff()
    distances = np.sqrt(dx**2 + dy**2).dropna()
    
    return {
        'scanpath_length': distances.sum(),
        'saccade_count': len(distances),
        'saccade_amplitude_mean': distances.mean() if len(distances) > 0 else 0.0,
    }


def compute_trial_duration(trial_df):
    """Длительность trial'а в секундах"""
    if 'TIME' in trial_df.columns and len(trial_df) > 1:
        return trial_df['TIME'].iloc[-1] - trial_df['TIME'].iloc[0]
    return 0.0


