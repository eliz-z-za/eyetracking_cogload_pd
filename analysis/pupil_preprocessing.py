"""pupil_preprocessing.py
=========================
Единый модуль препроцессинга pupil-данных Gazepoint GP3 для PD-эксперимента.

Реализует пайплайн по Mathôt & Vilotijević (2023):
  1) бинокулярное усреднение по правилу at-least-one-eye-valid;
  2) speed filter (|ΔD| > thr → NaN), одинаково для baseline и task;
  3) margin ±N сэмплов вокруг любого NaN;
  4) интерполяция коротких гэпов (cubic при 2+2 опорных точках, иначе linear);
  5) финальный outlier-pass по ±SD;
  6) метрики baseline и task с тремя вариантами коррекции:
        * pupil_subtractive_avg = mean_task − mean_baseline   (рекомендовано, primary)
        * pupil_z_avg           = (mean_task − mean_baseline) / sd_baseline
        * pupil_pct_change_avg  = (mean_task − mean_baseline) / mean_baseline × 100
          (дивизивная, сохранена для совместимости — не рекомендована Mathôt et al. 2018);
  7) встроенный анализ распределения |ΔD| для калибровки порога speed filter.

Используется из analysis_pd.compute_iti_pupil_baseline и compute_task_metrics
вместо ручных фильтров.

Источники:
  Mathôt & Vilotijević (2023) Behav Res Methods 55:3055–3077
  Kret & Sjak-Shie (2019) Behav Res Methods 51:1336
  Mathôt et al. (2018) Behav Res Methods 50:94 (про subtractive vs divisive)
"""
from __future__ import annotations

import os
import glob
from dataclasses import dataclass, field
from typing import Iterable, Optional

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.interpolate import interp1d


# ─────────────────────────────────────────────────────────────────────────────
# Конфигурация
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_PARAMS = {
    # Порог dilation speed: |Δpupil| > thr пкс/сэмпл → NaN.
    # Текущее значение (0.5) калибровать по реальным данным через
    # collect_speed_deltas + summarize_speed_distribution. На синтетике с
    # bимодальным распределением артефактов 0.5 попадает в долину между модами.
    'speed_thr_px_per_sample': 0.5,

    # ±N сэмплов вокруг любого NaN (включая исходно невалидные) тоже NaN.
    # 2 сэмпла при 60 Hz = ~33 мс. Mathôt рекомендует 10 мс для EyeLink при
    # 1000 Hz, что в относительной шкале сопоставимо.
    'margin_samples': 2,

    # Финальный outlier-pass: |x − mean| > sd_thr × SD → NaN. По Mathôt — 3.
    'sd_thr': 3.0,

    # 'cubic' | 'linear' | 'none'. 'cubic' автоматически падает в linear,
    # когда нет 2+2 опорных точек.
    'interp_method': 'cubic',

    # Максимальная длина гэпа для интерполяции (в сэмплах).
    # 30 сэмплов при 60 Hz = 500 мс (рекомендация Mathôt 2013).
    'max_interp_gap_samples': 30,

    # Если после очистки в трайле меньше этого процента валидных сэмплов —
    # метрики возвращаются как NaN.
    'min_valid_pct_for_metrics': 50.0,
}


# ─────────────────────────────────────────────────────────────────────────────
# Структуры данных
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CleanedTrace:
    """Результат очистки одного трайла (task или ITI)."""
    t: np.ndarray             # timestamps (секунды)
    pupil_clean: np.ndarray   # очищенный сигнал, NaN где не восстановилось
    pupil_raw: np.ndarray     # сырой бинокулярный сигнал до чистки
    qc: dict = field(default_factory=dict)

    @property
    def n_total(self) -> int:
        return len(self.pupil_clean)

    @property
    def n_valid_final(self) -> int:
        return int(np.sum(~np.isnan(self.pupil_clean)))

    @property
    def pct_valid_final(self) -> float:
        return self.n_valid_final / max(self.n_total, 1) * 100


# ─────────────────────────────────────────────────────────────────────────────
# Sample-level чистка
# ─────────────────────────────────────────────────────────────────────────────

def build_binocular_pupil(lpd, rpd, lpv, rpv):
    """Бинокулярный сигнал по правилу at-least-one-eye-valid.

    Returns: pupil (np.ndarray, len=N), NaN где оба глаза невалидны.
    """
    lpd = np.asarray(lpd, dtype=float)
    rpd = np.asarray(rpd, dtype=float)
    lpv = np.asarray(lpv)
    rpv = np.asarray(rpv)

    both = (lpv == 1) & (rpv == 1)
    l_only = (lpv == 1) & (rpv != 1)
    r_only = (rpv == 1) & (lpv != 1)

    pupil = np.full(len(lpd), np.nan)
    pupil[both]   = (lpd[both] + rpd[both]) / 2
    pupil[l_only] = lpd[l_only]
    pupil[r_only] = rpd[r_only]
    return pupil


def _apply_speed_filter(pupil, thr):
    """|Δpupil| > thr → NaN. Возвращает (pupil_filtered, n_removed)."""
    p = pupil.copy()
    if len(p) < 2:
        return p, 0
    d = np.abs(np.diff(p, prepend=p[0]))
    # NaN-сэмплы оставляем как есть; их разница — NaN, что не пройдёт > thr
    mask_fast = (d > thr) & ~np.isnan(d)
    n_removed = int(mask_fast.sum())
    p[mask_fast] = np.nan
    return p, n_removed


def _expand_nan_margin(pupil, margin):
    """Расширяет каждый NaN на ±margin сэмплов."""
    if margin <= 0:
        return pupil
    p = pupil.copy()
    nan_mask = np.isnan(p)
    expanded = nan_mask.copy()
    for k in range(1, margin + 1):
        expanded[k:]  |= nan_mask[:-k]
        expanded[:-k] |= nan_mask[k:]
    p[expanded] = np.nan
    return p


def _interp_short_gaps(t, pupil, max_gap_samples, method='cubic'):
    """Интерполирует гэпы NaN длиной ≤ max_gap.

    Cubic при 2 опорных точках до И 2 после гэпа; иначе linear (1+1);
    иначе оставляет NaN. Длинные гэпы (> max_gap) тоже остаются NaN.

    Returns: (pupil_interp, stats)
    """
    p = pupil.copy()
    n = len(p)
    stats = {'cubic_filled': 0, 'linear_filled': 0, 'left_as_nan': 0}

    if method == 'none' or n < 2:
        stats['left_as_nan'] = int(np.isnan(p).sum())
        return p, stats

    is_nan = np.isnan(p)
    if not is_nan.any():
        return p, stats

    # Идентифицируем runs of NaN
    int_nan = is_nan.astype(np.int8)
    edges = np.diff(int_nan)
    starts = list((np.where(edges == 1)[0] + 1).tolist())
    ends   = list((np.where(edges == -1)[0] + 1).tolist())  # exclusive
    if is_nan[0]:
        starts.insert(0, 0)
    if is_nan[-1]:
        ends.append(n)

    valid_idx = np.where(~is_nan)[0]

    for gs, ge in zip(starts, ends):
        gap_len = ge - gs
        if gap_len > max_gap_samples:
            stats['left_as_nan'] += gap_len
            continue

        before = valid_idx[valid_idx < gs]
        after  = valid_idx[valid_idx >= ge]

        if len(before) == 0 or len(after) == 0:
            stats['left_as_nan'] += gap_len
            continue

        x_interp = t[gs:ge]

        # Пытаемся cubic
        can_cubic = (method == 'cubic') and (len(before) >= 2) and (len(after) >= 2)
        if can_cubic:
            pts = [before[-2], before[-1], after[0], after[1]]
            xk, yk = t[pts], p[pts]
            if np.all(np.diff(xk) > 0):
                try:
                    f = interp1d(xk, yk, kind='cubic', assume_sorted=True)
                    p[gs:ge] = f(x_interp)
                    stats['cubic_filled'] += gap_len
                    continue
                except Exception:
                    pass  # упадём в linear

        # Linear fallback
        pts = [before[-1], after[0]]
        xk, yk = t[pts], p[pts]
        if xk[1] > xk[0]:
            f = interp1d(xk, yk, kind='linear', assume_sorted=True)
            p[gs:ge] = f(x_interp)
            stats['linear_filled'] += gap_len
        else:
            stats['left_as_nan'] += gap_len

    return p, stats


def _apply_sd_outlier(pupil, sd_thr):
    """|x − mean| > sd_thr × SD → NaN. Одна итерация (не рекурсивно)."""
    p = pupil.copy()
    valid = ~np.isnan(p)
    if valid.sum() < 4:
        return p, 0
    mu = np.nanmean(p)
    sd = np.nanstd(p)
    if sd <= 0:
        return p, 0
    out = valid & (np.abs(p - mu) > sd_thr * sd)
    n = int(out.sum())
    p[out] = np.nan
    return p, n


def clean_pupil_array(t, pupil_raw, params=None) -> tuple[np.ndarray, dict]:
    """Полная sample-level чистка одной трассы.

    Применяется одинаково к baseline и task — это устраняет асимметрию,
    которая была в текущем pd_analysis (speed filter только на baseline).
    """
    p = params or DEFAULT_PARAMS
    qc = {'n_total': len(pupil_raw)}
    qc['n_valid_raw'] = int(np.sum(~np.isnan(pupil_raw)))

    # 1) Speed filter
    pupil, n_speed = _apply_speed_filter(pupil_raw, p['speed_thr_px_per_sample'])
    qc['n_removed_by_speed'] = n_speed

    # 2) Margin расширение NaN
    pupil = _expand_nan_margin(pupil, p['margin_samples'])
    qc['n_after_margin'] = int(np.sum(~np.isnan(pupil)))

    # 3) Интерполяция коротких гэпов
    pupil, interp_stats = _interp_short_gaps(
        t, pupil, p['max_interp_gap_samples'], method=p['interp_method']
    )
    qc.update({
        'n_interp_cubic':  interp_stats['cubic_filled'],
        'n_interp_linear': interp_stats['linear_filled'],
        'n_long_gap_nan':  interp_stats['left_as_nan'],
    })

    # 4) Финальный SD outlier-pass
    pupil, n_sd = _apply_sd_outlier(pupil, p['sd_thr'])
    qc['n_removed_by_sd'] = n_sd

    qc['n_valid_final']   = int(np.sum(~np.isnan(pupil)))
    qc['pct_valid_final'] = qc['n_valid_final'] / max(qc['n_total'], 1) * 100
    qc['pct_unrecoverable'] = 100 - qc['pct_valid_final']

    return pupil, qc


# ─────────────────────────────────────────────────────────────────────────────
# High-level: DataFrame → CleanedTrace
# ─────────────────────────────────────────────────────────────────────────────

def clean_gp3_trace(gaze_df, params=None, drop_first_sec=0.0) -> CleanedTrace:
    """Очищает один GP3 TSV-фрагмент (task или ITI).

    Args:
        gaze_df: DataFrame с колонками TIME, LPD, LPV, RPD, RPV
        params:  dict (см. DEFAULT_PARAMS) или None
        drop_first_sec: отрезать первые N секунд (для ITI обычно 0.5 с
                        стабилизации после смены экрана)

    Returns: CleanedTrace
    """
    if gaze_df is None or len(gaze_df) == 0:
        return CleanedTrace(np.array([]), np.array([]), np.array([]),
                            qc={'n_total': 0})

    need = {'TIME', 'LPD', 'LPV', 'RPD', 'RPV'}
    missing = need - set(gaze_df.columns)
    if missing:
        raise ValueError(f'Не хватает колонок: {missing}')

    df = gaze_df.copy()
    t  = pd.to_numeric(df['TIME'], errors='coerce').values

    # Отрезаем стартовую стабилизацию
    if drop_first_sec > 0 and len(t) > 1:
        mask = (t - t[0]) >= drop_first_sec
        if mask.sum() < 2:
            # Слишком короткий фрагмент после обрезки
            return CleanedTrace(t, np.full_like(t, np.nan), np.full_like(t, np.nan),
                                qc={'n_total': len(t), 'too_short': True})
        df = df.loc[mask].reset_index(drop=True)
        t  = t[mask]

    pupil_raw = build_binocular_pupil(
        df['LPD'].values, df['RPD'].values,
        df['LPV'].values, df['RPV'].values,
    )
    pupil_clean, qc = clean_pupil_array(t, pupil_raw, params=params)

    return CleanedTrace(t=t, pupil_clean=pupil_clean, pupil_raw=pupil_raw, qc=qc)


# ─────────────────────────────────────────────────────────────────────────────
# Метрики
# ─────────────────────────────────────────────────────────────────────────────

def baseline_stats(cleaned: CleanedTrace, params=None) -> dict:
    """mean / std / N валидных сэмплов baseline-периода."""
    p = params or DEFAULT_PARAMS
    out = {
        'baseline_avg_mean':       np.nan,
        'baseline_avg_std':        np.nan,
        'baseline_pct_unrecoverable': cleaned.qc.get('pct_unrecoverable', np.nan),
        'baseline_n_valid':        cleaned.n_valid_final,
    }
    if cleaned.pct_valid_final < p['min_valid_pct_for_metrics']:
        return out

    out['baseline_avg_mean'] = float(np.nanmean(cleaned.pupil_clean))
    out['baseline_avg_std']  = float(np.nanstd(cleaned.pupil_clean, ddof=1))
    return out


def task_pupil_stats(cleaned: CleanedTrace, baseline: dict, params=None) -> dict:
    """Метрики зрачка для task с тремя вариантами baseline-коррекции."""
    p = params or DEFAULT_PARAMS
    out = {
        'pupil_mean_avg':            np.nan,
        'pupil_subtractive_avg':     np.nan,   # ← primary (Mathôt 2018)
        'pupil_z_avg':               np.nan,   # ← partially-subtractive z
        'pupil_pct_change_avg':      np.nan,   # ← divisive (legacy)
        'task_pct_unrecoverable':    cleaned.qc.get('pct_unrecoverable', np.nan),
        'task_n_valid':              cleaned.n_valid_final,
    }
    if cleaned.pct_valid_final < p['min_valid_pct_for_metrics']:
        return out

    mean_task = float(np.nanmean(cleaned.pupil_clean))
    out['pupil_mean_avg'] = mean_task

    bl_mean = baseline.get('baseline_avg_mean', np.nan)
    bl_std  = baseline.get('baseline_avg_std',  np.nan)
    if np.isnan(bl_mean):
        return out

    out['pupil_subtractive_avg'] = mean_task - bl_mean
    if bl_mean != 0:
        out['pupil_pct_change_avg'] = (mean_task - bl_mean) / bl_mean * 100
    if not np.isnan(bl_std) and bl_std > 0:
        out['pupil_z_avg'] = (mean_task - bl_mean) / bl_std

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Distribution analysis (бывший inspect_pupil_speed)
# ─────────────────────────────────────────────────────────────────────────────

def collect_speed_deltas(gaze_inputs) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Собирает |ΔLPD|, |ΔRPD|, |ΔAVG| по парам валидных соседних сэмплов.

    Args:
        gaze_inputs: либо список DataFrame, либо список путей к TSV,
                     либо путь к директории (рекурсивный поиск gaze_*_pd_b*_trial.tsv).
    Returns:
        (d_lpd, d_rpd, d_avg) — каждый flat np.ndarray.
    """
    dfs = _resolve_gaze_inputs(gaze_inputs)
    d_l, d_r, d_a = [], [], []

    for df in dfs:
        if not {'LPD', 'RPD', 'LPV', 'RPV'}.issubset(df.columns):
            continue
        lpd = pd.to_numeric(df['LPD'], errors='coerce').values
        rpd = pd.to_numeric(df['RPD'], errors='coerce').values
        lpv = pd.to_numeric(df['LPV'], errors='coerce').values
        rpv = pd.to_numeric(df['RPV'], errors='coerce').values

        pair_l = (lpv[:-1] == 1) & (lpv[1:] == 1)
        dl = np.abs(np.diff(lpd))[pair_l]
        d_l.append(dl[~np.isnan(dl)])

        pair_r = (rpv[:-1] == 1) & (rpv[1:] == 1)
        dr = np.abs(np.diff(rpd))[pair_r]
        d_r.append(dr[~np.isnan(dr)])

        both = (lpv == 1) & (rpv == 1)
        avg = (lpd + rpd) / 2
        pair_a = both[:-1] & both[1:]
        da = np.abs(np.diff(avg))[pair_a]
        d_a.append(da[~np.isnan(da)])

    return (np.concatenate(d_l) if d_l else np.array([]),
            np.concatenate(d_r) if d_r else np.array([]),
            np.concatenate(d_a) if d_a else np.array([]))


def _resolve_gaze_inputs(inp) -> list[pd.DataFrame]:
    """Принимает DataFrame, путь, директорию или список — возвращает list of DF."""
    if isinstance(inp, pd.DataFrame):
        return [inp]
    if isinstance(inp, str):
        if os.path.isdir(inp):
            files = sorted(set(
                glob.glob(os.path.join(inp, '*', 'gaze_*_pd_b*_trial.tsv'))
                + glob.glob(os.path.join(inp, 'gaze_*_pd_b*_trial.tsv'))
            ))
        elif os.path.isfile(inp):
            files = [inp]
        else:
            raise FileNotFoundError(inp)
        return [pd.read_csv(f, sep='\t') for f in files]
    if isinstance(inp, (list, tuple)):
        result = []
        for x in inp:
            result.extend(_resolve_gaze_inputs(x))
        return result
    raise TypeError(f'Не могу обработать ввод типа {type(inp)}')


def summarize_speed_distribution(deltas, current_thr=None, name=''):
    """Печатает + возвращает dict с перцентилями."""
    if len(deltas) == 0:
        return {}
    pcts = [50, 75, 90, 95, 97.5, 99, 99.5, 99.9, 99.99]
    qs = np.percentile(deltas, pcts)
    out = {
        'N': int(len(deltas)),
        'mean': float(deltas.mean()),
        'median': float(np.median(deltas)),
        'max': float(deltas.max()),
        **{f'p{p}': float(q) for p, q in zip(pcts, qs)},
    }
    if current_thr is not None:
        out['pct_under_current_thr'] = float((deltas <= current_thr).sum() / len(deltas) * 100)

    if name:
        print(f'\n=== {name}  (N={out["N"]:,}) ===')
        print(f'  mean = {out["mean"]:.4f}   median = {out["median"]:.4f}')
        for p in pcts:
            marker = ''
            if current_thr is not None and abs(out[f'p{p}'] - current_thr) < 0.1:
                marker = '  ← текущий порог рядом'
            print(f'  p{p:>5.2f} = {out[f"p{p}"]:.4f}{marker}')
        if current_thr is not None:
            print(f'  Под порогом {current_thr}: {out["pct_under_current_thr"]:.3f}%')
    return out


def calibrate_threshold(d_avg, method='p99') -> float:
    """Возвращает рекомендованный порог speed filter.

    method: 'p99' | 'p99.5' | 'p99.9' | 'auto'
        'auto' — ищет долину между двумя модами в логарифмическом гистограмме;
        падает в p99, если бимодальности не видно.
    """
    if len(d_avg) == 0:
        return DEFAULT_PARAMS['speed_thr_px_per_sample']

    if method == 'p99':
        return float(np.percentile(d_avg, 99))
    if method == 'p99.5':
        return float(np.percentile(d_avg, 99.5))
    if method == 'p99.9':
        return float(np.percentile(d_avg, 99.9))

    if method == 'auto':
        # Эвристика: найти минимум плотности в области [p95, p99.9] на log-шкале.
        # Если плотность монотонно убывает — берём p99.
        lo, hi = np.percentile(d_avg, [95, 99.9])
        if hi <= lo:
            return float(np.percentile(d_avg, 99))
        bins = np.logspace(np.log10(max(lo, 1e-4)), np.log10(hi), 30)
        hist, edges = np.histogram(d_avg, bins=bins)
        # Ищем локальный минимум во внутренних бинах
        if len(hist) < 5:
            return float(np.percentile(d_avg, 99))
        inner = hist[2:-2]
        if len(inner) == 0 or inner.min() == 0:
            return float(np.percentile(d_avg, 99))
        valley_idx = np.argmin(inner) + 2
        valley_value = (edges[valley_idx] + edges[valley_idx + 1]) / 2
        # Проверка: справа от долины должно быть «плечо» (хвост артефактов).
        # Если справа плотность не растёт — это просто хвост, берём p99.
        if valley_idx + 2 < len(hist) and hist[valley_idx + 2:].sum() < 10:
            return float(np.percentile(d_avg, 99))
        return float(valley_value)

    raise ValueError(f'Неизвестный method: {method}')


def plot_speed_distribution(d_lpd, d_rpd, d_avg,
                             save_path=None, current_thr=None, recommended_thr=None):
    """Гистограммы (log-y) + CDF (log-x) для LPD, RPD, AVG."""
    fig, axes = plt.subplots(2, 3, figsize=(16, 9))
    series = [('LPD', d_lpd), ('RPD', d_rpd), ('AVG (бинокулярное)', d_avg)]

    for ax, (name, d) in zip(axes[0], series):
        if len(d) == 0:
            ax.text(0.5, 0.5, 'нет данных', ha='center', transform=ax.transAxes)
            continue
        cap = np.percentile(d, 99.95)
        d_show = d[d <= cap]
        ax.hist(d_show, bins=np.linspace(0, cap, 80),
                color='#4488cc', alpha=0.75, edgecolor='none')
        ax.set_yscale('log')
        if current_thr is not None:
            ax.axvline(current_thr, color='red', ls='--', lw=1.6,
                       label=f'текущий = {current_thr}')
        if recommended_thr is not None:
            ax.axvline(recommended_thr, color='green', ls='-', lw=1.8,
                       label=f'рекомендация = {recommended_thr:.2f}')
        for p, c in [(95, '#cc8844'), (99, '#cc4444'), (99.5, '#8844cc')]:
            v = np.percentile(d, p)
            ax.axvline(v, color=c, ls=':', lw=1.0, label=f'p{p} = {v:.2f}')
        ax.set_xlabel(f'|Δ{name}| (пкс/сэмпл)')
        ax.set_ylabel('число сэмплов (log)')
        ax.set_title(f'Гистограмма |Δ{name}|')
        ax.legend(fontsize=8, loc='upper right')
        ax.grid(alpha=0.3)

    for ax, (name, d) in zip(axes[1], series):
        if len(d) == 0:
            continue
        d_pos = np.sort(d[d > 0])
        cdf = np.arange(1, len(d_pos) + 1) / len(d_pos)
        ax.plot(d_pos, cdf, color='#2266cc', lw=1.6)
        ax.set_xscale('log')
        if current_thr is not None:
            ax.axvline(current_thr, color='red', ls='--', lw=1.6, label=f'текущий = {current_thr}')
        if recommended_thr is not None:
            ax.axvline(recommended_thr, color='green', ls='-', lw=1.8,
                       label=f'рекомендация = {recommended_thr:.2f}')
        for p, c in [(95, '#cc8844'), (99, '#cc4444'), (99.5, '#8844cc')]:
            ax.axvline(np.percentile(d, p), color=c, ls=':', lw=1.0)
        ax.set_xlabel(f'|Δ{name}| (пкс/сэмпл, log)')
        ax.set_ylabel('CDF')
        ax.set_title(f'CDF |Δ{name}|')
        ax.legend(fontsize=8, loc='lower right')
        ax.grid(alpha=0.3, which='both')

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=130, bbox_inches='tight')
    return fig


def calibrate_from_data(gaze_inputs, save_plot=None, verbose=True,
                         current_thr=None, method='auto') -> dict:
    """One-shot: собрать дельты по всем файлам + выбрать порог + (опц.) график.

    Это то, что вызывается из ноутбука перед основным препроцессингом:

        from pupil_preprocessing import calibrate_from_data
        cal = calibrate_from_data('data/', save_plot='data/pupil_speed.png',
                                   current_thr=0.5)
        params = DEFAULT_PARAMS.copy()
        params['speed_thr_px_per_sample'] = cal['recommended_thr']
    """
    if current_thr is None:
        current_thr = DEFAULT_PARAMS['speed_thr_px_per_sample']

    d_l, d_r, d_a = collect_speed_deltas(gaze_inputs)

    stats = {
        'lpd': summarize_speed_distribution(d_l, current_thr, 'LPD' if verbose else ''),
        'rpd': summarize_speed_distribution(d_r, current_thr, 'RPD' if verbose else ''),
        'avg': summarize_speed_distribution(d_a, current_thr,
                                             'AVG (бинокулярное)' if verbose else ''),
    }
    rec = calibrate_threshold(d_a, method=method)
    stats['recommended_thr'] = rec
    stats['method'] = method

    if verbose:
        print(f'\nРекомендованный порог (method={method}): {rec:.3f}')
        if current_thr is not None:
            delta = abs(rec - current_thr)
            if delta < 0.1:
                print(f'  → близок к текущему {current_thr} — можно оставить как есть')
            else:
                print(f'  → отличается от текущего {current_thr} на {delta:.2f}')

    if save_plot:
        plot_speed_distribution(d_l, d_r, d_a, save_path=save_plot,
                                 current_thr=current_thr, recommended_thr=rec)
        if verbose:
            print(f'График: {save_plot}')

    return stats
