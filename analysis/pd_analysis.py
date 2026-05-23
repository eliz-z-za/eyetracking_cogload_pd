# ===== pd_analysis.py =====
# Групповой анализ PD-эксперимента: сводные таблицы метрик айтрекинга,
# подсчёт баллов по опросникам (STAI, NASA-TLX), графики зрачка во времени.
#
# Запуск из корня проекта:
#   python pd_analysis.py
#
# Выходные файлы:
#   data/pd_group_summary.xlsx  — сводные таблицы
#   data/<pid>/pd_pupil_time_<pid>.png  — графики зрачка

import os
import re
import sys
import glob
import colorsys
import tempfile

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats

# ── Путь к корню проекта ─────────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
_ANALYSIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _ANALYSIS_DIR not in sys.path:
    sys.path.insert(0, _ANALYSIS_DIR)

from analysis_pd import (
    analyze_participant,
    extract_task_gaze,
)
from analysis import load_gaze_data
from pupil_preprocessing import clean_gp3_trace, DEFAULT_PARAMS

# ── Конфигурация ──────────────────────────────────────────────────────────────
DATA_ROOT = os.path.join(PROJECT_ROOT, 'data')


def _find_nasa_csv(data_root=DATA_ROOT):
    for pat in ['*nasa*tlx*', '*nasa-tlx*', '*and_nasa*', '*experiment*nasa*']:
        found = sorted(glob.glob(os.path.join(data_root, pat)))
        if found:
            return found[0]
    return os.path.join(data_root, 'experiment_and_nasa-tlx.csv')


NASA_TLX_CSV       = _find_nasa_csv()
CONDITIONS_CSV     = os.path.join(DATA_ROOT, 'experiment_conditions.csv')
QUESTIONNAIRES_CSV = os.path.join(DATA_ROOT, 'questionnaires.csv')
TASKS_CSV          = os.path.join(DATA_ROOT, 'tasks_A1-B6.csv')
OUT_XLSX           = os.path.join(DATA_ROOT, 'pd_group_summary.xlsx')

TASK_ORDER = ['A1', 'A2', 'A3', 'A4', 'A5', 'A6',
              'B1', 'B2', 'B3', 'B4', 'B5', 'B6']

KEY_METRICS = [
    'fixation_duration_mean',
    'fixation_count',
    'scanpath_length',
    'pupil_pct_change_avg',
]

ROLL_WIN = 15       # окно скользящего среднего (сэмплы) для графиков зрачка
QUALITY_THRESHOLD = 0.30   # > 30% невалидных сэмплов зрачка → excluded

_NASA_DIMS = ('mental', 'temporal', 'performance', 'effort', 'frustration')

_ET_METRICS = {
    'fixation_count':         'Число фиксаций',
    'fixation_duration_mean': 'Длит. фиксации (с)',
    'scanpath_length':        'Длина сканпути',
    'pupil_pct_change_avg':   'Δ зрачок (% baseline)',
}

_NASA_CORR_COLS = {
    'nasa_unweighted':   'TLX (невзв.)',
    'nasa_weighted':     'TLX (взвеш.)',
    'score_mental':      'Умственная',
    'score_effort':      'Усилие',
    'score_frustration': 'Фрустрация',
    'score_temporal':    'Временное давление',
    'score_performance': 'Эффективность',
}

_SPLIT_BASE_COLORS = {
    'condition': {'flat': '#2266cc', 'sections': '#cc4400'},
    'dataset':   {'alpha': '#55aa44', 'beta': '#9944cc'},
}


def _participants():
    """Все папки с 4-значными ID в DATA_ROOT."""
    return sorted(
        d for d in os.listdir(DATA_ROOT)
        if re.fullmatch(r'\d{4}', d) and os.path.isdir(os.path.join(DATA_ROOT, d))
    )


def _to_float(v):
    try:
        if isinstance(v, str):
            v = v.replace(',', '.')
        return float(v)
    except (TypeError, ValueError):
        return np.nan


# ────────────────────────────────────────────────────────────────────────────
# Метаданные задач и tasks.json
# ────────────────────────────────────────────────────────────────────────────

def load_tasks_meta(csv_path=None):
    """Dict: task_id → {'type': str} из tasks_A1-B6.csv (или hardcoded fallback)."""
    path = csv_path or TASKS_CSV
    if os.path.isfile(path):
        df = pd.read_csv(path)
        return {str(r['task']): {'type': str(r['type'])} for _, r in df.iterrows()}
    return {
        'A1': {'type': 'Lookup'},    'A2': {'type': 'Lookup'},
        'A3': {'type': 'Comparison'},'A4': {'type': 'Comparison'},
        'A5': {'type': 'Diagnosis'}, 'A6': {'type': 'Diagnosis'},
        'B1': {'type': 'Lookup'},    'B2': {'type': 'Lookup'},
        'B3': {'type': 'Comparison'},'B4': {'type': 'Comparison'},
        'B5': {'type': 'Diagnosis'}, 'B6': {'type': 'Diagnosis'},
    }


def _get_tasks_json_path():
    """Ищет tasks.json в стандартном месте; если нет — создаёт temp из CSV."""
    path = os.path.join(PROJECT_ROOT, 'static', 'data', 'tasks.json')
    if os.path.isfile(path):
        return path
    if os.path.isfile(TASKS_CSV):
        return _make_tasks_json_from_csv(TASKS_CSV)
    return None


def _make_tasks_json_from_csv(csv_path):
    """Создаёт временный tasks.json из tasks_A1-B6.csv."""
    import json as _json
    df = pd.read_csv(csv_path)
    alpha_tasks, beta_tasks = [], []
    for _, row in df.iterrows():
        tid = str(row['task'])
        obj = {
            'id':        tid,
            'type':      str(row['type']),
            'text':      str(row.get('text', '')),
            'answer':    str(row.get('answer', '')),
            'resources': [],
        }
        (alpha_tasks if tid.startswith('A') else beta_tasks).append(obj)
    data = {
        'alpha': alpha_tasks,
        'beta':  beta_tasks,
        'valid_orders': [[0, 2, 4, 1, 3, 5], [1, 3, 5, 0, 2, 4],
                         [2, 4, 0, 3, 5, 1], [4, 0, 2, 5, 1, 3]],
    }
    fd, tmp_path = tempfile.mkstemp(suffix='.json', prefix='tasks_pd_',
                                    dir=tempfile.gettempdir())
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        _json.dump(data, f, ensure_ascii=False)
    return tmp_path


# ────────────────────────────────────────────────────────────────────────────
# 1. Гейз-метрики
# ────────────────────────────────────────────────────────────────────────────

def load_all_participants():
    """Запускает analyze_participant для всех участников → единый DataFrame."""
    tasks_json    = _get_tasks_json_path()
    task_type_map = load_tasks_meta()
    rows = []
    for pid in _participants():
        pid_dir  = os.path.join(DATA_ROOT, pid)
        pid_rows = analyze_participant(pid, pid_dir, tasks_json=tasks_json)
        for row in pid_rows:
            tid = row.get('task_id', '')
            if not row.get('task_type'):
                row['task_type'] = task_type_map.get(tid, {}).get('type', '')
            if not row.get('dataset'):
                row['dataset'] = (
                    'alpha' if tid.startswith('A') else
                    'beta'  if tid.startswith('B') else ''
                )
        rows.extend(pid_rows)
    return pd.DataFrame(rows)


def summary_by_condition(df):
    """Средние ± SD ключевых метрик по условию (flat / sections)."""
    g = df.groupby('condition')[KEY_METRICS].agg(['mean', 'std'])
    g.columns = ['_'.join(c) for c in g.columns]
    return g.reset_index()


def summary_by_task(df):
    """Средние ± SD ключевых метрик по заданию (A1–A6, B1–B6)."""
    g = df.groupby('task_id')[KEY_METRICS].agg(['mean', 'std'])
    g.columns = ['_'.join(c) for c in g.columns]
    g = g.reset_index()
    order_map = {t: i for i, t in enumerate(TASK_ORDER)}
    g['_ord'] = g['task_id'].map(order_map)
    return g.sort_values('_ord').drop(columns='_ord').reset_index(drop=True)


# ────────────────────────────────────────────────────────────────────────────
# 2. Условия и датасеты (experiment_conditions.csv)
# ────────────────────────────────────────────────────────────────────────────

def load_conditions(csv_path=None):
    """Long-format: id, block(1/2), condition('flat'/'sections'), dataset('alpha'/'beta')."""
    path = csv_path or CONDITIONS_CSV
    df = pd.read_csv(path)
    rows = []
    for _, r in df.iterrows():
        pid = str(r['id'])
        for bn in (1, 2):
            rows.append({
                'id':        pid,
                'block':     bn,
                'condition': str(r.get(f'disclosure_{bn}', '')).strip().lower(),
                'dataset':   str(r.get(f'dataset_{bn}',    '')).strip().lower(),
            })
    return pd.DataFrame(rows)


# ────────────────────────────────────────────────────────────────────────────
# 3. NASA-TLX (готовые суммы из CSV + условие/датасет из conditions)
# ────────────────────────────────────────────────────────────────────────────

def score_nasa_tlx(nasa_csv=None, conditions_csv=None):
    """Читает готовые NASA-TLX суммы; добавляет condition/dataset из conditions CSV.

    Возвращает long-format: id, block, condition, dataset,
    nasa_unweighted, nasa_weighted, score_mental, ..., weight_mental, ...
    """
    nasa_path = nasa_csv or NASA_TLX_CSV
    cond_path = conditions_csv or CONDITIONS_CSV

    if not os.path.isfile(nasa_path):
        raise FileNotFoundError(f'NASA-TLX CSV не найден: {nasa_path}')

    nasa = pd.read_csv(nasa_path)
    cond = load_conditions(cond_path)
    cond['id']    = cond['id'].astype(str)
    cond['block'] = cond['block'].astype(int)

    rows = []
    for _, r in nasa.iterrows():
        pid = str(r['id'])
        for block in (1, 2):
            pfx = f'nasa{block}_'
            row = {
                'id':              pid,
                'block':           block,
                'nasa_unweighted': _to_float(r.get(f'{pfx}sum')),
                'nasa_weighted':   _to_float(r.get(f'{pfx}weighted_sum')),
            }
            for d in _NASA_DIMS:
                row[f'score_{d}']  = _to_float(r.get(f'{pfx}{d}_score'))
                row[f'weight_{d}'] = _to_float(r.get(f'{pfx}{d}_weight'))
            rows.append(row)

    df = pd.DataFrame(rows)
    df['id']    = df['id'].astype(str)
    df['block'] = df['block'].astype(int)
    return df.merge(cond, on=['id', 'block'], how='left')


# ────────────────────────────────────────────────────────────────────────────
# 4. STAI (готовый STAI_sum из questionnaires.csv)
# ────────────────────────────────────────────────────────────────────────────

def score_stai(csv_path=None):
    """Читает STAI_sum напрямую из questionnaires.csv."""
    path = csv_path or QUESTIONNAIRES_CSV
    df = pd.read_csv(path)
    rows = []
    for _, r in df.iterrows():
        score = _to_float(r.get('STAI_sum'))
        if not np.isnan(score):
            level = ('очень низкая' if score < 31 else
                     'умеренная'    if score <= 45 else 'высокая')
        else:
            level = np.nan
        rows.append({'id': str(r['id']), 'stai_score': score, 'stai_level': level})
    return pd.DataFrame(rows)


# ────────────────────────────────────────────────────────────────────────────
# 5. Демографические статистики
# ────────────────────────────────────────────────────────────────────────────

def demographic_stats(csv_path=None):
    """Вычисляет демографические статистики из questionnaires.csv."""
    from datetime import date, datetime
    path = csv_path or QUESTIONNAIRES_CSV
    df = pd.read_csv(path)

    ref_date = date(2026, 4, 25)

    def _parse_age(bd_str):
        clean = str(bd_str).strip().replace('г.', '').replace('г', '').strip()
        for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y'):
            try:
                return (ref_date - datetime.strptime(clean, fmt).date()).days // 365
            except ValueError:
                pass
        return np.nan

    ages = df['birth_date'].apply(_parse_age)

    def _num(col):
        vals = pd.to_numeric(df.get(col, pd.Series(dtype=float)), errors='coerce').dropna()
        if len(vals) == 0:
            return {'mean': np.nan, 'sd': np.nan, 'min': np.nan, 'max': np.nan}
        return {
            'mean':      round(float(vals.mean()), 1),
            'sd':        round(float(vals.std()),  1),
            'min':       int(vals.min()),
            'max':       int(vals.max()),
            'n_missing': int(pd.to_numeric(df.get(col, pd.Series(dtype=float)),
                                           errors='coerce').isna().sum()),
        }

    return {
        'n': len(df),
        'age': {
            'mean':      round(float(ages.mean()), 1) if ages.notna().any() else np.nan,
            'sd':        round(float(ages.std()),  1) if ages.notna().any() else np.nan,
            'min':       int(ages.min()) if ages.notna().any() else np.nan,
            'max':       int(ages.max()) if ages.notna().any() else np.nan,
            'n_missing': int(ages.isna().sum()),
            'values': {str(r['id']): v
                       for _, r in df.iterrows()
                       for v in [_parse_age(r['birth_date'])]},
        },
        'sex':         df['sex'].value_counts().to_dict(),
        'profession':  df['prof'].astype(str).str.strip().value_counts().to_dict(),
        'it_exp':      _num('it_exp_years'),
        'infra_exp':   _num('infra_exp_years'),
        'infra_level': df['infra_level'].value_counts().to_dict(),
        'in_lenses':   dict(zip(df['id'].astype(str), df.get('in_lenses', pd.Series()))),
        'sleep_quality': _num('sleep_quality'),
    }


# ────────────────────────────────────────────────────────────────────────────
# 6. Качество данных айтрекинга
# ────────────────────────────────────────────────────────────────────────────

def compute_data_quality_all(all_df=None, threshold=None):
    """Per-participant ET data quality: % невалидных сэмплов.

    Returns DataFrame: participant, n_samples_total,
        n_invalid_pupil, pct_invalid_pupil,
        n_invalid_gaze,  pct_invalid_gaze,
        excluded_pupil (bool)
    """
    if threshold is None:
        threshold = QUALITY_THRESHOLD

    rows = []
    for pid in _participants():
        pid_dir  = os.path.join(DATA_ROOT, pid)
        total    = 0
        inv_pup  = 0
        inv_gaze = 0

        if all_df is not None and len(all_df) > 0:
            pid_task_df = all_df[all_df['participant'] == pid][
                ['block_num', 'task_id']
            ].drop_duplicates()
        else:
            pid_task_df = None

        for bn in (1, 2):
            gaze_path = os.path.join(pid_dir, f'gaze_{pid}_pd_b{bn}_trial.tsv')
            if not os.path.isfile(gaze_path):
                continue
            try:
                bdf = load_gaze_data(gaze_path)
            except Exception:
                continue

            task_ids = (
                pid_task_df[pid_task_df['block_num'] == bn]['task_id'].tolist()
                if pid_task_df is not None
                else TASK_ORDER
            )

            for tid in task_ids:
                tdf = extract_task_gaze(bdf, tid)
                if len(tdf) == 0:
                    continue
                total    += len(tdf)
                inv_pup  += int(
                    ((tdf.get('LPV', pd.Series(0, index=tdf.index)) == 0) &
                     (tdf.get('RPV', pd.Series(0, index=tdf.index)) == 0)).sum()
                )
                inv_gaze += int(
                    (tdf.get('FPOGV', pd.Series(0, index=tdf.index)) == 0).sum()
                )

        pct_pup  = inv_pup  / total * 100 if total > 0 else np.nan
        pct_gaze = inv_gaze / total * 100 if total > 0 else np.nan
        rows.append({
            'participant':       pid,
            'n_samples_total':   total,
            'n_invalid_pupil':   inv_pup,
            'pct_invalid_pupil': round(pct_pup,  1) if not np.isnan(pct_pup)  else np.nan,
            'n_invalid_gaze':    inv_gaze,
            'pct_invalid_gaze':  round(pct_gaze, 1) if not np.isnan(pct_gaze) else np.nan,
            'excluded_pupil':    bool(pct_pup > threshold * 100) if not np.isnan(pct_pup) else False,
        })
    return pd.DataFrame(rows)


# ────────────────────────────────────────────────────────────────────────────
# 6.5 Парные тесты Уилкоксона flat vs sections + FDR + эффект-сайзы
# ────────────────────────────────────────────────────────────────────────────

_TEST_METRICS = [
    'fixation_count',
    'fixation_duration_mean',
    'fixation_duration_median',
    'scanpath_length',
    'pupil_mean_avg',
    'pupil_pct_change_avg',
    'pupil_z_avg',
]

# Разделение метрик по иерархии датафреймов:
# зрачковые (primary=df_good) vs не-зрачковые (primary=df_all)
PUPIL_METRICS     = ['pupil_pct_change_avg', 'pupil_z_avg']
NON_PUPIL_METRICS = ['fixation_count', 'fixation_duration_mean',
                     'fixation_duration_median', 'scanpath_length']

EASY_TASK_SUFFIXES = {'1', '2', '3', '4'}  # A1-A4, B1-B4 (задачи 5-6 слишком различаются между датасетами)


def filter_easy_tasks(df):
    """Возвращает строки где task_id оканчивается на 1-4 (исключает задачи 5, 6)."""
    return df[df['task_id'].str[-1].isin(EASY_TASK_SUFFIXES)].copy()


# ── Bootstrap CI и permutation test ─────────────────────────────────────────

def _rank_biserial(diffs):
    """Rank-biserial r из массива разностей (flat - sections)."""
    n = len(diffs)
    if n == 0:
        return np.nan
    try:
        W, _ = stats.wilcoxon(diffs, alternative='two-sided')
    except ValueError:
        return np.nan
    return float(1 - (2 * W) / (n * (n + 1) / 2))


def _cohens_d(diffs):
    """Cohen's d на попарных разностях."""
    s = diffs.std(ddof=1)
    return float(diffs.mean() / s) if s > 0 else np.nan


def bootstrap_effect_size_ci(diffs, n_boot=10000, seed=42):
    """Bootstrap 95% CI для rank-biserial r и Cohen's d.

    Parameters
    ----------
    diffs : array-like  — попарные разности (flat - sections)
    n_boot : int        — число итераций (default 10 000)
    seed : int

    Returns dict: r_lo, r_hi, d_lo, d_hi
    """
    diffs = np.asarray(diffs, dtype=float)
    n = len(diffs)
    rng = np.random.default_rng(seed)
    rs, ds = [], []
    for _ in range(n_boot):
        sample = rng.choice(diffs, size=n, replace=True)
        rs.append(_rank_biserial(sample))
        ds.append(_cohens_d(sample))
    rs = np.array([v for v in rs if not np.isnan(v)])
    ds = np.array([v for v in ds if not np.isnan(v)])
    return {
        'r_lo': float(np.percentile(rs, 2.5))  if len(rs) > 0 else np.nan,
        'r_hi': float(np.percentile(rs, 97.5)) if len(rs) > 0 else np.nan,
        'd_lo': float(np.percentile(ds, 2.5))  if len(ds) > 0 else np.nan,
        'd_hi': float(np.percentile(ds, 97.5)) if len(ds) > 0 else np.nan,
    }


def permutation_paired_test(x, y, n_perm=10000, seed=42, alternative='two-sided'):
    """Exact permutation p-value: переброска знаков разностей n_perm раз.

    Критично при N=8, где Wilcoxon на границе применимости таблиц.
    alternative: 'two-sided', 'less' (x < y), 'greater' (x > y).

    Returns p_perm (float).
    """
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    diffs = x - y
    diffs = diffs[~np.isnan(diffs)]
    n = len(diffs)
    if n == 0:
        return np.nan

    obs = np.sum(diffs)
    rng = np.random.default_rng(seed)
    count = 0
    for _ in range(n_perm):
        signs = rng.choice([-1.0, 1.0], size=n)
        perm_stat = np.sum(diffs * signs)
        if alternative == 'two-sided':
            if abs(perm_stat) >= abs(obs):
                count += 1
        elif alternative == 'less':
            if perm_stat <= obs:
                count += 1
        else:  # 'greater'
            if perm_stat >= obs:
                count += 1
    return count / n_perm


def wilcoxon_condition_test(df, metrics=None, alternative='two-sided',
                            n_boot=10000, n_perm=10000, seed=42):
    """Парный тест Уилкоксона flat vs sections для ET-метрик.

    Единица анализа: среднее метрики по участнику × условие (агрегация по заданиям).
    Применяет FDR-поправку Бенджамини–Хохберга.

    Parameters
    ----------
    alternative : str  — scipy-семантика: 'two-sided' (H2),
                         'greater' (H_1: flat > sections, т.е. sections < flat — для H1, H3, H4),
                         'less'    (H_1: flat < sections, т.е. sections > flat)
    n_boot      : int  — bootstrap CI iterations (0 = skip)
    n_perm      : int  — permutation test iterations (0 = skip)

    Returns DataFrame: metric, M_flat, M_sections, N_pairs, W, p_raw, p_perm,
                       rank_biserial_r, cohens_d, r_CI95_lo, r_CI95_hi,
                       d_CI95_lo, d_CI95_hi, p_fdr, significant_fdr
    """
    if metrics is None:
        metrics = [m for m in _TEST_METRICS if m in df.columns]

    agg = (df.groupby(['participant', 'condition'])[metrics]
             .mean()
             .reset_index())

    rows = []
    for metric in metrics:
        flat_s = agg[agg['condition'] == 'flat'].set_index('participant')[metric]
        sect_s = agg[agg['condition'] == 'sections'].set_index('participant')[metric]
        common = flat_s.index.intersection(sect_s.index)
        if len(common) < 4:
            continue

        fv   = flat_s.loc[common].values
        sv   = sect_s.loc[common].values
        diff = fv - sv

        try:
            W, p = stats.wilcoxon(fv, sv, alternative=alternative)
        except ValueError:
            W, p = np.nan, np.nan

        n    = len(fv)
        r_rb = float(1 - (2 * W) / (n * (n + 1) / 2)) if not np.isnan(W) else np.nan
        d    = _cohens_d(diff)

        # Bootstrap CI
        ci = bootstrap_effect_size_ci(diff, n_boot=n_boot, seed=seed) if n_boot > 0 else {
            'r_lo': np.nan, 'r_hi': np.nan, 'd_lo': np.nan, 'd_hi': np.nan,
        }
        # Permutation test
        p_perm = permutation_paired_test(fv, sv, n_perm=n_perm, seed=seed,
                                         alternative=alternative) if n_perm > 0 else np.nan

        rows.append({
            'metric':          metric,
            'M_flat':          round(float(fv.mean()), 4),
            'M_sections':      round(float(sv.mean()), 4),
            'N_pairs':         n,
            'W':               W,
            'p_raw':           round(float(p), 5)    if not np.isnan(p)    else np.nan,
            'p_perm':          round(float(p_perm), 5) if not np.isnan(p_perm) else np.nan,
            'rank_biserial_r': round(r_rb, 3)        if not np.isnan(r_rb) else np.nan,
            'cohens_d':        round(d,    3)        if not np.isnan(d)    else np.nan,
            'r_CI95_lo':       round(ci['r_lo'], 3)  if not np.isnan(ci['r_lo']) else np.nan,
            'r_CI95_hi':       round(ci['r_hi'], 3)  if not np.isnan(ci['r_hi']) else np.nan,
            'd_CI95_lo':       round(ci['d_lo'], 3)  if not np.isnan(ci['d_lo']) else np.nan,
            'd_CI95_hi':       round(ci['d_hi'], 3)  if not np.isnan(ci['d_hi']) else np.nan,
        })

    result = pd.DataFrame(rows)
    if result.empty:
        return result

    # FDR Benjamini-Hochberg
    ps = result['p_raw'].values.astype(float)
    n_tests = len(ps)
    order = np.argsort(ps)
    p_adj = np.empty(n_tests)
    for k, idx in enumerate(order):
        p_adj[idx] = min(1.0, ps[idx] * n_tests / (k + 1))
    for k in range(n_tests - 2, -1, -1):
        p_adj[order[k]] = min(p_adj[order[k]], p_adj[order[k + 1]])

    result['p_fdr']           = np.round(p_adj, 5)
    result['significant_fdr'] = result['p_fdr'] < 0.05
    return result


def lmm_condition_test(df, metrics=None):
    """LMM: metric ~ C(condition) + C(dataset) + block_num + (1|participant).

    Возвращает DataFrame: metric, beta_sections, SE, z, p_raw, p_fdr, N_obs, converged.
    beta_sections > 0 → sections > flat.
    Использует REML=True (рекомендуется для repeated measures).

    ⚠ При N=8 участников LMM нестабильна (singular fit, дисперсия RE ≈ 0).
    Результаты exploratory; используйте gee_condition_test() как основной тест.
    """
    import statsmodels.formula.api as smf
    if metrics is None:
        metrics = [m for m in _TEST_METRICS if m in df.columns]

    rows = []
    for metric in metrics:
        sub = df.dropna(subset=[metric]).copy()
        sub['block_num'] = sub['block_num'].astype(float)
        try:
            import warnings
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always')
                md = smf.mixedlm(
                    f'{metric} ~ C(condition) + C(dataset) + block_num',
                    data=sub,
                    groups=sub['participant'],
                )
                res = md.fit(reml=True, method='lbfgs', disp=False)
            # Convergence: проверяем флаг и наличие ConvergenceWarning
            converged = bool(getattr(res, 'converged', True))
            if any('ConvergenceWarning' in str(w.category) or
                   'singular' in str(w.message).lower() for w in caught):
                converged = False
            coef_key = 'C(condition)[T.sections]'
            beta = res.params.get(coef_key, np.nan)
            se   = res.bse.get(coef_key, np.nan)
            z    = res.tvalues.get(coef_key, np.nan)
            p    = res.pvalues.get(coef_key, np.nan)
            rows.append({'metric': metric,
                         'beta_sections': round(float(beta), 4),
                         'SE': round(float(se), 4),
                         'z': round(float(z), 3),
                         'p_raw': round(float(p), 5),
                         'N_obs': len(sub),
                         'N_participants': sub['participant'].nunique(),
                         'converged': converged})
        except Exception as e:
            rows.append({'metric': metric, 'error': str(e), 'converged': False})

    result = pd.DataFrame(rows)
    if 'p_raw' in result.columns:
        ps = result['p_raw'].dropna().values
        order = np.argsort(ps)
        p_adj = np.empty(len(ps))
        n = len(ps)
        for k, idx in enumerate(order):
            p_adj[idx] = min(1.0, ps[idx] * n / (k + 1))
        for k in range(n - 2, -1, -1):
            p_adj[order[k]] = min(p_adj[order[k]], p_adj[order[k + 1]])
        result.loc[result['p_raw'].notna(), 'p_fdr'] = np.round(p_adj, 5)
        result['significant_fdr'] = result.get('p_fdr', np.nan) < 0.05
    return result


def gee_condition_test(df, metrics=None):
    """GEE: metric ~ C(condition) + C(dataset) + block_num, groups=participant.

    Использует exchangeable working correlation — устойчивее LMM при малом N кластеров.
    Возвращает DataFrame аналогичной структуры: metric, beta_sections, SE, z, p_raw, p_fdr.
    """
    from statsmodels.genmod.generalized_estimating_equations import GEE
    from statsmodels.genmod.cov_struct import Exchangeable
    from statsmodels.genmod.families import Gaussian
    if metrics is None:
        metrics = [m for m in _TEST_METRICS if m in df.columns]

    rows = []
    for metric in metrics:
        sub = df.dropna(subset=[metric]).copy()
        sub = sub.sort_values(['participant', 'block_num'])
        sub['block_num'] = sub['block_num'].astype(float)
        try:
            model = GEE.from_formula(
                f'{metric} ~ C(condition) + C(dataset) + block_num',
                groups='participant',
                data=sub,
                family=Gaussian(),
                cov_struct=Exchangeable(),
            )
            res = model.fit()
            coef_key = 'C(condition)[T.sections]'
            beta = res.params.get(coef_key, np.nan)
            se   = res.bse.get(coef_key, np.nan)
            z    = res.tvalues.get(coef_key, np.nan)
            p    = res.pvalues.get(coef_key, np.nan)
            rows.append({'metric': metric,
                         'beta_sections': round(float(beta), 4),
                         'SE': round(float(se), 4),
                         'z': round(float(z), 3),
                         'p_raw': round(float(p), 5),
                         'N_obs': len(sub),
                         'N_participants': sub['participant'].nunique()})
        except Exception as e:
            rows.append({'metric': metric, 'error': str(e)})

    result = pd.DataFrame(rows)
    if 'p_raw' in result.columns:
        ps = result['p_raw'].dropna().values
        n = len(ps)
        if n > 0:
            order = np.argsort(ps)
            p_adj = np.empty(n)
            for k, idx in enumerate(order):
                p_adj[idx] = min(1.0, ps[idx] * n / (k + 1))
            for k in range(n - 2, -1, -1):
                p_adj[order[k]] = min(p_adj[order[k]], p_adj[order[k + 1]])
            result.loc[result['p_raw'].notna(), 'p_fdr'] = np.round(p_adj, 5)
            result['significant_fdr'] = result.get('p_fdr', np.nan) < 0.05
    return result


def lmm_interaction_test(df, metrics=None):
    """LMM: metric ~ C(condition) * C(task_type) + block_num + (1|participant).

    Тестирует взаимодействие условия и типа задачи.
    Особый интерес: C(condition)[T.sections]:C(task_type)[T.Diagnosis].

    Returns DataFrame: metric, interaction_term, beta, SE, z, p_raw, p_fdr.
    """
    import statsmodels.formula.api as smf
    if metrics is None:
        metrics = [m for m in _TEST_METRICS if m in df.columns]
    if 'task_type' not in df.columns:
        raise ValueError("Колонка 'task_type' отсутствует в df.")

    rows = []
    for metric in metrics:
        sub = df.dropna(subset=[metric, 'task_type']).copy()
        sub['block_num'] = sub['block_num'].astype(float)
        try:
            import warnings
            with warnings.catch_warnings(record=True):
                warnings.simplefilter('always')
                md = smf.mixedlm(
                    f'{metric} ~ C(condition) * C(task_type) + block_num',
                    data=sub,
                    groups=sub['participant'],
                )
                res = md.fit(reml=True, method='lbfgs', disp=False)
            # Ищем все interaction-коэффициенты
            for key in res.params.index:
                if ':' in key:
                    rows.append({
                        'metric':           metric,
                        'interaction_term': key,
                        'beta':             round(float(res.params[key]), 4),
                        'SE':               round(float(res.bse[key]), 4),
                        'z':                round(float(res.tvalues[key]), 3),
                        'p_raw':            round(float(res.pvalues[key]), 5),
                        'N_obs':            len(sub),
                    })
        except Exception as e:
            rows.append({'metric': metric, 'interaction_term': 'error', 'error': str(e)})

    result = pd.DataFrame(rows)
    if 'p_raw' in result.columns and result['p_raw'].notna().any():
        ps = result['p_raw'].dropna().values
        n = len(ps)
        order = np.argsort(ps)
        p_adj = np.empty(n)
        for k, idx in enumerate(order):
            p_adj[idx] = min(1.0, ps[idx] * n / (k + 1))
        for k in range(n - 2, -1, -1):
            p_adj[order[k]] = min(p_adj[order[k]], p_adj[order[k + 1]])
        result.loc[result['p_raw'].notna(), 'p_fdr'] = np.round(p_adj, 5)
    return result


# ────────────────────────────────────────────────────────────────────────────
# 6.6 H3: NASA-TLX flat vs sections (парный Wilcoxon)
# ────────────────────────────────────────────────────────────────────────────

def wilcoxon_nasa_condition_test(nasa_df, metrics=None, n_boot=10000, n_perm=10000, seed=42):
    """H3: парный Wilcoxon flat vs sections для NASA-TLX (направленный, sections < flat).

    Единица анализа: среднее по блоку на участника × условие.
    FDR Benjamini-Hochberg по проверяемым метрикам.
    alternative='greater': H_1: flat > sections (т.е. sections < flat).

    Returns DataFrame той же структуры, что wilcoxon_condition_test().
    """
    if metrics is None:
        metrics = ['nasa_unweighted', 'nasa_weighted']
    metrics = [m for m in metrics if m in nasa_df.columns]

    agg = (nasa_df.groupby(['id', 'condition'])[metrics]
                   .mean()
                   .reset_index()
                   .rename(columns={'id': 'participant'}))

    rows = []
    for metric in metrics:
        flat_s = agg[agg['condition'] == 'flat'].set_index('participant')[metric]
        sect_s = agg[agg['condition'] == 'sections'].set_index('participant')[metric]
        common = flat_s.index.intersection(sect_s.index)
        if len(common) < 4:
            continue

        fv   = flat_s.loc[common].values
        sv   = sect_s.loc[common].values
        diff = fv - sv

        try:
            W, p = stats.wilcoxon(fv, sv, alternative='greater')
        except ValueError:
            W, p = np.nan, np.nan

        n    = len(fv)
        r_rb = float(1 - (2 * W) / (n * (n + 1) / 2)) if not np.isnan(W) else np.nan
        d    = _cohens_d(diff)
        ci   = bootstrap_effect_size_ci(diff, n_boot=n_boot, seed=seed) if n_boot > 0 else {
            'r_lo': np.nan, 'r_hi': np.nan, 'd_lo': np.nan, 'd_hi': np.nan,
        }
        p_perm = permutation_paired_test(fv, sv, n_perm=n_perm, seed=seed,
                                         alternative='greater') if n_perm > 0 else np.nan
        rows.append({
            'metric':          metric,
            'M_flat':          round(float(fv.mean()), 4),
            'M_sections':      round(float(sv.mean()), 4),
            'N_pairs':         n,
            'W':               W,
            'p_raw':           round(float(p), 5)      if not np.isnan(p)    else np.nan,
            'p_perm':          round(float(p_perm), 5) if not np.isnan(p_perm) else np.nan,
            'rank_biserial_r': round(r_rb, 3)          if not np.isnan(r_rb) else np.nan,
            'cohens_d':        round(d, 3)              if not np.isnan(d)    else np.nan,
            'r_CI95_lo':       round(ci['r_lo'], 3)    if not np.isnan(ci['r_lo']) else np.nan,
            'r_CI95_hi':       round(ci['r_hi'], 3)    if not np.isnan(ci['r_hi']) else np.nan,
            'd_CI95_lo':       round(ci['d_lo'], 3)    if not np.isnan(ci['d_lo']) else np.nan,
            'd_CI95_hi':       round(ci['d_hi'], 3)    if not np.isnan(ci['d_hi']) else np.nan,
        })

    result = pd.DataFrame(rows)
    if result.empty:
        return result

    ps = result['p_raw'].values.astype(float)
    n_tests = len(ps)
    order = np.argsort(ps)
    p_adj = np.empty(n_tests)
    for k, idx in enumerate(order):
        p_adj[idx] = min(1.0, ps[idx] * n_tests / (k + 1))
    for k in range(n_tests - 2, -1, -1):
        p_adj[order[k]] = min(p_adj[order[k]], p_adj[order[k + 1]])
    result['p_fdr']           = np.round(p_adj, 5)
    result['significant_fdr'] = result['p_fdr'] < 0.05
    return result


# ────────────────────────────────────────────────────────────────────────────
# 6.7 H4: Время выполнения flat vs sections (медианный Wilcoxon)
# ────────────────────────────────────────────────────────────────────────────

def wilcoxon_completion_test(df, n_boot=10000, n_perm=10000, seed=42):
    """H4: парный Wilcoxon на медианном completion_ms (направленный, sections < flat).

    Гипотеза: медианное время выполнения задачи короче в условии sections.
    Единица анализа: медиана completion_ms по участнику × условие.
    alternative='greater': H_1: flat > sections (т.е. sections < flat).

    Returns DataFrame той же структуры, что wilcoxon_condition_test().
    """
    if 'completion_ms' not in df.columns:
        raise ValueError("Колонка 'completion_ms' отсутствует в df.")

    agg = (df.groupby(['participant', 'condition'])['completion_ms']
             .median()
             .reset_index())

    flat_s = agg[agg['condition'] == 'flat'].set_index('participant')['completion_ms']
    sect_s = agg[agg['condition'] == 'sections'].set_index('participant')['completion_ms']
    common = flat_s.index.intersection(sect_s.index)
    if len(common) < 4:
        return pd.DataFrame()

    fv   = flat_s.loc[common].values
    sv   = sect_s.loc[common].values
    diff = fv - sv

    try:
        W, p = stats.wilcoxon(fv, sv, alternative='greater')
    except ValueError:
        W, p = np.nan, np.nan

    n    = len(fv)
    r_rb = float(1 - (2 * W) / (n * (n + 1) / 2)) if not np.isnan(W) else np.nan
    d    = _cohens_d(diff)
    ci   = bootstrap_effect_size_ci(diff, n_boot=n_boot, seed=seed) if n_boot > 0 else {
        'r_lo': np.nan, 'r_hi': np.nan, 'd_lo': np.nan, 'd_hi': np.nan,
    }
    p_perm = permutation_paired_test(fv, sv, n_perm=n_perm, seed=seed,
                                     alternative='greater') if n_perm > 0 else np.nan

    return pd.DataFrame([{
        'metric':          'completion_ms_median',
        'M_flat':          round(float(fv.mean()), 1),
        'M_sections':      round(float(sv.mean()), 1),
        'N_pairs':         n,
        'W':               W,
        'p_raw':           round(float(p), 5)      if not np.isnan(p)    else np.nan,
        'p_perm':          round(float(p_perm), 5) if not np.isnan(p_perm) else np.nan,
        'rank_biserial_r': round(r_rb, 3)          if not np.isnan(r_rb) else np.nan,
        'cohens_d':        round(d, 3)              if not np.isnan(d)    else np.nan,
        'r_CI95_lo':       round(ci['r_lo'], 3)    if not np.isnan(ci['r_lo']) else np.nan,
        'r_CI95_hi':       round(ci['r_hi'], 3)    if not np.isnan(ci['r_hi']) else np.nan,
        'd_CI95_lo':       round(ci['d_lo'], 3)    if not np.isnan(ci['d_lo']) else np.nan,
        'd_CI95_hi':       round(ci['d_hi'], 3)    if not np.isnan(ci['d_hi']) else np.nan,
        'p_fdr':           np.nan,
        'significant_fdr': False,
    }])


# ────────────────────────────────────────────────────────────────────────────
# 6.8 Контрбалансировка ET-метрик: блок 1 vs блок 2
# ────────────────────────────────────────────────────────────────────────────

def wilcoxon_block_order_et(df, metrics=None, n_boot=0, n_perm=0, seed=42):
    """Парный Wilcoxon блок 1 vs блок 2 для ET-метрик.

    Тестирует порядковый эффект — дополнение к аналогичному тесту для NASA-TLX.
    Единица анализа: среднее метрики по участнику × блоку.

    Returns DataFrame с той же структурой, что wilcoxon_condition_test().
    """
    if metrics is None:
        metrics = [m for m in _TEST_METRICS if m in df.columns]

    agg = (df.groupby(['participant', 'block_num'])[metrics]
             .mean()
             .reset_index())

    rows = []
    for metric in metrics:
        b1_s = agg[agg['block_num'] == 1].set_index('participant')[metric]
        b2_s = agg[agg['block_num'] == 2].set_index('participant')[metric]
        common = b1_s.index.intersection(b2_s.index)
        if len(common) < 4:
            continue

        b1v  = b1_s.loc[common].values
        b2v  = b2_s.loc[common].values
        diff = b1v - b2v

        try:
            W, p = stats.wilcoxon(b1v, b2v, alternative='two-sided')
        except ValueError:
            W, p = np.nan, np.nan

        n    = len(b1v)
        r_rb = float(1 - (2 * W) / (n * (n + 1) / 2)) if not np.isnan(W) else np.nan
        d    = _cohens_d(diff)
        ci   = bootstrap_effect_size_ci(diff, n_boot=n_boot, seed=seed) if n_boot > 0 else {
            'r_lo': np.nan, 'r_hi': np.nan, 'd_lo': np.nan, 'd_hi': np.nan,
        }
        rows.append({
            'metric':          metric,
            'M_block1':        round(float(b1v.mean()), 4),
            'M_block2':        round(float(b2v.mean()), 4),
            'N_pairs':         n,
            'W':               W,
            'p_raw':           round(float(p), 5) if not np.isnan(p) else np.nan,
            'rank_biserial_r': round(r_rb, 3)     if not np.isnan(r_rb) else np.nan,
            'cohens_d':        round(d, 3)         if not np.isnan(d)    else np.nan,
            'r_CI95_lo':       round(ci['r_lo'], 3) if not np.isnan(ci['r_lo']) else np.nan,
            'r_CI95_hi':       round(ci['r_hi'], 3) if not np.isnan(ci['r_hi']) else np.nan,
        })

    result = pd.DataFrame(rows)
    if result.empty:
        return result

    ps = result['p_raw'].values.astype(float)
    n_tests = len(ps)
    order = np.argsort(ps)
    p_adj = np.empty(n_tests)
    for k, idx in enumerate(order):
        p_adj[idx] = min(1.0, ps[idx] * n_tests / (k + 1))
    for k in range(n_tests - 2, -1, -1):
        p_adj[order[k]] = min(p_adj[order[k]], p_adj[order[k + 1]])
    result['p_fdr']           = np.round(p_adj, 5)
    result['significant_fdr'] = result['p_fdr'] < 0.05
    return result


# ────────────────────────────────────────────────────────────────────────────
# 6.9 Сводная таблица результатов по гипотезам
# ────────────────────────────────────────────────────────────────────────────

def build_hypothesis_summary(h1_primary, h1_sensitivity=None,
                             h2_res=None, h3_res=None, h4_res=None,
                             subsample_label='full'):
    """Собирает результаты H1-H4 в единую сводную таблицу.

    Параметры
    ---------
    h1_primary      : DataFrame из wilcoxon_condition_test() для H1 (pupil_pct_change_avg)
    h1_sensitivity  : DataFrame для H1 sensitivity (pupil_z_avg), опционально
    h2_res          : DataFrame для H2 (fixation_count)
    h3_res          : DataFrame из wilcoxon_nasa_condition_test()
    h4_res          : DataFrame из wilcoxon_completion_test()
    subsample_label : str  — метка подвыборки ('full', 'easy_tasks', ...)

    Returns DataFrame: hypothesis, metric, N, test, W, p_raw, p_perm,
                       p_FDR, rank_biserial_r, cohens_d, r_CI95, d_CI95,
                       decision, subsample
    """
    _H_MAP = {
        'pupil_pct_change_avg':   ('H1', 'Wilcoxon paired (less)', 'df_good'),
        'pupil_z_avg':            ('H1 sensitivity', 'Wilcoxon paired (less)', 'df_good'),
        'fixation_count':         ('H2', 'Wilcoxon paired (two-sided)', 'df_all'),
        'nasa_unweighted':        ('H3', 'Wilcoxon paired (less)', 'nasa_df'),
        'nasa_weighted':          ('H3', 'Wilcoxon paired (less)', 'nasa_df'),
        'completion_ms_median':   ('H4', 'Wilcoxon paired (less)', 'df_all'),
    }

    parts = []
    for src in [h1_primary, h1_sensitivity, h2_res, h3_res, h4_res]:
        if src is None or src.empty:
            continue
        parts.append(src)

    if not parts:
        return pd.DataFrame()

    combined = pd.concat(parts, ignore_index=True)

    rows = []
    for _, row in combined.iterrows():
        metric = row.get('metric', '')
        hyp, test_name, df_label = _H_MAP.get(metric, ('?', 'Wilcoxon paired', '?'))
        n = int(row.get('N_pairs', 0))
        p_raw  = row.get('p_raw',  np.nan)
        p_perm = row.get('p_perm', np.nan)
        p_fdr  = row.get('p_fdr',  np.nan)
        r      = row.get('rank_biserial_r', np.nan)
        d      = row.get('cohens_d', np.nan)
        r_lo   = row.get('r_CI95_lo', np.nan)
        r_hi   = row.get('r_CI95_hi', np.nan)
        d_lo   = row.get('d_CI95_lo', np.nan)
        d_hi   = row.get('d_CI95_hi', np.nan)

        sig_fdr = bool(row.get('significant_fdr', False))
        decision = 'Подтверждена*' if sig_fdr else ('Тенденция' if (not np.isnan(p_raw) and p_raw < 0.10) else 'Не подтверждена')

        r_ci_str = f'[{r_lo:.2f}, {r_hi:.2f}]' if not (np.isnan(r_lo) or np.isnan(r_hi)) else '—'
        d_ci_str = f'[{d_lo:.2f}, {d_hi:.2f}]' if not (np.isnan(d_lo) or np.isnan(d_hi)) else '—'

        rows.append({
            'hypothesis':      hyp,
            'metric':          metric,
            'df_used':         df_label,
            'N_pairs':         n,
            'test':            test_name,
            'W':               row.get('W', np.nan),
            'p_raw':           p_raw,
            'p_perm':          p_perm,
            'p_FDR':           p_fdr,
            'rank_biserial_r': r,
            'cohens_d':        d,
            'r_CI95':          r_ci_str,
            'd_CI95':          d_ci_str,
            'decision':        decision,
            'subsample':       subsample_label,
        })

    return pd.DataFrame(rows)


# ────────────────────────────────────────────────────────────────────────────
# 7. Сбалансированная подвыборка для корреляционного анализа
# ────────────────────────────────────────────────────────────────────────────

def build_balanced_subsample(quality_df, conditions_csv=None):
    """Выбирает 8 участников (2 per 2×2 ячейка) на основе качества данных.

    Из ячеек с N=3 оставляет 2 с наименьшим pct_invalid_pupil.
    Из ячеек с N=2 оставляет обоих.
    Из ячеек с N=1 оставляет единственного (предупреждение).

    Returns list of participant IDs (str).
    """
    cond_df = pd.read_csv(conditions_csv or CONDITIONS_CSV)
    cond_df['id'] = cond_df['id'].astype(str)
    quality = quality_df.copy()
    quality['participant'] = quality['participant'].astype(str)

    merged = cond_df.merge(quality, left_on='id', right_on='participant', how='left')

    selected = []
    for disc_order in (1, 2):
        for ds_order in (1, 2):
            cell = merged[
                (merged['disclosure_order'] == disc_order) &
                (merged['dataset_order']    == ds_order)
            ].sort_values('pct_invalid_pupil', ascending=True)
            n_cell = len(cell)
            n_keep = min(2, n_cell)
            if n_cell < 2:
                print(f'  ⚠ Ячейка disc={disc_order}, ds={ds_order}: только {n_cell} участник(а)')
            selected.extend(cell['id'].head(n_keep).tolist())

    return sorted(set(selected))


# ────────────────────────────────────────────────────────────────────────────
# 8. Корреляции Спирмена
# ────────────────────────────────────────────────────────────────────────────

def spearman_nasa_eyetrack(gaze_df, nasa_df):
    """Корреляции Спирмена NASA-TLX × ET-метрики (participant×block).

    Returns: corr_r, corr_p, merged
    """
    et_block = (
        gaze_df.groupby(['participant', 'block_num'])[list(_ET_METRICS.keys())]
        .mean()
        .reset_index()
        .rename(columns={'participant': 'id', 'block_num': 'block'})
    )
    et_block['id']    = et_block['id'].astype(str)
    et_block['block'] = et_block['block'].astype(int)

    nasa = nasa_df[['id', 'block'] + list(_NASA_CORR_COLS.keys())].copy()
    nasa['id']    = nasa['id'].astype(str)
    nasa['block'] = nasa['block'].astype(int)

    merged = et_block.merge(nasa, on=['id', 'block'], how='inner')

    r_rows, p_rows = {}, {}
    for et_col, et_label in _ET_METRICS.items():
        r_row, p_row = {}, {}
        for nasa_col, nasa_label in _NASA_CORR_COLS.items():
            sub = merged[[et_col, nasa_col]].dropna()
            if len(sub) >= 4:
                r, p = stats.spearmanr(sub[et_col], sub[nasa_col])
            else:
                r, p = np.nan, np.nan
            r_row[nasa_label] = round(float(r), 3) if not np.isnan(r) else np.nan
            p_row[nasa_label] = round(float(p), 4) if not np.isnan(p) else np.nan
        r_rows[et_label] = r_row
        p_rows[et_label] = p_row

    return pd.DataFrame(r_rows).T, pd.DataFrame(p_rows).T, merged


def spearman_by_split(gaze_df, nasa_df, split_col='condition'):
    """Корреляции Спирмена, стратифицированные по split_col ('condition' или 'dataset').

    Returns dict: split_value → (r_df, p_df, merged_sub)
    """
    if split_col not in nasa_df.columns:
        raise ValueError(f"'{split_col}' не найдена в nasa_df. "
                         f"Доступные: {nasa_df.columns.tolist()}")

    et_block = (
        gaze_df.groupby(['participant', 'block_num'])[list(_ET_METRICS.keys())]
        .mean()
        .reset_index()
        .rename(columns={'participant': 'id', 'block_num': 'block'})
    )
    et_block['id']    = et_block['id'].astype(str)
    et_block['block'] = et_block['block'].astype(int)

    nasa = nasa_df[['id', 'block', split_col] + list(_NASA_CORR_COLS.keys())].copy()
    nasa['id']    = nasa['id'].astype(str)
    nasa['block'] = nasa['block'].astype(int)

    merged = et_block.merge(nasa, on=['id', 'block'], how='inner')

    results = {}
    for split_val in sorted(merged[split_col].dropna().unique()):
        sub = merged[merged[split_col] == split_val].copy()
        r_rows, p_rows = {}, {}
        for et_col, et_label in _ET_METRICS.items():
            r_row, p_row = {}, {}
            for nasa_col, nasa_label in _NASA_CORR_COLS.items():
                s = sub[[et_col, nasa_col]].dropna()
                if len(s) >= 4:
                    r_val, p_val = stats.spearmanr(s[et_col], s[nasa_col])
                    r_row[nasa_label] = round(float(r_val), 3)
                    p_row[nasa_label] = round(float(p_val), 4)
                else:
                    r_row[nasa_label] = np.nan
                    p_row[nasa_label] = np.nan
            r_rows[et_label] = r_row
            p_rows[et_label] = p_row
        results[split_val] = (pd.DataFrame(r_rows).T, pd.DataFrame(p_rows).T, sub)
    return results


def apply_fdr_to_spearman(corr_p):
    """Применяет FDR BH к матрице p-значений Спирмена (DataFrame).

    Возвращает DataFrame той же формы с FDR-скорректированными p-значениями.
    """
    flat = corr_p.values.flatten().astype(float)
    n = len(flat)
    order = np.argsort(flat)
    p_adj = np.empty(n)
    for k, idx in enumerate(order):
        p_adj[idx] = min(1.0, flat[idx] * n / (k + 1))
    for k in range(n - 2, -1, -1):
        p_adj[order[k]] = min(p_adj[order[k]], p_adj[order[k + 1]])
    return pd.DataFrame(
        np.round(p_adj.reshape(corr_p.shape), 5),
        index=corr_p.index,
        columns=corr_p.columns,
    )


def plot_spearman_heatmap(corr_r, corr_p, save_path=None, show=False):
    """Тепловая карта r Спирмена со звёздочками значимости."""
    r_vals = corr_r.values.astype(float)
    p_vals = corr_p.values.astype(float)

    fig, ax = plt.subplots(figsize=(len(corr_r.columns) * 1.4 + 1,
                                    len(corr_r) * 1.0 + 1.2))
    im = ax.imshow(r_vals, cmap='RdBu_r', vmin=-1, vmax=1, aspect='auto')

    ax.set_xticks(range(len(corr_r.columns)))
    ax.set_xticklabels(corr_r.columns, rotation=35, ha='right', fontsize=9)
    ax.set_yticks(range(len(corr_r.index)))
    ax.set_yticklabels(corr_r.index, fontsize=9)

    for i in range(r_vals.shape[0]):
        for j in range(r_vals.shape[1]):
            r = r_vals[i, j]
            p = p_vals[i, j]
            if np.isnan(r):
                txt = '–'
            else:
                stars = '**' if p < 0.01 else ('*' if p < 0.05 else '')
                txt = f'{r:.2f}{stars}'
            ax.text(j, i, txt, ha='center', va='center',
                    fontsize=9, color='white' if abs(r) > 0.5 else 'black')

    plt.colorbar(im, ax=ax, label='Спирмен r', shrink=0.8)
    ax.set_title('Корреляции Спирмена: NASA-TLX × айтрекинг\n* p<0.05  ** p<0.01', fontsize=11)
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
    if show:
        plt.show()
    return fig


# ────────────────────────────────────────────────────────────────────────────
# 9. Графики зрачка
# ────────────────────────────────────────────────────────────────────────────

def _pupil_series(task_df):
    """TIME (от 0) и диаметр зрачка; невалидные сэмплы отфильтрованы."""
    if task_df is None or len(task_df) == 0:
        return np.array([]), np.array([])

    df = task_df.copy()
    t0 = df['TIME'].iloc[0]
    t  = (df['TIME'] - t0).values

    both = (df['LPV'] == 1) & (df['RPV'] == 1)
    l_ok = (df['LPV'] == 1) & ~both
    r_ok = (df['RPV'] == 1) & ~both

    pupil = np.full(len(df), np.nan)
    pupil[both] = (df.loc[both, 'LPD'].values + df.loc[both, 'RPD'].values) / 2
    pupil[l_ok] =  df.loc[l_ok, 'LPD'].values
    pupil[r_ok] =  df.loc[r_ok, 'RPD'].values

    mask = ~np.isnan(pupil)
    return t[mask], pupil[mask]


def _pupil_series_split(task_df):
    """Полный временной ряд зрачка + булева маска валидности (без фильтрации NaN).

    Returns: t (от 0), pupil (NaN где оба глаза невалидны), is_valid (bool array)
    """
    if task_df is None or len(task_df) == 0:
        return np.array([]), np.array([]), np.array([], dtype=bool)

    df = task_df.copy()
    t0 = df['TIME'].iloc[0]
    t  = (df['TIME'] - t0).values

    both = (df['LPV'] == 1) & (df['RPV'] == 1)
    l_ok = (df['LPV'] == 1) & ~both
    r_ok = (df['RPV'] == 1) & ~both

    pupil = np.full(len(df), np.nan)
    pupil[both] = (df.loc[both, 'LPD'].values + df.loc[both, 'RPD'].values) / 2
    pupil[l_ok] =  df.loc[l_ok, 'LPD'].values
    pupil[r_ok] =  df.loc[r_ok, 'RPD'].values

    is_valid = (both | l_ok | r_ok).values
    return t, pupil, is_valid


def _shade_palette(base_hex, n, lightness_range=(0.25, 0.65)):
    """Генерирует N оттенков из базового HEX-цвета (варьирует lightness)."""
    r_v = int(base_hex[1:3], 16) / 255
    g_v = int(base_hex[3:5], 16) / 255
    b_v = int(base_hex[5:7], 16) / 255
    h, l, s = colorsys.rgb_to_hls(r_v, g_v, b_v)
    colors = []
    for i in range(max(n, 1)):
        frac = i / max(n - 1, 1)
        li = lightness_range[0] + (lightness_range[1] - lightness_range[0]) * frac
        colors.append(colorsys.hls_to_rgb(h, li, s))
    return colors


def plot_pupil_traces(all_df, split_by='condition', participant_ids=None,
                      save_path=None, show=False,
                      use_cleaned=False, clean_params=None):
    """Детальный график зрачка 3×4: оттенки по участнику, невалидные как rug.

    split_by='condition': flat=синий (#2266cc), sections=оранжевый (#cc4400)
    split_by='dataset':   alpha=желто-зелёный (#55aa44), beta=фиолетовый (#9944cc)

    Невалидные сэмплы: eventplot у нижней границы оси, alpha=0.1.
    """
    base_colors = _SPLIT_BASE_COLORS.get(split_by, _SPLIT_BASE_COLORS['condition'])
    pids = participant_ids or _participants()

    # Собираем (pid, block_num) per split_val
    split_pid_blocks = {sv: [] for sv in base_colors}
    for pid in pids:
        pid_rows = all_df[all_df['participant'] == pid]
        for bn in (1, 2):
            block_rows = pid_rows[pid_rows['block_num'] == bn]
            if len(block_rows) == 0:
                continue
            sv = block_rows[split_by].iloc[0] if split_by in block_rows.columns else ''
            if sv in split_pid_blocks:
                split_pid_blocks[sv].append((pid, bn))

    # Предрассчитываем допустимые комбинации (pid, block, task_id) — избегаем лишних вызовов
    valid_combos: set = set()
    if all_df is not None and len(all_df) > 0:
        for _, r in all_df[['participant', 'block_num', 'task_id']].iterrows():
            valid_combos.add((str(r['participant']), int(r['block_num']), str(r['task_id'])))

    palettes = {sv: _shade_palette(base_colors[sv], len(pb))
                for sv, pb in split_pid_blocks.items()}

    fig, axes = plt.subplots(3, 4, figsize=(20, 12))
    split_lbl = 'условию' if split_by == 'condition' else 'датасету'
    fig.suptitle(f'Диаметр зрачка по {split_lbl} (валидные → скользящее среднее; '
                 'невалидные → rug 10%)', fontsize=12)

    gaze_cache = {}

    for ax_idx, task_id in enumerate(TASK_ORDER):
        ax = axes.flatten()[ax_idx]
        if len(all_df[all_df['task_id'] == task_id]) == 0:
            ax.set_visible(False)
            continue

        ax.set_title(task_id, fontsize=9)
        ax.set_xlabel('Время (с)', fontsize=7)
        ax.set_ylabel('Зрачок (мм)', fontsize=7)
        ax.tick_params(labelsize=7)

        for sv, pid_blocks in split_pid_blocks.items():
            palette = palettes[sv]
            for shade_idx, (pid, bn) in enumerate(pid_blocks):
                color_rgb = palette[shade_idx % len(palette)]
                key = (pid, bn)
                if key not in gaze_cache:
                    gp = os.path.join(DATA_ROOT, pid,
                                      f'gaze_{pid}_pd_b{bn}_trial.tsv')
                    gaze_cache[key] = load_gaze_data(gp) if os.path.isfile(gp) else None

                bdf = gaze_cache[key]
                if bdf is None:
                    continue
                # Пропускаем заведомо отсутствующие комбинации без вывода warnings
                if valid_combos and (pid, bn, task_id) not in valid_combos:
                    continue
                task_df = extract_task_gaze(bdf, task_id)
                if len(task_df) == 0:
                    continue

                if use_cleaned:
                    ct = clean_gp3_trace(task_df, params=clean_params, drop_first_sec=0)
                    t0 = task_df['TIME'].iloc[0]
                    t  = (task_df['TIME'].values - t0)
                    pupil    = ct.pupil_clean
                    is_valid = ~np.isnan(pupil)
                else:
                    t, pupil, is_valid = _pupil_series_split(task_df)
                if len(t) == 0:
                    continue

                # Валидные → скользящее среднее
                valid_t = t[is_valid]
                valid_p = pupil[is_valid]
                if len(valid_t) >= ROLL_WIN:
                    smooth = (pd.Series(valid_p)
                              .rolling(ROLL_WIN, center=True, min_periods=1)
                              .mean().values)
                    ax.plot(valid_t, smooth, color=color_rgb, lw=0.8, alpha=0.7)

                # Невалидные / неинтерполированные → rug у нижней границы
                inv_t = t[~is_valid]
                if len(inv_t) > 0:
                    y_lim = ax.get_ylim()
                    y_rug = y_lim[0] + 0.03 * (y_lim[1] - y_lim[0])
                    ax.eventplot(
                        inv_t,
                        orientation='horizontal',
                        lineoffsets=y_rug,
                        linelengths=0.04 * (y_lim[1] - y_lim[0]),
                        colors=[color_rgb],
                        alpha=0.1,
                        linewidths=0.5,
                    )

    from matplotlib.lines import Line2D
    legend_el = [Line2D([0], [0], color=base_colors[sv], lw=2, label=sv)
                 for sv in base_colors]
    fig.legend(handles=legend_el, loc='lower center', ncol=len(base_colors),
               fontsize=10, bbox_to_anchor=(0.5, 0.01))
    plt.tight_layout(rect=[0, 0.04, 1, 1])

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
    if show:
        plt.show()
    return fig


def plot_pupil_time_participant(pid, all_df, save_dir=None, show=False):
    """Фигура 3×4: один подграфик на задание, скользящее среднее зрачка (per-participant)."""
    pid_rows = all_df[all_df['participant'] == pid]
    if len(pid_rows) == 0:
        return None

    pid_dir   = os.path.join(DATA_ROOT, pid)
    block_dfs = {}
    for bn in (1, 2):
        p = os.path.join(pid_dir, f'gaze_{pid}_pd_b{bn}_trial.tsv')
        if os.path.isfile(p):
            block_dfs[bn] = load_gaze_data(p)

    fig, axes = plt.subplots(3, 4, figsize=(18, 10))
    fig.suptitle(f'Участник {pid} — диаметр зрачка во времени', fontsize=13)

    for ax_idx, task_id in enumerate(TASK_ORDER):
        ax = axes.flatten()[ax_idx]
        task_rows = pid_rows[pid_rows['task_id'] == task_id]
        if len(task_rows) == 0:
            ax.set_visible(False)
            continue

        meta = task_rows.iloc[0]
        bn   = int(meta['block_num'])
        cond = meta['condition']
        bdf  = block_dfs.get(bn)

        if bdf is None:
            ax.set_title(f'{task_id}  [{cond}]\n[нет файла]', fontsize=8)
            continue

        task_df = extract_task_gaze(bdf, task_id)
        t, pupil = _pupil_series(task_df)
        if len(t) > 0:
            smooth = (pd.Series(pupil)
                      .rolling(ROLL_WIN, center=True, min_periods=1)
                      .mean().values)
            ax.plot(t, smooth, lw=1.2, color='#2266cc' if cond == 'flat' else '#cc4400')
            ax.set_xlim(left=0)

        ax.set_title(f'{task_id}  [{cond}]', fontsize=9)
        ax.set_xlabel('Время (с)', fontsize=7)
        ax.set_ylabel('Зрачок (мм)', fontsize=7)
        ax.tick_params(labelsize=7)

    plt.tight_layout()
    if save_dir:
        out = os.path.join(save_dir, f'pd_pupil_time_{pid}.png')
        plt.savefig(out, dpi=150, bbox_inches='tight')
    if show:
        plt.show()
    return fig


def plot_pupil_time_all(all_df):
    """Сохраняет PNG графиков зрачка для каждого участника."""
    for pid in _participants():
        fig = plot_pupil_time_participant(pid, all_df,
                                          save_dir=os.path.join(DATA_ROOT, pid))
        if fig is not None:
            plt.close(fig)


def _normalize_pupil_trace(t, pupil, n_bins=100):
    if len(t) < 2:
        return None
    t_norm = (t - t.min()) / (t.max() - t.min())
    bins = np.linspace(0, 1, n_bins)
    return np.interp(bins, t_norm, pupil)


def plot_pupil_by_group(all_df, split_by='condition', n_bins=100,
                        save_path=None, show=False):
    """Сводный нормализованный зрачок: среднее ± SEM по группам.

    Для каждого участника × задания извлекает нормализованный ряд зрачка,
    группирует по split_by ('condition' или 'dataset'),
    усредняет все трассы внутри группы и рисует доверительный коридор ±SEM.

    Returns fig
    """
    base_colors = _SPLIT_BASE_COLORS.get(split_by, _SPLIT_BASE_COLORS['condition'])
    bins = np.linspace(0, 1, n_bins)

    # Предрассчитываем допустимые комбинации
    valid_combos: set = set()
    for _, r in all_df[['participant', 'block_num', 'task_id']].iterrows():
        valid_combos.add((str(r['participant']), int(r['block_num']), str(r['task_id'])))

    # Собираем трассы per group; отдельно считаем уникальных участников
    group_traces: dict  = {sv: [] for sv in base_colors}
    group_pids:   dict  = {sv: set() for sv in base_colors}
    gaze_cache:   dict  = {}

    for pid in _participants():
        pid_rows = all_df[all_df['participant'] == pid]
        for bn in (1, 2):
            block_rows = pid_rows[pid_rows['block_num'] == bn]
            if len(block_rows) == 0:
                continue
            sv = block_rows[split_by].iloc[0] if split_by in block_rows.columns else ''
            if sv not in group_traces:
                continue

            key = (pid, bn)
            if key not in gaze_cache:
                gp = os.path.join(DATA_ROOT, pid, f'gaze_{pid}_pd_b{bn}_trial.tsv')
                gaze_cache[key] = load_gaze_data(gp) if os.path.isfile(gp) else None

            bdf = gaze_cache[key]
            if bdf is None:
                continue

            for _, row in block_rows.iterrows():
                task_id = str(row['task_id'])
                if (pid, bn, task_id) not in valid_combos:
                    continue
                tdf = extract_task_gaze(bdf, task_id)
                if len(tdf) == 0:
                    continue
                t, p = _pupil_series(tdf)
                trace = _normalize_pupil_trace(t, p, n_bins)
                if trace is not None:
                    group_traces[sv].append(trace)
                    group_pids[sv].add(pid)

    fig, ax = plt.subplots(figsize=(12, 5))
    split_lbl = 'условию' if split_by == 'condition' else 'датасету'
    ax.set_title(f'Средний нормализованный зрачок по {split_lbl} (среднее ± SEM)', fontsize=12)
    ax.set_xlabel('Доля времени задания [0–1]', fontsize=11)
    ax.set_ylabel('Диаметр зрачка (мм)', fontsize=11)

    for sv, color in base_colors.items():
        traces = group_traces.get(sv, [])
        if not traces:
            continue
        mat  = np.array(traces)                        # (N_traces, n_bins)
        mean = np.nanmean(mat, axis=0)
        n    = np.sum(~np.isnan(mat), axis=0)
        sem  = np.nanstd(mat, axis=0, ddof=1) / np.sqrt(np.maximum(n, 1))
        n_p  = len(group_pids[sv])
        ax.plot(bins, mean, color=color, lw=2.5,
                label=f'{sv}  ({n_p} уч., {len(traces)} трасс)')
        ax.fill_between(bins, mean - sem, mean + sem,
                        color=color, alpha=0.20, linewidth=0)

    ax.legend(fontsize=10, loc='best')
    ax.set_xlim(0, 1)
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
    if show:
        plt.show()
    return fig


def plot_pupil_by_task(all_df, save_path=None, show=False):
    """Сводный нормализованный график зрачка по заданию."""
    n_bins = 100
    fig, ax = plt.subplots(figsize=(14, 5))
    ax.set_title('Средний диаметр зрачка по заданию (нормализованное время)', fontsize=12)
    ax.set_xlabel('Доля времени задания', fontsize=10)
    ax.set_ylabel('Зрачок (мм)', fontsize=10)

    bins = np.linspace(0, 1, n_bins)
    cond_color = {'flat': '#2266cc', 'sections': '#cc4400'}

    for task_id in TASK_ORDER:
        task_meta = all_df[all_df['task_id'] == task_id]
        if len(task_meta) == 0:
            continue
        cond  = task_meta['condition'].mode().iloc[0]
        color = cond_color.get(cond, 'gray')

        traces = []
        for pid in _participants():
            pid_rows = task_meta[task_meta['participant'] == pid]
            if len(pid_rows) == 0:
                continue
            bn = int(pid_rows.iloc[0]['block_num'])
            gaze_path = os.path.join(DATA_ROOT, pid, f'gaze_{pid}_pd_b{bn}_trial.tsv')
            if not os.path.isfile(gaze_path):
                continue
            bdf   = load_gaze_data(gaze_path)
            tdf   = extract_task_gaze(bdf, task_id)
            t, p  = _pupil_series(tdf)
            trace = _normalize_pupil_trace(t, p, n_bins)
            if trace is not None:
                traces.append(trace)

        if traces:
            ax.plot(bins, np.nanmean(traces, axis=0),
                    lw=1.5, color=color, alpha=0.75, label=task_id)

    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], color=cond_color['flat'],     lw=2, label='flat'),
        Line2D([0], [0], color=cond_color['sections'], lw=2, label='sections'),
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=9)
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
    if show:
        plt.show()
    return fig


# ────────────────────────────────────────────────────────────────────────────
# main
# ────────────────────────────────────────────────────────────────────────────

def main():
    plt.switch_backend('Agg')
    print('=== PD Eye-Tracking: групповой анализ ===\n')
    print(f'Участники:    {_participants()}')
    print(f'NASA TLX CSV: {NASA_TLX_CSV}\n')

    df      = load_all_participants()
    nasa_df = score_nasa_tlx()
    stai_df = score_stai()
    print(f'Строк: {len(df)}\n')

    cond_summary = summary_by_condition(df)
    task_summary = summary_by_task(df)

    with pd.ExcelWriter(OUT_XLSX, engine='openpyxl') as writer:
        df.to_excel(writer,           sheet_name='per_task_all', index=False)
        cond_summary.to_excel(writer, sheet_name='by_condition', index=False)
        task_summary.to_excel(writer, sheet_name='by_task',      index=False)
        stai_df.to_excel(writer,      sheet_name='stai_scores',  index=False)
        nasa_df.to_excel(writer,      sheet_name='nasa_tlx',     index=False)
    print(f'Сохранено: {OUT_XLSX}')

    corr_r, corr_p, _ = spearman_nasa_eyetrack(df, nasa_df)
    fig = plot_spearman_heatmap(
        corr_r, corr_p,
        save_path=os.path.join(DATA_ROOT, 'pd_spearman_heatmap.png'),
    )
    plt.close(fig)
    print('Готово.')


if __name__ == '__main__':
    main()
