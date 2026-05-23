# ===== config.py =====
# Конфигурация эксперимента

import os

from frozen_utils import get_base_dir, get_resource_dir, is_frozen

# Тайминги
CURSOR_IDLE_THRESHOLD = 10000  # мс (JavaScript)

# Gazepoint (должны совпадать с Control Address и Control Port в Gazepoint Settings)
GP3_HOST = '127.0.0.1'
GP3_PORT = 4242

# Flask
FLASK_HOST = '127.0.0.1'
FLASK_PORT = 5000

# Видео
RECORD_SCREEN = True
VIDEO_FPS = 30
VIDEO_MONITOR = 1

# Оверлей взгляда
# True  -> рисовать невалидные gaze-точки отдельным цветом (жёлтым)
# False -> рисовать только валидные gaze-точки
OVERLAY_DRAW_INVALID_GAZE = True

# Калибровка
MAX_CALIBRATION_ERROR = 40.0   # допустимая средняя ошибка (в единицах GP3)

# Папка для данных (при frozen exe — рядом с exe)
DATA_DIR = os.path.join(get_base_dir(), 'data') if is_frozen() else 'data'

# Режим отладки без айтрекера (True = не подключаться к GP3, не калибровать)
EYE_TRACKER_MOCK = True

# Режим проверки оверлея: без айтрекера, пишем только курсор мыши (TIME, CX, CY, USER)
CURSOR_ONLY_MODE = True

# ---------- переопределение флагов из внешнего файла ----------
# При запуске exe рядом с ним можно положить файл `flags.txt` такого вида:
#   EYE_TRACKER_MOCK=true
#   CURSOR_ONLY_MODE=false
#   PD_SKIP_BLOCKS=false
# Строки с '#' игнорируются. Файл читается каждый раз при старте.
def _read_flags_file():
    import os as _os
    from frozen_utils import get_base_dir as _gbd
    path = _os.path.join(_gbd(), 'flags.txt')
    if not _os.path.isfile(path):
        return {}
    result = {}
    with open(path, encoding='utf-8') as _f:
        for line in _f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                k, _, v = line.partition('=')
                raw = v.strip()
                try:
                    result[k.strip().upper()] = int(raw)
                except ValueError:
                    result[k.strip().upper()] = raw.lower() in ('1', 'true', 'yes')
    return result

_flags = _read_flags_file()
if 'EYE_TRACKER_MOCK' in _flags:
    EYE_TRACKER_MOCK = _flags['EYE_TRACKER_MOCK']
if 'CURSOR_ONLY_MODE' in _flags:
    CURSOR_ONLY_MODE = _flags['CURSOR_ONLY_MODE']

# === Фаза 2: Картины ===
# При frozen exe — из бандла; иначе — относительный путь
PAINTINGS_DIR = os.path.join(get_resource_dir(), 'paintings') if is_frozen() else 'paintings'
PAINTING_DISPLAY_TIME = 15.0          # секунд (None = до нажатия клавиши)
PAINTING_FEEDBACK_TIME = 2.0          # секунд фиксационного креста после картины (сбор обратной связи)
PAINTING_CORNER_CALIBRATION = True    # калибровочная проверка перед картинами

# Калибровочная картина (проверка точности айтрекера)
CALIB_PAINTING_FILE = os.path.join(get_resource_dir(), 'calib_painting.jpg') if is_frozen() else 'calib_painting.jpg'
CALIB_PAINTING_DISPLAY_TIME = 15.0    # секунд на просмотр калибровочной картины

# Baseline
BASELINE_DURATION = 2.0               # секунд (фиксация на кресте перед trial)

# ============================================================
# Фаза 3: Progressive Disclosure (PD) эксперимент
# ============================================================

EXPERIMENT_MODE = 'pd'

# Counterbalancing group (1 or 2) — controls CONDITION order:
#   Group 1: Block1 = Flat,     Block2 = Sections
#   Group 2: Block1 = Sections, Block2 = Flat
PD_GROUP = 1

# Dataset order (1 or 2) — controls DATASET assignment, independent of PD_GROUP:
#   1: Block1 gets Alpha, Block2 gets Beta
#   2: Block1 gets Beta,  Block2 gets Alpha
PD_DATASET_ORDER = 1

# Индекс порядка заданий (0–3), ссылается на valid_orders в tasks.json
PD_TASK_ORDER_INDEX = 0

# Длительность межзадачного baseline ITI (секунд)
PD_ITI_DURATION = 2.0

# Пропустить PD-блоки (для тестовых запусков — сразу к фазе картин)
PD_SKIP_BLOCKS = False
if 'PD_SKIP_BLOCKS' in _flags:
    PD_SKIP_BLOCKS = _flags['PD_SKIP_BLOCKS']
if 'PD_GROUP' in _flags:
    PD_GROUP = int(_flags['PD_GROUP'])
if 'PD_TASK_ORDER_INDEX' in _flags:
    PD_TASK_ORDER_INDEX = int(_flags['PD_TASK_ORDER_INDEX'])
if 'PD_DATASET_ORDER' in _flags:
    PD_DATASET_ORDER = int(_flags['PD_DATASET_ORDER'])
