# Архитектура анализа — PD Eye-Tracking

> Документ описывает все модули в папке `analysis/`, их назначение, внутреннюю логику,
> порядок вызовов и мотивацию методологических решений.
> Актуален для ветки `pupil-preprocessing-v2` (май 2026).

---

## Структура папки

```
analysis/
├── pupil_preprocessing.py      # Самодостаточный модуль очистки зрачкового сигнала
├── inspect_pupil_speed.py      # CLI-врапер для калибровки speed-filter порога
├── analysis_pd.py              # Per-participant ETметрики (ядро пайплайна)
├── pd_analysis.py              # Групповой анализ: статистика, тесты, графики
├── pd_analysis.ipynb           # Интерактивный ноутбук (вызывает pd_analysis.py)
├── group_analysis_pd.py        # Устаревший групповой анализ (не используется активно)
├── generate_overlays.py        # Генерация heatmap/overlay-видео (отдельная задача)
├── ANALYSIS_ARCHITECTURE.md    # Этот файл
└── HANDOFF_TO_CLAUDE_CODE.md   # История интеграции pupil_preprocessing (архив)
```

---

## Поток данных: от сырых файлов до результата

```
data/<pid>/
  gaze_<pid>_pd_b1_trial.tsv   ─┐
  gaze_<pid>_pd_b2_trial.tsv   ─┤─→ analysis_pd.analyze_participant()
  events_<pid>_*.csv           ─┘        │
  pd_<pid>_nasa_tlx_b*.json             │
                                         ▼
                              list of dicts (per-task, ~12 строк на участника)
                                         │
                              pd_analysis.load_all_participants()
                                         │
                                         ▼
                              df_all  (DataFrame, 120 строк = 10 уч × 12 задач)
                                         │
                              ┌──────────┼──────────────┐
                              ▼          ▼              ▼
                           df_good   df_balanced    nasa_df
                        (8 уч, QC)  (8 уч, 2×2)  (опросники)
                              │
                    wilcoxon_condition_test()
                    build_hypothesis_summary()
                              │
                              ▼
                    data/pd_group_summary.xlsx
                    графики (PNG)
```

---

## Модуль 1: `pupil_preprocessing.py`

### Назначение

Самодостаточная библиотека очистки зрачкового сигнала по стандарту Mathôt & Vilotijević (2023).
Не знает ни о структуре папок, ни о GP3 — работает с любым DataFrame у которого есть
колонки `TIME`, `LPD`, `RPD`, `LPV`, `RPV`.

### Конфигурация: `DEFAULT_PARAMS`

```python
DEFAULT_PARAMS = {
    'speed_thr_px_per_sample': 0.5,   # порог speed filter (|ΔD| > thr → NaN)
    'nan_margin_samples':       2,     # ±N сэмплов вокруг каждого NaN
    'max_gap_samples':          15,    # максимальный гэп для интерполяции (250 мс @ 60 Гц)
    'interp_method':       'cubic',    # cubic с fallback на linear
    'sd_outlier_thr':          3.0,    # финальный SD-pass
    'min_valid_pct_for_metrics': 50.0, # минимум % валидных для вычисления метрик
}
```

**Выбор порога 0.5:** анализ `inspect_pupil_speed.py` на всех 10 участниках показал
унимодальное распределение |ΔD|. Порог 0.5 = p90 бинокулярного сигнала — консервативный,
документированный. Автоматически рекомендованный порог 7.77 (p99.9) физиологически не
обоснован при отсутствии бимодальности.

### Ключевые функции и их порядок в пайплайне

#### `build_binocular_pupil(lpd, rpd, lpv, rpv) → np.ndarray`

Правило **at-least-one-eye-valid** (Kret & Sjak-Shie 2019):
- Оба глаза валидны → среднее `(LPD + RPD) / 2`
- Только левый → `LPD`
- Только правый → `RPD`
- Оба невалидны → `NaN`

*Почему не both-eyes-valid:* старый код в `analysis_pd` требовал обоих валидных,
что теряло данные когда один глаз временно выходил из захвата (моргание с одной стороны,
частичное перекрытие). At-least-one-eye дает больше сэмплов и меньше NaN-гэпов.

#### `_apply_speed_filter(pupil, thr) → (pupil, n_removed)`

Маскирует сэмплы где `|Δpupil[i]| > thr`. Применяется **одинаково** к baseline и task.
Старый `analysis_pd` применял speed filter только к baseline — это создавало асимметрию:
task-период не фильтровался, что загрязняло `pupil_mean_avg` blink-артефактами.

#### `_expand_nan_margin(pupil, margin=2) → pupil`

Расширяет каждый NaN на ±2 сэмпла. Мотивация: края моргания — переходные состояния
(полуоткрытое веко), метрически ненадёжны. Margin убирает эти краевые сэмплы.

#### `_interp_short_gaps(t, pupil, max_gap_samples=15) → (pupil, stats)`

Интерполирует гэпы NaN длиной ≤ 15 сэмплов (≤ 250 мс при 60 Гц):
- **Cubic** если по 2 опорные точки до и после гэпа (Mathôt 2013 — рекомендован
  для гладких физиологических сигналов, минимизирует edge-ringing)
- **Linear** как fallback (1+1 опорная точка)
- NaN остаётся при гэпе > 15 сэмплов (вероятно, длинное моргание или потеря трека)

*stats возвращает:* `cubic_filled`, `linear_filled`, `left_as_nan` — для QC.

#### `_apply_sd_outlier(pupil, sd_thr=3.0) → (pupil, n_removed)`

Финальный одноитерационный outlier pass: `|x − mean| > 3σ → NaN`.
Работает на уже интерполированном сигнале. Не рекурсивный (рекурсия дала бы
агрессивное shrinking), одного прохода достаточно для явных выбросов.

#### `clean_pupil_array(t, pupil_raw, params) → (pupil_clean, qc_dict)`

Оркестрирует весь пайплайн: `build_binocular` → `speed_filter` → `margin` →
`interpolation` → `sd_outlier`. Возвращает очищенный массив + QC-словарь с подробной
статистикой по каждому шагу.

#### `CleanedTrace` (dataclass)

```python
@dataclass
class CleanedTrace:
    t:           np.ndarray   # временная ось от 0
    pupil_clean: np.ndarray   # очищенный сигнал, NaN = невосстановимые сэмплы
    pupil_raw:   np.ndarray   # исходный бинокулярный сигнал до чистки
    qc:          dict         # полная QC-статистика

    # Вычисляемые свойства:
    n_total:        int        # общее число сэмплов
    n_valid_final:  int        # не-NaN в pupil_clean
    pct_valid_final: float     # n_valid_final / n_total × 100
```

#### `clean_gp3_trace(gaze_df, params=None, drop_first_sec=0.0) → CleanedTrace`

Главная точка входа для внешнего кода. Принимает сырой DataFrame из GP3,
опционально отбрасывает первые N секунд (для baseline: `drop_first_sec=0.5`),
возвращает `CleanedTrace`.

#### `baseline_stats(cleaned, params) → dict`

Из `CleanedTrace` вычисляет mean и std baseline-периода.
Возвращает `NaN` если `pct_valid_final < min_valid_pct_for_metrics` (50%).
Ключи: `baseline_avg_mean`, `baseline_avg_std`, `baseline_pct_unrecoverable`,
`baseline_n_valid`.

#### `task_pupil_stats(cleaned, baseline_dict, params) → dict`

Три варианта baseline-коррекции:

| Ключ | Формула | Мотивация |
|------|---------|-----------|
| `pupil_subtractive_avg` | `mean_task − mean_baseline` | **Primary.** Mathôt et al. (2018): субтрактивная коррекция минимизирует мультипликативный шум |
| `pupil_z_avg` | `(mean_task − mean_baseline) / sd_baseline` | Sensitivity check: z-score нормирует на индивидуальную вариабельность |
| `pupil_pct_change_avg` | `(mean_task − mean_baseline) / mean_baseline × 100` | Legacy/divisive. Не рекомендована Mathôt (делитель вносит шум при малом baseline), сохранена для сравнения с предыдущим анализом |

Также возвращает: `pupil_mean_avg`, `task_pct_unrecoverable`, `task_n_valid`.

#### `calibrate_from_data(gaze_inputs, ...) → dict`

Собирает |ΔD| по всем TSV-файлам, строит гистограмму + CDF, рекомендует порог.
Возвращает dict с ключами `recommended_thr`, `lpd`, `rpd`, `avg`, `method`.

---

## Модуль 2: `inspect_pupil_speed.py`

Тонкий CLI-врапер над `calibrate_from_data`. Единственная задача — удобный
запуск из терминала без написания кода:

```bash
python inspect_pupil_speed.py data/
python inspect_pupil_speed.py data/ --current-thr 0.5 --method p99
```

Сохраняет `data/pupil_speed_distribution.png`. Используется один раз при калибровке
перед интеграцией — не входит в основной пайплайн.

---

## Модуль 3: `analysis_pd.py`

### Назначение

Per-participant анализ: принимает ID участника и папку с данными,
возвращает список словарей (одна строка = одно задание).
Это ядро пайплайна — всё остальное агрегирует его результаты.

### Вспомогательные функции загрузки

#### `load_tasks_meta(tasks_json)` → dict `{task_id: {type, dataset}}`

Читает `static/data/tasks.json` — маппинг ID задачи → тип (Lookup/Comparison/Diagnosis)
и датасет (alpha/beta). При отсутствии файла pd_analysis создаёт временный JSON из CSV.

#### `load_nasa_tlx_scores(participant_id, data_dir)` → list of dicts

Читает `pd_<pid>_nasa_tlx_b{1,2}_*.json`. Вычисляет взвешенную и невзвешенную оценку,
парные предпочтения. Результат объединяется с ET-метриками на уровне pd_analysis.

#### `parse_pd_events(events_csv)` → dict `{task_id: {block_num, condition, completion_ms, clicks, ...}}`

Парсит события из `events_<pid>_*.csv`:
- `PD_BLOCK_START_B1_FLAT_ALPHA` → condition='flat', block=1
- `PD_TASK_START_A1` → начало задачи
- `PD_TASK_END_A1_ct10703ms_clicks1` → completion time + clicks

#### `extract_task_gaze(block_df, task_id)` → DataFrame

Извлекает строки гейза между маркерами `PD_TASK_START_{id}` и `PD_TASK_END_{id}`.
Делегирует в `analysis.extract_trial_data()`.

#### `extract_iti_gaze(block_df, task_id)` → DataFrame

То же для маркеров `PD_ITI_START_{id}` / `PD_ITI_END_{id}`.
ITI = Inter-Trial Interval, 2-секундный период с фиксационным крестом — baseline для зрачка.

### Зрачковые функции (v2 + legacy)

#### `compute_iti_pupil_baseline(iti_df, params=None)` — **текущая версия**

```
iti_df → clean_gp3_trace(drop_first_sec=0.5) → baseline_stats() → dict
```

`drop_first_sec=0.5` — отбрасывает первые 0.5 с стабилизации зрачка после появления
фиксационного креста. Возвращает dict совместимый со старым форматом плюс QC-поля.

*Примечание по per-eye полям:* `baseline_l_mean`, `baseline_r_mean` = `NaN` в новой
версии. Новый пайплайн работает с бинокулярным усреднением — отдельный per-eye baseline
не вычисляется. Поля сохранены для обратной совместимости с legacy-кодом.

#### `compute_iti_pupil_baseline_legacy(iti_df)` — оригинальная версия (v1)

Ручная реализация: `diff().abs() > 0.5` → row-wise удаление, `both-eyes-valid` среднее.
Применяла speed filter **только к baseline**, task не фильтровался.
Сохранена для sensitivity-анализа (сравнение метрик v1 и v2).

#### `compute_task_metrics(task_df, iti_baseline, task_meta, params=None)` — **текущая версия**

Главная функция метрик задания. Структура:

```
1. Метаданные (participant, task_id, condition, completion_ms, clicks, ...)
2. Длительность (viewing_duration_sec)
3. Фиксации → _fixations_from_trial() → fixation_count, duration_mean/median/std/total, rate
4. Саккады → _saccades_from_fixations() → saccade_count, amplitude_mean, scanpath_length
5. Зрачок (НОВЫЙ ПАЙПЛАЙН):
      clean_gp3_trace(task_df, drop_first_sec=0)
      → task_pupil_stats(cleaned, iti_baseline)
      → pupil_mean_avg, pupil_subtractive_avg, pupil_z_avg, pupil_pct_change_avg
        task_pct_unrecoverable, task_n_valid
   + per-eye простые средние (pupil_mean_left/right) для совместимости
6. AOI-метрики → dwell time и fixation count для task bar / sidebar / content
```

*Важно:* блоки фиксаций, саккад и AOI не изменялись при интеграции.
Они не зависят от зрачкового пайплайна и работают напрямую с сырым `task_df`.

#### `compute_task_metrics_legacy(task_df, iti_baseline, task_meta)` — оригинальная версия (v1)

Полная копия оригинала: both-eyes-valid средние без speed filter на task,
`pupil_pct_change_avg` и `pupil_z_avg` из ручных `_pct()` / `_z()` функций.

#### `analyze_participant(participant_id, data_dir, tasks_json)` → list of dicts

Основной entry point. Алгоритм:

```
1. Найти events_<pid>_*.csv → parse_pd_events()
2. Для block_num in (1, 2):
     a. Загрузить gaze_<pid>_pd_b{N}_trial.tsv → load_gaze_data()
     b. Для каждого task_id в блоке:
           extract_iti_gaze() → compute_iti_pupil_baseline()
           extract_task_gaze() → compute_task_metrics()
     c. Собрать список dict-ов
3. Вернуть список (12 строк = 2 блока × 6 задач)
```

Каждый блок загружается **отдельным файлом** (`b1` и `b2` разные TSV).
Это важно: блоки могут иметь разное качество трекинга (пример: участник 1818,
у которого b1 содержит 2% валидных сэмплов из-за сбоя записи).

#### `save_results(rows, participant_id, data_dir)` → путь к XLSX

Сохраняет результаты в `data/<pid>/pd_<pid>_metrics.xlsx` с листами:
- `per_task` — 12 строк, все метрики
- `per_block` — агрегированные по блоку
- `summary` — агрегированные по condition × task_type
- `nasa_tlx` — если переданы TLX-данные

---

## Модуль 4: `pd_analysis.py`

### Назначение

Групповой анализ: запускает `analyze_participant` для всех 10 участников,
объединяет данные, строит статистические тесты, визуализации.
Вызывается из `pd_analysis.ipynb` или напрямую `python pd_analysis.py`.

### Конфигурация

```python
DATA_ROOT          = 'data/'
QUALITY_THRESHOLD  = 0.30    # >30% невалидных сэмплов → excluded_pupil = True
ROLL_WIN           = 15      # окно скользящего среднего для графиков трасс
_TEST_METRICS      = [...]   # метрики для Wilcoxon тестов
PUPIL_METRICS      = ['pupil_pct_change_avg', 'pupil_z_avg']  # зрачковые (df_good)
NON_PUPIL_METRICS  = ['fixation_count', ...]                   # остальные (df_all)
```

### Формирование подвыборок

| Подвыборка | Определение | Использование |
|-----------|-------------|---------------|
| `df_all` | Все 10 участников | Референс, non-pupil метрики |
| `df_good` | Участники с `pct_invalid_pupil ≤ 30%` (8 чел: исключены 1818 и 2746) | Зрачковые метрики, H1 |
| `df_balanced` | 2 уч из каждой ячейки дизайна 2×2 | Корреляции ET × NASA-TLX |

*Почему отдельные подвыборки:* зрачок чувствителен к качеству трекинга.
Включение 1818 (60.6% невалидных) и 2746 (52.6%) в pupil-анализ исказило бы
средние и тесты. Фиксационные метрики робустнее к частичной потере трека.

### Статистические функции

#### `compute_data_quality_all(all_df, threshold)` → DataFrame

Для каждого участника считает `% невалидных сэмплов` (LPV=0 AND RPV=0 по всем task-периодам).
Флаг `excluded_pupil = pct_invalid > threshold * 100`.

#### `wilcoxon_condition_test(df, metrics, alternative, n_boot, n_perm)` → DataFrame

Основной статистический тест. Алгоритм:

```
1. Агрегировать df по participant × condition → средние метрики
2. Для каждой метрики:
     a. flat_vals, sections_vals — попарные средние по участникам
     b. scipy.stats.wilcoxon(flat, sections, alternative=alternative)
     c. rank_biserial_r = 1 − (2W) / (N(N+1)/2)
     d. Cohen's d из попарных разностей
     e. Bootstrap 95% CI для r и d (n_boot=10000 по умолчанию)
     f. Permutation test (n_perm=10000, знаковые перестановки)
3. FDR Benjamini-Hochberg по всем метрикам
```

*Почему Wilcoxon, а не t-test:* N=8 пар — ниже порога нормальности.
Wilcoxon не предполагает нормального распределения разностей.

*Почему permutation test дополнительно:* при N=8 таблицы критических значений
Wilcoxon на границе применимости. Пермутационный тест точный при любом N.

*alternative='less'* для H1: проверяем `flat < sections` (зрачок в sections больше).

#### `lmm_condition_test(df, metrics)` — exploratory

LMM: `metric ~ condition + dataset + block_num + (1|participant)`.
При N=8 нестабильна (singular fit), результаты только как exploratory.

#### `gee_condition_test(df, metrics)` — exploratory

GEE с exchangeable correlation — устойчивее LMM при малом N кластеров.

#### `build_hypothesis_summary(h1_primary, h1_sensitivity, h2_res, h3_res, h4_res)` → DataFrame

Собирает результаты H1–H4 в единую таблицу. Маппинг гипотез:

| Гипотеза | Метрика | Направление | DataFrame |
|---------|---------|------------|-----------|
| H1 primary | `pupil_subtractive_avg` | sections > flat | `df_good` |
| H1 sensitivity | `pupil_z_avg` | sections > flat | `df_good` |
| H1 legacy | `pupil_pct_change_avg` | sections > flat | `df_good` |
| H2 | `fixation_count` | two-sided | `df_all` |
| H3 | `nasa_unweighted`, `nasa_weighted` | sections < flat | nasa_df |
| H4 | `completion_ms_median` | sections < flat | `df_all` |

*Почему `pupil_subtractive_avg` стал primary (v2):* субтрактивная коррекция математически
предпочтительнее дивизивной при работе с pupillometry. Деление на baseline вносит
мультипликативный шум: если baseline случайно мал, `pct_change` раздувается вне зависимости
от реального задачного эффекта (Mathôt et al. 2018, Behav Res Methods 50:94).

### Визуализационные функции

#### `plot_pupil_traces(all_df, split_by, use_cleaned, ...)` → Figure

Грид 3×4 (12 заданий). Для каждого задания:
- Загружает gaze-файл (кэшируется в `gaze_cache` в пределах вызова)
- Если `use_cleaned=True`: прогоняет `clean_gp3_trace()` → рисует `pupil_clean`
  (только не-NaN сэмплы); невосстановленные гэпы = rug у нижней границы
- Если `use_cleaned=False` (old): `_pupil_series_split()` → both-eyes-valid
- Скользящее среднее (ROLL_WIN=15 сэмплов) для визуального сглаживания
- Цвета: оттенки от базового (flat=синий, sections=оранжевый, alpha=зелёный, beta=фиолетовый)

*Версия v2 (use_cleaned=True) — текущая* для всех ячеек ноутбука (8.2, 8.3).

#### `plot_pupil_by_task(all_df, ...)` — нормированный временной ряд

Ось X = нормированное время [0, 1]. Позволяет сравнивать внутризадачную
динамику зрачка между задачами разной длительности.

#### `plot_pupil_by_group(all_df, split_by, ...)` — групповое усреднение

Среднее ± SEM по группе. Видимая зона перекрытия = нет группового эффекта.

---

## Модуль 5: `pd_analysis.ipynb`

Интерактивная оболочка над `pd_analysis.py`. Порядок секций:

| Секция | Содержание |
|--------|-----------|
| 1. Загрузка данных | `load_all_participants()` → `df_all` |
| **1.5. Препроцессинг зрачка** *(новая)* | Калибровка speed filter, baseline-диагностика per participant, cleaned vs raw трассы, QC confound-чек, групповой анализ |
| 2. Качество данных | `compute_data_quality_all()` → `df_good`, `df_balanced` |
| 3. Демография | `demographic_stats()` |
| 4. STAI + NASA-TLX | `score_stai()`, `score_nasa_tlx()` |
| 5. Эффект порядка блоков | `wilcoxon_block_order_et()` |
| 6. Тесты гипотез H1–H4 | `wilcoxon_condition_test()`, `build_hypothesis_summary()` |
| 7. Корреляции ET × NASA-TLX | `spearman_nasa_eyetrack()` |
| 8. Трассы зрачка | `plot_pupil_traces(use_cleaned=True)`, `plot_pupil_by_task()` |
| 9. Метрики фиксаций | by condition/task_type/interaction |

**Зависимости между секциями:** `df_all` нужен везде → секция 1 всегда первая.
`df_good` нужен секциям 1.5 (ячейки 1.5.4–1.5.5 компенсируют это inline-вычислением),
6, 8 → секция 2 должна быть выполнена до них.

---

## Модуль 6: `generate_overlays.py`

Генерирует heatmap и overlay-видео с траекторией взгляда поверх записи экрана.
Не входит в основной аналитический пайплайн — запускается отдельно по необходимости.
Зависит от `analysis.py` и записей `screen_<pid>_*.mp4`.

---

## Модуль 7: `group_analysis_pd.py`

Устаревший групповой анализ (712 строк). Предшественник `pd_analysis.py`.
Не используется активно, сохранён для справки. Не имеет интеграции с
`pupil_preprocessing`.

---

## Зависимости между модулями

```
inspect_pupil_speed.py
        ↓ вызывает
pupil_preprocessing.py  ←──────────────────────────┐
        ↑                                            │
        │ импортируется                               │
analysis_pd.py  (clean_gp3_trace, baseline_stats,  │
                 task_pupil_stats, DEFAULT_PARAMS)   │
        ↑                                            │
        │ analyze_participant()                       │
pd_analysis.py  ←──── импортирует clean_gp3_trace ──┘
        ↑               (для plot_pupil_traces)
        │
pd_analysis.ipynb
```

`analysis.py` и `analysis_paintings.py` — базовые утилиты из родительской папки,
импортируются как `from analysis import load_gaze_data, extract_trial_data` и
`from analysis_paintings import _fixations_from_trial, _saccades_from_fixations`.

---

## Ключевые методологические решения

### Почему speed filter ≤ 0.5 (p90), а не авто-рекомендованный 7.77 (p99.9)?

Распределение |ΔD| унимодально — у него нет «естественного» разрыва между
физиологическими и артефактными изменениями. Авто-алгоритм ищет inflection point
на монотонном хвосте, что нефизиологично. Порог 0.5 — консервативный, на уровне p90,
соответствует скорости дилятации ~0.5 мм/сэмпл при 60 Гц.

### Почему субтрактивная коррекция primary, а не процентная?

Mathôt et al. (2018) показали, что дивизивная (`pct_change`) вносит мультипликативный шум:
при малом baseline случайный разброс baseline перемножается с задачным эффектом.
Субтрактивная коррекция линейна и устойчива к этому.

### Почему min_valid_pct_for_metrics = 50%?

При менее чем 50% валидных сэмплов среднее зрачка смещено: мы наблюдаем только
«хорошие» моменты, пропуская периоды потери трека. Это делает межзадачное сравнение
несостоятельным. 50% — стандартный консервативный порог (ср. Kret & Sjak-Shie 2019).

### Почему at-least-one-eye-valid, а не both-eyes-valid?

Требование обоих глаз теряет ~10–20% сэмплов при боковых морганиях. При GP3,
где один глаз иногда временно выходит из захвата из-за рефлексов или очков,
это существенная потеря. At-least-one-eye сохраняет эти сэмплы с минимальной
потерей точности (при коротком одностороннем выходе оба глаза всё равно
движутся синхронно).

---

## Git-история изменений (ветка `pupil-preprocessing-v2`)

| Коммит | Что изменено |
|--------|-------------|
| `dd8fc9b` | Добавлены `pupil_preprocessing.py` и `inspect_pupil_speed.py` |
| `a5e106c` | Добавлен `HANDOFF_TO_CLAUDE_CODE.md` |
| `df18eec` | Интеграция в `analysis_pd.py`: новые функции + _legacy-копии |
| `2bf5508` | Обновлены `pd_analysis.py` и `pd_analysis.ipynb` (групповой анализ) |
| `88263f9` | Добавлена Секция 1.5 в ноутбук + `use_cleaned=True` в trace plots |
| `895d30d` | Fix: `calibrate_from_data` возвращает dict, использовать `cal['recommended_thr']` |
| `ebb31fa` | Fix: `df_good` вычисляется inline в ячейках 1.5.4–1.5.5 |
