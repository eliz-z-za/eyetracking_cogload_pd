# eyetracking_cogload_pd

Эксперимент по когнитивной нагрузке (Progressive Disclosure UI) с записью взгляда через **Gazepoint GP3**, окулографическим оверлеем поверх скринкаста и групповым анализом метрик зрачка / фиксаций / саккад.

Под капотом: Python 3.12 + Flask + pywebview (встроенный Chromium) + TCP-обёртка над OpenGaze API.

---

## Что это вообще такое

Участник сидит перед монитором, GP3 светит ему в лицо невидимым ИК, а наш Flask-сервер крутит ему интерфейс из двух условий:

- **flat** — все секции открыты, листай и читай;
- **sections** — аккордеон, кликай чтобы развернуть.

Между блоками — NASA-TLX и пересборка калибровки. После — фаза картин (если включена). На выходе — куча TSV/CSV/JSON/MP4 в [data/](data/), которые потом перемалываются в Excel-таблицы и графики.

Полный конспект (markers, события, поток данных) лежит в [doc/CONTEXT.md](doc/CONTEXT.md) — там всё ужато до одного экрана.

---

## Где что искать

### Запуск и оркестрация

| Файл | Что делает |
|---|---|
| [experiment.py](experiment.py) | Главный дирижёр. Класс `Experiment`, `run()`, цикл блоков, calibrate → instructions → blocks → nasa_tlx → paintings |
| [server.py](server.py) | Flask: маршруты страниц, thread-safe очередь событий из JS |
| [config.py](config.py) | Все флаги. `EYE_TRACKER_MOCK`, `PD_GROUP`, `PD_DATASET_ORDER`, тайминги, пути |
| [frozen_utils.py](frozen_utils.py) | Резолв путей для PyInstaller-сборки |
| [flags.txt](flags.txt) | Подмена флагов рядом с собранным exe (т.к. `config.py` вкомпилен) |

### Глаз и экран

| Файл | Что делает |
|---|---|
| [PyOpenGaze/opengaze.py](PyOpenGaze/opengaze.py) | Python-обёртка над OpenGaze API (TCP, 4242) — оригинал [Edwin Dalmaijer / PyOpenGaze](https://github.com/esdalmaijer/PyOpenGaze) |
| [PyOpenGaze/README.md](PyOpenGaze/README.md) | Что такое PyOpenGaze (короткое описание) |
| [PyOpenGaze/example/](PyOpenGaze/example/) | Минимальный пример из апстрима |
| [screen_recorder.py](screen_recorder.py) | mss + ffmpeg — захват экрана в mp4 |
| [cursor_recorder.py](cursor_recorder.py) | Mock-режим: пишет только курсор мыши (если GP3 нет под рукой) |
| [overlay.py](overlay.py) | Рисует gaze + cursor поверх записанного видео |

Подробности про сам трекер и его API:

- **Gazepoint OpenGaze API (GP3)** — [doc/Gazepoint_API_doc.md](doc/Gazepoint_API_doc.md)
- **Gazepoint Control** — [doc/Gazepoint_Control_doc.md](doc/Gazepoint_Control_doc.md)
- **OpenGaze API (источник)** — [gazept.com/dl/Gazepoint_API_v2.0.pdf](https://www.gazept.com/dl/Gazepoint_API_v2.0.pdf)

### Фронт (то, что видит участник)

| Папка | Что внутри |
|---|---|
| [templates/](templates/) | HTML-страницы — калибровка, инструкции, PD-консоль, NASA-TLX, картины |
| [static/](static/) | JS (`experiment.js`, `pd_console.js`), стили, датасеты, картины |
| [static/data/](static/data/) | `dataset_alpha.json`, `dataset_beta.json`, `tasks.json` (12 заданий + practice + valid_orders) |
| [paintings/](paintings/) | Картинки для фазы картин |

### Анализ

| Файл | Что делает |
|---|---|
| [analysis.py](analysis.py) | Общие утилиты: `load_gaze_data`, `extract_trial_data`, фиксации, зрачок, baseline |
| [analysis/analysis_pd.py](analysis/analysis_pd.py) | На одного участника: метрики PD-блоков → `pd_<id>_metrics.xlsx` |
| [analysis_paintings.py](analysis_paintings.py) | Фаза картин: хитмапы, траектории, XLSX |
| [analysis/pd_analysis.py](analysis/pd_analysis.py) | **Групповой анализ:** сводки по condition/task, шкалы SAN/STAI/NASA-TLX, корреляции Спирмена, графики зрачка во времени |
| [analysis/pd_analysis.ipynb](analysis/pd_analysis.ipynb) | То же самое, но в ноутбуке с инлайн-графиками |
| [analysis/group_analysis_pd.py](analysis/group_analysis_pd.py) | Парный Wilcoxon flat vs sections + FDR BH |
| [analysis/pupil_preprocessing.py](analysis/pupil_preprocessing.py) | Чистка зрачка (моргания, артефакты) |
| [analysis/generate_overlays.py](analysis/generate_overlays.py) | Оверлей-видео по сессии: `python analysis/generate_overlays.py <pid>` |
| [analysis/inspect_pupil_speed.py](analysis/inspect_pupil_speed.py) | Дебаг скорости зрачка |

### Документация

| Файл | Зачем |
|---|---|
| [doc/CONTEXT.md](doc/CONTEXT.md) | Главный конспект проекта — читать первым |
| [doc/analysis_doc.md](doc/analysis_doc.md) | Что считается, как считается, какие колонки в xlsx |
| [doc/Gazepoint_API_doc.md](doc/Gazepoint_API_doc.md) | TCP-команды GP3 |
| [doc/Gazepoint_Control_doc.md](doc/Gazepoint_Control_doc.md) | Gazepoint Control (приложение производителя) |
| [doc/NASA-TLX-rus.pdf](doc/NASA-TLX-rus.pdf) | Опросник на русском |
| [doc/дока.md](doc/дока.md) | Внутренние заметки |

### Скрипты для проверки железа

| Файл | Зачем |
|---|---|
| [scripts/test_connection.py](scripts/test_connection.py) | Проверить, что GP3 отвечает по TCP |
| [scripts/test_calibration.py](scripts/test_calibration.py) | Прогнать калибровку отдельно |
| [scripts/test_screen_recorder.py](scripts/test_screen_recorder.py) | Проверить mss+ffmpeg |

### Сборка

| Файл | Зачем |
|---|---|
| [experiment.spec](experiment.spec) | PyInstaller, portable folder |
| [experiment_onedir.spec](experiment_onedir.spec) | Альтернативная сборка onedir |
| [build_exe.bat](build_exe.bat) / [build_exe.ps1](build_exe.ps1) | Запускалки сборки под Windows |
| [runtime_hook_startup_log.py](runtime_hook_startup_log.py) | Хук для логов при старте exe |

---

## Быстрый старт

```bash
# 1. Окружение
python -m venv .venv
source .venv/bin/activate           # на Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Запустить Gazepoint Control (включить трекер), либо
#    выставить в config.py:
#      EYE_TRACKER_MOCK = True       # без железа
#      CURSOR_ONLY_MODE = True       # курсор вместо взгляда

# 3. Поехали
python experiment.py
```

Данные пишутся в [data/](data/) под именами вида `gaze_<id>_<datetime>.tsv`, `events_<id>_<datetime>.csv`, `screen_<id>_pd_b1_*.mp4` и т.д. (полный список — в [doc/CONTEXT.md](doc/CONTEXT.md#data-output-per-participant)).

После сессии:

```bash
python analysis/generate_overlays.py <participant_id>
python analysis/analysis_pd.py <participant_id> data
python analysis/pd_analysis.py            # групповой анализ по всем
python analysis/group_analysis_pd.py data # Wilcoxon flat vs sections
```

---

## PyOpenGaze: откуда

Папка [PyOpenGaze/](PyOpenGaze/) — слегка адаптированная копия библиотеки **Edwin Dalmaijer**'a:

- Репозиторий: <https://github.com/esdalmaijer/PyOpenGaze>
- Лицензия апстрима: [PyOpenGaze/LICENSE](PyOpenGaze/LICENSE)
- Класс `OpenGazeTracker` — TCP-клиент к серверу Gazepoint (по умолчанию `127.0.0.1:4242`), методы `calibrate_start()`, `start_recording()`, `log()`, `sample()` и т.д.

Если хочется просто пощупать API — посмотрите [PyOpenGaze/example/](PyOpenGaze/example/) или прочитайте `opengaze.py` подряд: он линейный.

---

## Стек одной строкой

`Python 3.12` · `Flask 3.0` · `pywebview` · `numpy/pandas/scipy/statsmodels` · `matplotlib/seaborn` · `mss + imageio-ffmpeg` · `opencv-python` · `PyInstaller` · `Gazepoint GP3 + OpenGaze API`

Зависимости: [requirements.txt](requirements.txt). Версия Python: [.python-version](.python-version).
