# vibe-eyetracking — Compressed Context for Claude Sessions

## Stack & Entry Point
```
Python 3.10 / Flask 3.0 / pywebview (embedded Chromium) / Gazepoint GP3 TCP API
experiment.py → Experiment class → run() → pywebview window + background _experiment_thread()
```

## Key Files

| File | Purpose |
|------|---------|
| `experiment.py` | Main orchestrator (Experiment class, ~1400 lines) |
| `server.py` | Flask routes + thread-safe event queue |
| `config.py` | All settings, EXPERIMENT_MODE flag |
| `frozen_utils.py` | Path resolution for PyInstaller bundle |
| `static/experiment.js` | sendEvent(), PAGE_LOADED, idle detection — loaded on every page |
| `static/pd_console.js` | PD UI: sidebar, sections toggle, AOI snapshots, click counter |
| `PyOpenGaze/opengaze.py` | GP3 TCP wrapper (1463 lines) |
| `analysis.py` | Shared gaze utilities: `load_gaze_data`, `extract_trial_data`, `compute_fixation_metrics`, `compute_pupil_metrics`, `compute_scanpath_length`, `extract_baseline_pupil` |
| `analysis_pd.py` | PD metrics: fixations, saccades, pupil (z-score vs ITI baseline), static AOI → `pd_<id>_metrics.xlsx` |
| `analysis_paintings.py` | Paintings phase: heatmaps, trajectories, XLSX |
| `pd_analysis.py` | **Групповой анализ PD** (все участники): сводные таблицы ET-метрик по условию/заданию, подсчёт баллов SAN/STAI/NASA-TLX, корреляции Спирмена NASA-TLX × ET, графики зрачка во времени → `data/pd_group_summary.xlsx` + PNG |
| `pd_analysis.ipynb` | Ноутбук с тем же анализом + инлайн-визуализации (импортирует функции из `pd_analysis.py`) |
| `group_analysis_pd.py` | Group stats for PD: paired Wilcoxon flat vs sections (+ by task_type), FDR BH → `group_results_pd/` |
| `group_analysis.py` | (legacy) Group stats for aws/gcp design — kept for reference |
| `overlay.py` | Gaze + cursor overlay rendering (`extract_trial_gaze`, `make_overlay`) |
| `generate_overlays.py` | Overlay orchestration script — run manually after session: `python generate_overlays.py <pid> [data_dir]` |
| `screen_recorder.py` | mss + ffmpeg screen capture |
| `cursor_recorder.py` | Mock mode: record only mouse cursor (no GP3) |

## EXPERIMENT_MODE

```python
EXPERIMENT_MODE = 'pd'          # единственный режим
```

`_run_experiment()` вызывает `_run_pd_experiment()`.

## PD Experiment Flow

```
calibrate → instructions → practice(cond1, ds1) →
run_pd_block(1, cond1, ds1) → nasa_tlx/1 → break + recalibrate →
run_pd_block(2, cond2, ds2) → nasa_tlx/2 → post_interview →
[optional] run_paintings_phase() → post_process_paintings() → experiment_done

# Оверлеи — отдельно, после завершения записи:
python generate_overlays.py <participant_id> [data_dir]
```

> `post_process_paintings()` генерирует только хитмапы/траектории (не оверлей).
> `post_process_pd()` сохранён в коде, но не вызывается автоматически.

> Предварительный опрос (демография, навыки) собирается на бумаге — соответствующий экран убран.

**Counterbalancing (2×2, независимые оси):**

| PD_GROUP | PD_DATASET_ORDER | Block 1 | Block 2 |
|---|---|---|---|
| 1 | 1 | flat+alpha | sections+beta |
| 1 | 2 | flat+beta | sections+alpha |
| 2 | 1 | sections+alpha | flat+beta |
| 2 | 2 | sections+beta | flat+alpha |

- `PD_GROUP` — порядок **условий** (flat/sections)
- `PD_DATASET_ORDER` — порядок **датасетов** (alpha/beta), независим от PD_GROUP

**Per block (run_pd_block):**
```
block_intro → start_recording + GP3 → PD_BLOCK_START_B{n}_{COND}_{DS}
  for each task_id:
    ITI baseline (PD_ITI_START/END) → /pd/console → PD_TASK_START →
    wait PD_TASK_DONE → PD_TASK_END
PD_BLOCK_END_B{n} → stop recording
```

## Gaze Markers (USER column of TSV)

```python
tracker.log("MARKER")      # direct (while recording is running)
_safe_log("MARKER")        # with thread timeout (baseline/transition)
_log_event("name","phase") # → events CSV only, not TSV
```

**PD markers:**
```
PD_BLOCK_START_B{n}_{COND}_{DS}   e.g. PD_BLOCK_START_B1_FLAT_ALPHA
  PD_ITI_START_{task_id}
  PD_ITI_END_{task_id}
  PD_TASK_START_{task_id}
  PD_TASK_END_{task_id}
PD_BLOCK_END_B{n}
```

**Paintings markers:**
```
PAINTINGS_PHASE_START → PAINTING_DISPLAYED_{name} → SPACEBAR_{name}_{N} →
PAINTING_VIEWED_{name} → PAINTING_RATED_{name}_{N} → PAINTINGS_PHASE_END
```

## Config Flags (check before every real session)

```python
EXPERIMENT_MODE = 'pd'        # единственный режим
EYE_TRACKER_MOCK = False      # True = skip GP3 (dev only)
CURSOR_ONLY_MODE = False       # True = cursor_recorder instead of GP3
RECORD_SCREEN = True
PD_GROUP = 1                  # 1 or 2 — condition order (flat/sections)
PD_DATASET_ORDER = 1          # 1 or 2 — dataset order (alpha/beta), independent of PD_GROUP
PD_TASK_ORDER_INDEX = 0       # 0–3 (which valid_order from tasks.json)
PD_ITI_DURATION = 2.0         # inter-task baseline seconds
PD_SKIP_BLOCKS = False        # True = skip PD blocks (dev only)
BASELINE_DURATION = 2.0       # fixation cross duration (seconds)
GP3_HOST = '127.0.0.1'        # must match Gazepoint Settings → Control Address
GP3_PORT = 4242
```

> ⚠️ **config.py is compiled into the exe** — values are frozen at build time.
> The config.py file next to the exe is ignored.
> To change flags without rebuilding, create **`flags.txt`** next to the exe:
> ```
> EYE_TRACKER_MOCK=false
> CURSOR_ONLY_MODE=false
> PD_SKIP_BLOCKS=false
> PD_GROUP=2
> PD_DATASET_ORDER=1
> PD_TASK_ORDER_INDEX=1
> ```
> Boolean flags: `true`/`false`/`1`/`0`/`yes`/`no`. Integer flags: bare number.
> If file is absent — all flags use values from config.py (production defaults).

> ⚠️ gaze TSV with only `TIME/CX/CY/USER` columns (no FPOGX) = `CURSOR_ONLY_MODE` was active.

## Flask Routes (key)

| Route | Template / Notes |
|-------|-----------------|
| `/pd/console` | `pd_console.html`; injects CONDITION, DATASET, TASK as JSON |
| `/pd/nasa_tlx/<n>` | `pd_nasa_tlx.html` |
| `/pd/break` | `pd_break.html` |
| `/pd/post_interview` | `pd_post_interview.html` |
| `/baseline?duration=` | `baseline_fixation.html`; float seconds |
| `/event` POST | JSON intake → thread-safe queue; 204 response |
| `/pd/instructions` | `pd_instructions.html` |
| `/pd/block_intro/<n>` | `pd_block_intro.html` |

Event queue API: `get_events()`, `peek_events()`, `pop_event(name, page_filter)`, `pop_all_events(name, page_filter)`, `drain_page_events(page)` → removes & returns all events from a given page (used after each PD task to flush pd_console events to interactions.jsonl)

## JS Events (PD)

`PD_TASK_DONE` is consumed by `_wait_for_event()` in `run_pd_block()`; all other
`pd_console` events are drained after each task via `drain_page_events('pd_console')`
and written to `pd_<id>_b{N}_interactions.jsonl` (one JSON object per line).

| Event | Key Payload | Saved to |
|-------|-------------|----------|
| `PD_TASK_START` | task_id, task_index, task_type, condition, dataset, block_num | interactions.jsonl |
| `PD_TASK_DONE` | task_id, completion_time_ms, click_count | events CSV (as PD_TASK_END…) |
| `PD_SIDEBAR_CLICK` | resource_type, task_id | interactions.jsonl |
| `PD_RESOURCE_OPEN` | resource_id, task_id | interactions.jsonl |
| `PD_SECTION_TOGGLE` | resource_id, section_name, new_state ('open'/'closed') | interactions.jsonl |
| `PD_BREADCRUMB_CLICK` | target, task_id | interactions.jsonl |
| `PD_AOI_SNAPSHOT` | task_id, aois[]{name,x,y,w,h,visible}, screen_w, screen_h | interactions.jsonl |
| `PD_NASA_TLX_RATINGS_DONE` | block_num, scores:{mental,temporal,performance,effort,frustration} | events CSV |
| `PD_NASA_TLX_DONE` | block_num, scores:{...5 dims}, weights:{...5 dims, sum=10}, pairs:{mental_vs_temporal:'mental',...} | NASA-TLX JSON |

Base events (all pages via experiment.js): `PAGE_LOADED`, `CURSOR_IDLE`

## Dataset Structure (static/data/)

**dataset_alpha.json** — E-commerce platform (api-prod-*, postgres-*, lb-api-*)
**dataset_beta.json** — Fintech platform (gateway-*, mysql-*, batch-worker-*)

```json
{
  "name": "alpha",
  "resources": {
    "vms": [{ "id": "...", "status": "...", "key_info": "...", "region": "...",
              "sections": { "General": {}, "Networking": {}, "Disks": {},
                            "Security": {}, "Monitoring": {}, "Metadata": {} } }],
    "dbs": [...], "firewall": [...], "loadbalancers": [...], "networks": [...]
  }
}
```

Embedded tables use `__table_NAME__` key prefix: `{ label, headers[], rows[[]] }`

**tasks.json:**
```json
{
  "alpha": [6 tasks: A1-A6 (2 Lookup, 2 Comparison, 2 Diagnosis)],
  "beta":  [6 tasks: B1-B6],
  "practice": { "alpha": {...}, "beta": {...} },
  "valid_orders": [[0,2,4,1,3,5], [1,3,5,0,2,4], [2,4,0,3,5,1], [4,0,2,5,1,3]]
}
```

## Data Output per Participant

```
data/
├─ gaze_<id>_<datetime>.tsv              # GP3 stream + markers in USER column
├─ events_<id>_<datetime>.csv            # all orchestrator events
│
├─ (PD blocks)
│  ├─ screen_<id>_pd_b{1,2}_<cond>_<ds>_*.mp4
│  ├─ screen_<id>_pd_b{1,2}_<cond>_<ds>_*_overlay.mp4
│  ├─ gaze_<id>_pd_b{1,2}_trial.tsv        # extracted per-block gaze
│  ├─ pd_<id>_b{1,2}_interactions.jsonl    # JS interaction events with full payload
│  ├─ pd_<id>_metrics.xlsx                  # per-task metrics (run analysis_pd.py)
│  ├─ pd_<id>_nasa_tlx_b{1,2}_*.json   # {scores:{5 dims}, weights:{5 dims, sum=10}, pairs:{10 keys}}
│  └─ pd_<id>_interview_*.json
│
└─ (paintings phase)
   ├─ screen_<id>_paintings_*.mp4 + *_overlay.mp4
   ├─ paintings_<id>_order_*.csv
   ├─ paintings_<id>_ratings_*.csv
   ├─ paintings_<id>_bboxes_*.csv        # columns: filename,img_x,img_y,img_w,img_h,screen_w,screen_h,fit_mode
   ├─ paintings_<id>_metrics.xlsx
   ├─ paintings_<id>_fixations.xlsx
   ├─ paintings_<id>_heatmaps/
   ├─ paintings_<id>_trajectories/
   └─ paintings_<id>_gaze/<painting>.tsv
```

## Architecture Notes

- **PD analysis pipeline**:
  1. `generate_overlays.py <pid> [data_dir]` — gaze overlay videos (run manually after session)
  2. `analysis_pd.py <pid> <data_dir>` — per-participant metrics → `pd_<id>_metrics.xlsx` (per_task/per_block/summary/nasa_tlx sheets). Uses `PD_TASK_START/END` and `PD_ITI_START/END` markers from block TSV. Static AOI: task bar (y < 56/1080), sidebar (x < 72/1920), content (rest).
  3. `pd_analysis.py` (or `pd_analysis.ipynb`) — **cross-participant group analysis**: loads all `gaze_*_pd_b{1,2}_trial.tsv` directly via `analyze_participant()`, computes mean ET metrics by condition (flat/sections) and by task (A1–A6, B1–B6); scores SAN (before/after, 3 subscales С/А/Н), STAI (total 20–80), NASA-TLX (weighted/unweighted per block); Spearman r between NASA-TLX subscales and ET metrics at participant×block level (N≤18); pupil-over-time plots per participant (3×4 grid, 12 tasks) and summary per task. Saves `data/pd_group_summary.xlsx` (8 sheets) + PNG files. Run from project root: `python pd_analysis.py`.
  4. `group_analysis_pd.py <data_dir>` — Wilcoxon paired tests flat vs sections → `group_results_pd/report.xlsx`.
- **b2 end marker fallback**: `extract_trial_gaze()` now accepts `fallback_to_eof=True` — if `PD_BLOCK_END_B{n}` is missing from gaze TSV (race with `stop_recording()`), extraction continues to EOF. A 0.15 s sleep was added before `stop_recording()` to prevent future occurrences.
- **pywebview owns main thread**: Flask + experiment logic run in daemon threads; navigation via `window.load_url()`.
- **PyInstaller bundles**: `experiment.spec` (основной, portable folder) and `experiment_onedir.spec` (альтернативный). `frozen_utils.py` resolves resource paths inside bundle.
- **pd_console conditions**: `flat` = all sections always visible; `sections` = collapsible accordion (General open by default, reset on resource change).
- **Coordinate transform (paintings)**: `screen_to_image_coords()` in `analysis_paintings.py` corrects for `object-fit: contain` letterboxing — `getBoundingClientRect()` returns CSS element box (full screen), not actual content area. Fix: natural image dims (img_w/img_h from PIL) are used to recompute the real content rect. bbox CSV now includes `fit_mode` column (`'contain'` default | `'fill'` for calib painting). Calib painting uses `object-fit: fill` (CSS) so circles land at true screen corners.
- **AOI snapshots**: `reportAoiSnapshot()` captures `getBoundingClientRect()` of all `[data-aoi]` elements; fires after render + 200ms scroll debounce + section toggle.
- **start_recording() re-enables all ENABLE_SEND_* flags** (not just ENABLE_SEND_DATA) — some GP3 firmware versions reset these flags after `calibrate_show(False)`; re-enabling on every recording start prevents empty FPOGX/FPOGY columns after multiple calibrations.
