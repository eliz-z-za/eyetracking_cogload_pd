# ===== group_analysis_pd.py =====
# Групповой статистический анализ PD-эксперимента.
# Агрегирует pd_*_metrics.xlsx всех участников, строит
# paired Wilcoxon flat vs sections (+ разбивку по task_type),
# делает FDR-коррекцию (Benjamini-Hochberg).
#
# Использование:
#   python group_analysis_pd.py [data_dir]
#
# Выходная папка: <data_dir>/group_results_pd/
#   report.xlsx               — полный отчёт (несколько листов)
#   results_main.csv          — Wilcoxon, главный эффект условия
#   results_by_task_type.csv  — Wilcoxon по типам задач
#   results_nasa_tlx.csv      — Wilcoxon NASA-TLX
#   plots/                    — визуализации

import os
import sys
import glob

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy import stats


KEY_METRICS = [
    'completion_ms',
    'clicks',
    'viewing_duration_sec',
    'fixation_count',
    'fixation_duration_mean',
    'fixation_duration_median',
    'fixation_duration_total',
    'fixation_rate_per_sec',
    'saccade_count',
    'saccade_amplitude_mean',
    'scanpath_length',
    'pupil_mean_avg',
    'pupil_pct_change_avg',
    'pupil_z_avg',
    'aoi_taskbar_pct',
    'aoi_sidebar_pct',
    'aoi_content_pct',
    'aoi_taskbar_fix_count',
    'aoi_sidebar_fix_count',
    'aoi_content_fix_count',
]

TLX_METRICS = [
    'tlx_weighted_score',
    'tlx_unweighted_mean',
    'score_mental',
    'score_temporal',
    'score_performance',
    'score_effort',
    'score_frustration',
]

CONDITION_COLORS = {'flat': '#4285F4', 'sections': '#34A853'}
TASK_TYPE_ORDER  = ['lookup', 'comparison', 'diagnosis']


def _avail(df, candidates):
    """Только те метрики из candidates, что есть в df."""
    return [m for m in candidates if m in df.columns]


# ─────────────────────── Загрузка ─────────────────────────────────

def load_all_participants(data_dir='data'):
    """Находит pd_*_metrics.xlsx, загружает и объединяет.

    Returns (per_task_df, nasa_tlx_df) или (None, None).
    """
    pattern = os.path.join(data_dir, 'pd_*_metrics.xlsx')
    files = sorted(glob.glob(pattern))
    if not files:
        print(f'[ERROR] Не найдены pd_*_metrics.xlsx в {data_dir}')
        return None, None

    per_task_list, tlx_list = [], []
    for path in files:
        fname = os.path.basename(path)
        try:
            per_task = pd.read_excel(path, sheet_name='per_task')
        except Exception as e:
            print(f'  [WARN] {fname}: per_task: {e}')
            continue
        per_task_list.append(per_task)

        try:
            tlx = pd.read_excel(path, sheet_name='nasa_tlx')
            # Добавляем condition к NASA-TLX по block_num
            bc = (per_task[['participant', 'block_num', 'condition']]
                  .drop_duplicates()
                  .dropna(subset=['condition']))
            tlx = tlx.merge(bc, on=['participant', 'block_num'], how='left')
            tlx_list.append(tlx)
        except Exception:
            pass

    if not per_task_list:
        print('[ERROR] Нет корректных файлов.')
        return None, None

    df   = pd.concat(per_task_list, ignore_index=True)
    nasa = pd.concat(tlx_list, ignore_index=True) if tlx_list else None

    print(f'[GROUP] Загружено: {len(files)} файлов, '
          f'{df["participant"].nunique()} участников, {len(df)} строк')
    if nasa is not None:
        print(f'[GROUP] NASA-TLX: {len(nasa)} строк')
    return df, nasa


# ─────────────────────── Контроль качества ────────────────────────

def check_data_quality(df):
    """Фильтрует короткие задания и задания без фиксаций.
    Требует оба условия на участника.

    Returns (df_clean, report).
    """
    report = {
        'total_rows': len(df),
        'total_participants': int(df['participant'].nunique()),
        'excluded_short_task': 0,
        'excluded_low_fixations': 0,
        'excluded_no_paired_conditions': 0,
    }
    mask = pd.Series(True, index=df.index)

    if 'completion_ms' in df.columns:
        short = pd.to_numeric(df['completion_ms'], errors='coerce') < 2000
        report['excluded_short_task'] = int(short.sum())
        mask &= ~short.fillna(False)

    if 'fixation_count' in df.columns:
        low = pd.to_numeric(df['fixation_count'], errors='coerce') < 3
        report['excluded_low_fixations'] = int(low.sum())
        mask &= ~low.fillna(False)

    df2 = df[mask].copy()
    df2['condition'] = df2['condition'].astype(str).str.lower().str.strip()

    paired = df2.groupby('participant')['condition'].apply(
        lambda x: {'flat', 'sections'}.issubset(set(x))
    )
    valid_pids = paired[paired].index
    report['excluded_no_paired_conditions'] = int(
        (~df2['participant'].isin(valid_pids)).sum()
    )
    df_clean = df2[df2['participant'].isin(valid_pids)].copy()

    report['rows_after']          = len(df_clean)
    report['participants_after']  = int(df_clean['participant'].nunique())
    return df_clean, report


# ─────────────────────── Агрегация ────────────────────────────────

def aggregate_per_condition(df, metrics=None):
    """Среднее на участника × условие → wide DataFrame.

    Index: participant. Columns: flat_<m>, sections_<m>.
    """
    if metrics is None:
        metrics = _avail(df, KEY_METRICS)
    agg  = df.groupby(['participant', 'condition'])[metrics].mean().reset_index()
    flat = agg[agg['condition'] == 'flat'].set_index('participant')[metrics]
    sect = agg[agg['condition'] == 'sections'].set_index('participant')[metrics]
    common = flat.index.intersection(sect.index)
    return (flat.loc[common].add_prefix('flat_')
            .join(sect.loc[common].add_prefix('sections_')))


def aggregate_per_condition_tasktype(df, metrics=None):
    """Среднее на участника × условие × тип задачи (long format)."""
    if metrics is None:
        metrics = _avail(df, KEY_METRICS)
    return (df.groupby(['participant', 'condition', 'task_type'])[metrics]
              .mean().reset_index())


# ─────────────────────── Дескриптивная статистика ─────────────────

def compute_descriptive_stats(df, metrics=None):
    """M ± SD, медиана, квартили по условию."""
    if metrics is None:
        metrics = _avail(df, KEY_METRICS)
    rows = []
    for m in metrics:
        for cond in ('flat', 'sections'):
            vals = df[df['condition'] == cond][m].dropna()
            rows.append({
                'metric': m, 'condition': cond,
                'N_obs': len(vals),
                'N_participants': int(df[df['condition'] == cond]['participant'].nunique()),
                'mean': vals.mean(), 'std': vals.std(), 'median': vals.median(),
                'Q1': vals.quantile(0.25) if len(vals) else np.nan,
                'Q3': vals.quantile(0.75) if len(vals) else np.nan,
                'min': vals.min() if len(vals) else np.nan,
                'max': vals.max() if len(vals) else np.nan,
            })
    return pd.DataFrame(rows)


def compute_descriptive_by_tasktype(df, metrics=None):
    """M ± SD по условию × типу задачи."""
    if metrics is None:
        metrics = _avail(df, KEY_METRICS)
    rows = []
    for m in metrics:
        for tt in TASK_TYPE_ORDER:
            for cond in ('flat', 'sections'):
                vals = df[(df['condition'] == cond) & (df['task_type'] == tt)][m].dropna()
                rows.append({
                    'metric': m, 'task_type': tt, 'condition': cond,
                    'N_obs': len(vals),
                    'mean': vals.mean(), 'std': vals.std(), 'median': vals.median(),
                })
    return pd.DataFrame(rows)


# ─────────────────────── Нормальность ─────────────────────────────

def test_normality(wide_df, metrics=None):
    """Shapiro-Wilk на разностях flat − sections (на агрегированных средних)."""
    if metrics is None:
        metrics = [m for m in KEY_METRICS
                   if f'flat_{m}' in wide_df.columns and f'sections_{m}' in wide_df.columns]
    rows = []
    for m in metrics:
        diff = (wide_df[f'flat_{m}'] - wide_df[f'sections_{m}']).dropna()
        n = len(diff)
        if n < 3:
            rows.append({'metric': m, 'N': n, 'W': np.nan, 'p': np.nan, 'normal_p05': None})
            continue
        try:
            w, p = stats.shapiro(diff)
        except Exception:
            w, p = np.nan, np.nan
        rows.append({
            'metric': m, 'N': n,
            'W': round(float(w), 4) if pd.notna(w) else np.nan,
            'p': float(p) if pd.notna(p) else np.nan,
            'normal_p05': bool(p > 0.05) if pd.notna(p) else None,
        })
    return pd.DataFrame(rows)


# ─────────────────────── Wilcoxon ─────────────────────────────────

def _wilcoxon_pair(flat_s, sect_s, metric, task_type=None):
    """Wilcoxon signed-rank для одной пары Series (индекс — participant_id).

    Возвращает dict с полями: metric, [task_type,] N,
    median_flat, median_sections, mean_flat, mean_sections,
    W, z, p, r, d.
    """
    common = flat_s.index.intersection(sect_s.index)
    a = flat_s.loc[common].dropna()
    b = sect_s.loc[common].dropna()
    shared = a.index.intersection(b.index)
    a, b = a.loc[shared], b.loc[shared]
    n = len(a)

    row = {
        'metric': metric,
        'N': n,
        'median_flat':     float(a.median()) if n else np.nan,
        'median_sections': float(b.median()) if n else np.nan,
        'mean_flat':       float(a.mean())   if n else np.nan,
        'mean_sections':   float(b.mean())   if n else np.nan,
        'W': np.nan, 'z': np.nan, 'p': np.nan, 'r': np.nan, 'd': np.nan,
    }
    if task_type is not None:
        row['task_type'] = task_type

    if n < 2:
        return row

    diff = a.values - b.values
    if np.all(diff == 0):
        row.update({'W': 0.0, 'z': 0.0, 'p': 1.0, 'r': 0.0, 'd': 0.0})
        return row

    try:
        res = stats.wilcoxon(a.values, b.values, alternative='two-sided')
        W, p = float(res.statistic), float(res.pvalue)
    except ValueError:
        W, p = 0.0, 1.0

    n_nz   = int((diff != 0).sum())
    mean_W = n_nz * (n_nz + 1) / 4
    std_W  = np.sqrt(n_nz * (n_nz + 1) * (2 * n_nz + 1) / 24)
    z      = float((W - mean_W) / std_W) if std_W > 0 else 0.0

    denom_r = n_nz * (n_nz + 1) / 2
    r = float(1 - 2 * W / denom_r) if denom_r > 0 else 0.0

    d_std = float(np.std(diff, ddof=1))
    d     = float(np.mean(diff)) / d_std if d_std > 0 else 0.0

    row.update({'W': W, 'z': z, 'p': p, 'r': r, 'd': d})
    return row


def run_wilcoxon_main(wide_df, metrics=None):
    """Paired Wilcoxon flat vs sections (уровень участника, среднее по задачам)."""
    if metrics is None:
        metrics = [m for m in KEY_METRICS
                   if f'flat_{m}' in wide_df.columns and f'sections_{m}' in wide_df.columns]
    return pd.DataFrame([
        _wilcoxon_pair(wide_df[f'flat_{m}'], wide_df[f'sections_{m}'], m)
        for m in metrics
    ])


def run_wilcoxon_by_task_type(agg_df, metrics=None):
    """Paired Wilcoxon flat vs sections отдельно для каждого типа задачи."""
    if metrics is None:
        metrics = _avail(agg_df, KEY_METRICS)
    rows = []
    for tt in TASK_TYPE_ORDER:
        sub  = agg_df[agg_df['task_type'] == tt]
        flat = sub[sub['condition'] == 'flat'].set_index('participant')
        sect = sub[sub['condition'] == 'sections'].set_index('participant')
        for m in metrics:
            if m not in flat.columns or m not in sect.columns:
                continue
            rows.append(_wilcoxon_pair(flat[m], sect[m], m, task_type=tt))
    return pd.DataFrame(rows)


def run_wilcoxon_nasa_tlx(nasa_tlx_df, clean_pids=None, metrics=None):
    """Paired Wilcoxon flat vs sections для NASA-TLX метрик."""
    if nasa_tlx_df is None or len(nasa_tlx_df) == 0:
        return pd.DataFrame()
    if 'condition' not in nasa_tlx_df.columns:
        print('  [WARN] NASA-TLX: нет колонки condition, пропускаем')
        return pd.DataFrame()
    if metrics is None:
        metrics = _avail(nasa_tlx_df, TLX_METRICS)
    if not metrics:
        return pd.DataFrame()

    df = nasa_tlx_df.copy()
    if clean_pids is not None:
        df = df[df['participant'].isin(clean_pids)]
    df['condition'] = df['condition'].astype(str).str.lower().str.strip()

    flat = df[df['condition'] == 'flat'].set_index('participant')
    sect = df[df['condition'] == 'sections'].set_index('participant')
    return pd.DataFrame([
        _wilcoxon_pair(flat[m], sect[m], m)
        for m in metrics
        if m in flat.columns and m in sect.columns
    ])


# ─────────────────────── FDR ──────────────────────────────────────

def apply_fdr(results_df, method='fdr_bh'):
    """Добавляет p_adjusted, significant, significant_adjusted."""
    from statsmodels.stats.multitest import multipletests

    p_vals = results_df['p'].values.astype(float)
    valid  = ~np.isnan(p_vals)
    p_adj  = np.full_like(p_vals, np.nan)

    if valid.sum() > 1:
        _, p_adj_v, _, _ = multipletests(p_vals[valid], method=method, alpha=0.05)
        p_adj[valid] = p_adj_v
    elif valid.sum() == 1:
        p_adj[valid] = p_vals[valid]

    out = results_df.copy()
    out['p_adjusted']          = p_adj
    out['significant']          = out['p'] < 0.05
    out['significant_adjusted'] = out['p_adjusted'] < 0.05
    return out


# ─────────────────────── Визуализации ─────────────────────────────

def plot_paired_comparison(wide_df, metric, result_row, output_path):
    """Violin + spaghetti: flat vs sections для одной метрики."""
    fc, sc = f'flat_{metric}', f'sections_{metric}'
    if fc not in wide_df.columns or sc not in wide_df.columns:
        return False
    flat = wide_df[fc].dropna()
    sect = wide_df[sc].dropna()
    common = flat.index.intersection(sect.index)
    flat, sect = flat.loc[common], sect.loc[common]
    if len(flat) < 2:
        return False

    fig, ax = plt.subplots(figsize=(5, 5))
    parts = ax.violinplot([flat.values, sect.values], positions=[1, 2],
                          showmedians=True, showextrema=False)
    for pc, color in zip(parts['bodies'],
                         [CONDITION_COLORS['flat'], CONDITION_COLORS['sections']]):
        pc.set_facecolor(color)
        pc.set_alpha(0.35)
    parts['cmedians'].set_color('black')

    for pid in common:
        ax.plot([1, 2], [flat.loc[pid], sect.loc[pid]],
                '-', color='gray', alpha=0.4, linewidth=0.8)

    rng = np.random.default_rng(42)
    jf  = 1 + rng.normal(0, 0.04, len(flat))
    js  = 2 + rng.normal(0, 0.04, len(sect))
    ax.scatter(jf, flat.values, c=CONDITION_COLORS['flat'], s=35, zorder=5,
               edgecolors='white', linewidths=0.5)
    ax.scatter(js, sect.values, c=CONDITION_COLORS['sections'], s=35, zorder=5,
               edgecolors='white', linewidths=0.5)

    if result_row:
        p_adj = result_row.get('p_adjusted', np.nan)
        if pd.notna(p_adj) and p_adj < 0.05:
            y_max = max(flat.max(), sect.max())
            y_bar = y_max * 1.08
            ax.plot([1, 1, 2, 2], [y_bar * 0.97, y_bar, y_bar, y_bar * 0.97],
                    'k-', lw=1)
            stars = '***' if p_adj < 0.001 else ('**' if p_adj < 0.01 else '*')
            ax.text(1.5, y_bar * 1.01, stars, ha='center', va='bottom', fontsize=14)

    ax.set_xticks([1, 2])
    ax.set_xticklabels(['Flat', 'Sections'])
    ax.set_ylabel(metric.replace('_', ' ').title())
    ax.set_title(f'{metric.replace("_", " ").title()}\n(N={len(flat)} participants)')
    plt.tight_layout()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    return True


def plot_effect_sizes(results_df, title, output_path):
    """Forest plot rank-biserial r (positive = flat > sections)."""
    df = results_df.dropna(subset=['r']).copy()
    if len(df) == 0:
        return False

    sig_col = 'significant_adjusted' if 'significant_adjusted' in df.columns else 'significant'
    colors = [
        '#d32f2f' if row.get(sig_col, False) else '#9e9e9e'
        for _, row in df.iterrows()
    ]
    n = len(df)
    fig, ax = plt.subplots(figsize=(9, max(3, n * 0.45)))
    y_pos = list(range(n))
    ax.barh(y_pos, df['r'].values, color=colors, height=0.5, alpha=0.7)
    ax.axvline(0, color='black', lw=0.8, ls='--')

    labels = df['metric'].values.tolist()
    if 'task_type' in df.columns:
        labels = [f'{tt}: {m}' for tt, m in zip(df['task_type'].values, labels)]
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel('Rank-biserial r  (positive = flat > sections)')
    ax.set_title(title)

    for i, row in enumerate(df.itertuples()):
        r_val = float(row.r)
        p_adj = getattr(row, 'p_adjusted', np.nan)
        lbl   = f'r={r_val:.2f}'
        if pd.notna(p_adj):
            lbl += f'  p={p_adj:.3f}'
        ha = 'left' if r_val >= 0 else 'right'
        ax.text(r_val + (0.02 if r_val >= 0 else -0.02), i,
                lbl, va='center', ha=ha, fontsize=7)

    plt.tight_layout()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    return True


def plot_condition_by_tasktype(df, metric, output_path):
    """Boxplot flat vs sections × task_type (3 панели)."""
    available = [tt for tt in TASK_TYPE_ORDER if tt in df['task_type'].values]
    if not available or metric not in df.columns:
        return False

    fig, axes = plt.subplots(1, len(available),
                              figsize=(4 * len(available), 4), sharey=True)
    if len(available) == 1:
        axes = [axes]

    for ax, tt in zip(axes, available):
        sub = df[df['task_type'] == tt]
        fv  = sub[sub['condition'] == 'flat'][metric].dropna().values
        sv  = sub[sub['condition'] == 'sections'][metric].dropna().values
        if len(fv) == 0 and len(sv) == 0:
            ax.set_visible(False)
            continue
        bp = ax.boxplot([fv, sv], positions=[1, 2], patch_artist=True, widths=0.5)
        for box, color in zip(bp['boxes'],
                               [CONDITION_COLORS['flat'], CONDITION_COLORS['sections']]):
            box.set_facecolor(color)
            box.set_alpha(0.6)
        ax.set_xticks([1, 2])
        ax.set_xticklabels(['Flat', 'Sections'], fontsize=9)
        ax.set_title(tt.capitalize(), fontsize=10)

    axes[0].set_ylabel(metric.replace('_', ' ').title())
    fig.suptitle(f'{metric.replace("_", " ").title()}: Flat vs Sections × Task Type',
                 fontsize=11)
    plt.tight_layout()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    return True


# ─────────────────────── XLSX отчёт ───────────────────────────────

def export_report(out_dir, df_clean, wide_df, agg_tt,
                  desc_main, desc_by_tt,
                  res_main, res_by_tt, res_tlx,
                  normality_df, quality_report, nasa_tlx_df=None):
    path = os.path.join(out_dir, 'report.xlsx')
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        df_clean.to_excel(writer,              sheet_name='per_task_all',          index=False)
        wide_df.reset_index().to_excel(writer, sheet_name='wide_paired',           index=False)
        agg_tt.to_excel(writer,                sheet_name='agg_by_task_type',      index=False)
        desc_main.to_excel(writer,             sheet_name='desc_main',             index=False)
        desc_by_tt.to_excel(writer,            sheet_name='desc_by_task_type',     index=False)
        res_main.to_excel(writer,              sheet_name='wilcoxon_main',         index=False)
        res_by_tt.to_excel(writer,             sheet_name='wilcoxon_by_task_type', index=False)
        if res_tlx is not None and len(res_tlx) > 0:
            res_tlx.to_excel(writer,           sheet_name='wilcoxon_nasa_tlx',     index=False)
        normality_df.to_excel(writer,          sheet_name='normality',             index=False)
        pd.DataFrame([quality_report]).to_excel(writer, sheet_name='data_quality', index=False)
        if nasa_tlx_df is not None:
            nasa_tlx_df.to_excel(writer,       sheet_name='nasa_tlx_raw',          index=False)
    print(f'[GROUP] Отчёт: {path}')


# ─────────────────────── Вспомогательные для main ─────────────────

def _print_results(df):
    cols = ['metric', 'N', 'mean_flat', 'mean_sections', 'p', 'p_adjusted', 'r', 'd']
    if 'task_type' in df.columns:
        cols = ['task_type'] + cols
    print(df[[c for c in cols if c in df.columns]].to_string(index=False))


def _run_plots(df_clean, wide_df, res_main, res_by_tt, plots_dir):
    PLOT_METRICS = [
        'completion_ms', 'fixation_count', 'fixation_duration_mean',
        'pupil_z_avg', 'scanpath_length', 'aoi_content_pct',
    ]
    for m in PLOT_METRICS:
        row_df = res_main[res_main['metric'] == m]
        row    = row_df.iloc[0].to_dict() if len(row_df) else None
        p = os.path.join(plots_dir, f'{m}_paired.png')
        if plot_paired_comparison(wide_df, m, row, p):
            print(f'[GROUP] Plot: {p}')

    p_forest = os.path.join(plots_dir, 'forest_main.png')
    if plot_effect_sizes(res_main, 'Effect Sizes: Flat vs Sections', p_forest):
        print(f'[GROUP] Forest: {p_forest}')

    if len(res_by_tt) > 0:
        p_tt = os.path.join(plots_dir, 'forest_by_task_type.png')
        if plot_effect_sizes(res_by_tt,
                              'Effect Sizes: Flat vs Sections × Task Type', p_tt):
            print(f'[GROUP] Forest: {p_tt}')

    for m in ['completion_ms', 'fixation_count', 'pupil_z_avg']:
        p = os.path.join(plots_dir, f'{m}_by_task_type.png')
        if plot_condition_by_tasktype(df_clean, m, p):
            print(f'[GROUP] Plot: {p}')


def _print_summary(res_main, res_by_tt, res_tlx):
    print('\n' + '=' * 60)
    print('  РЕЗЮМЕ')
    print('=' * 60)
    sig_col = 'significant_adjusted'

    def _sig_rows(df):
        if sig_col in df.columns:
            return df[df[sig_col] == True]
        return pd.DataFrame()

    print('\n[Главный эффект условия]')
    sig = _sig_rows(res_main)
    if len(sig):
        for _, row in sig.iterrows():
            direction = ('flat > sections'
                         if row['mean_flat'] > row['mean_sections']
                         else 'sections > flat')
            print(f'  {row["metric"]}: W={row["W"]:.1f}, '
                  f'p={row["p"]:.4f}, p_adj={row["p_adjusted"]:.4f}, '
                  f'r={row["r"]:.3f}, d={row["d"]:.3f}  [{direction}]')
    else:
        print('  Значимых различий нет (после FDR-коррекции)')

    if len(res_by_tt):
        print('\n[Эффект условия × тип задачи]')
        sig_tt = _sig_rows(res_by_tt)
        if len(sig_tt):
            for _, row in sig_tt.iterrows():
                print(f'  [{row["task_type"]}] {row["metric"]}: '
                      f'p_adj={row["p_adjusted"]:.4f}, r={row["r"]:.3f}')
        else:
            print('  Нет значимых взаимодействий (после FDR-коррекции)')

    if res_tlx is not None and len(res_tlx):
        print('\n[NASA-TLX]')
        sig_tlx = _sig_rows(res_tlx)
        if len(sig_tlx):
            for _, row in sig_tlx.iterrows():
                direction = ('flat > sections'
                             if row['mean_flat'] > row['mean_sections']
                             else 'sections > flat')
                print(f'  {row["metric"]}: '
                      f'p_adj={row["p_adjusted"]:.4f}, r={row["r"]:.3f}  [{direction}]')
        else:
            print('  Нет значимых различий в NASA-TLX (после FDR-коррекции)')


# ─────────────────────── Главный пайплайн ─────────────────────────

def run_group_analysis_pd(data_dir='data'):
    """Полный групповой анализ PD-эксперимента."""
    print('\n' + '=' * 60)
    print('  ГРУППОВОЙ АНАЛИЗ PD-ЭКСПЕРИМЕНТА')
    print('=' * 60)

    per_task_df, nasa_tlx_df = load_all_participants(data_dir)
    if per_task_df is None:
        return None

    df_clean, quality = check_data_quality(per_task_df)
    print(f'[GROUP] После фильтрации: {quality["rows_after"]} строк, '
          f'{quality["participants_after"]} участников')

    n_pid = quality['participants_after']
    if n_pid < 2:
        print('[ERROR] Нужно >= 2 участников с обоими условиями.')
        return None
    if n_pid < 5:
        print('[WARN] Менее 5 участников — низкая мощность, результаты предварительные.')

    out_dir   = os.path.join(data_dir, 'group_results_pd')
    plots_dir = os.path.join(out_dir, 'plots')
    os.makedirs(plots_dir, exist_ok=True)

    # Агрегация и нормальность
    wide_df    = aggregate_per_condition(df_clean)
    norm_df    = test_normality(wide_df)
    desc_main  = compute_descriptive_stats(df_clean)
    desc_by_tt = compute_descriptive_by_tasktype(df_clean)

    # Главный эффект условия
    print('\n--- Wilcoxon: flat vs sections (main effect) ---')
    res_main = apply_fdr(run_wilcoxon_main(wide_df))
    _print_results(res_main)

    # Эффект по типам задач
    print('\n--- Wilcoxon: flat vs sections × task_type ---')
    agg_tt    = aggregate_per_condition_tasktype(df_clean)
    res_by_tt = apply_fdr(run_wilcoxon_by_task_type(agg_tt))
    for tt in TASK_TYPE_ORDER:
        sub = res_by_tt[res_by_tt['task_type'] == tt]
        n_sig = int((sub.get('significant_adjusted', sub.get('significant', False)) == True).sum()
                    if len(sub) else 0)
        print(f'  {tt}: {len(sub)} тестов, {n_sig} значимых после FDR')

    # NASA-TLX
    res_tlx = pd.DataFrame()
    if nasa_tlx_df is not None:
        print('\n--- Wilcoxon: NASA-TLX flat vs sections ---')
        clean_pids = df_clean['participant'].unique()
        res_tlx    = run_wilcoxon_nasa_tlx(nasa_tlx_df, clean_pids=clean_pids)
        if len(res_tlx) > 0:
            res_tlx = apply_fdr(res_tlx)
            _print_results(res_tlx)

    # CSV результатов
    res_main.to_csv(os.path.join(out_dir, 'results_main.csv'), index=False)
    res_by_tt.to_csv(os.path.join(out_dir, 'results_by_task_type.csv'), index=False)
    if len(res_tlx) > 0:
        res_tlx.to_csv(os.path.join(out_dir, 'results_nasa_tlx.csv'), index=False)

    # Визуализации
    _run_plots(df_clean, wide_df, res_main, res_by_tt, plots_dir)

    # XLSX отчёт
    export_report(out_dir, df_clean, wide_df, agg_tt,
                  desc_main, desc_by_tt,
                  res_main, res_by_tt, res_tlx,
                  norm_df, quality, nasa_tlx_df=nasa_tlx_df)

    _print_summary(res_main, res_by_tt, res_tlx)
    print(f'\nФайлы: {out_dir}/')
    return res_main


if __name__ == '__main__':
    _ddir = sys.argv[1] if len(sys.argv) > 1 else 'data'
    run_group_analysis_pd(_ddir)
