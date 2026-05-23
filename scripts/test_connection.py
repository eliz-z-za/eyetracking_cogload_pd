import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'PyOpenGaze'))
from opengaze import OpenGazeTracker

# Путь к лог-файлу рядом со скриптом; папка data создаётся при необходимости
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)
LOGFILE = os.path.join(DATA_DIR, 'test.tsv')

# ПЕРЕД ЗАПУСКОМ: убедись, что Gazepoint Control запущен и API включён (порт 4242)!

from config import GP3_HOST, GP3_PORT

print("Подключаюсь к Gazepoint Control...")
try:
    tracker = OpenGazeTracker(ip=GP3_HOST, port=GP3_PORT, logfile=LOGFILE)
except ConnectionError as e:
    print("Ошибка подключения:", e)
    print("\nПроверь:")
    print("  1. Gazepoint Control запущен.")
    print("  2. В меню: View → API (или настройки) — сервер API включён, порт 4242.")
    print("  3. IP в скрипте совпадает с Gazepoint Settings → Control Address.")
    print("  4. Брандмауэр не блокирует соединение.")
    sys.exit(1)
except Exception as e:
    print("Ошибка:", e)
    sys.exit(1)

print("Подключение успешно!")

# Проверка: подключён ли физически айтрекер (NOCAM — сервер есть, камеры нет)
tracker_info = tracker.get_tracker_id()
has_tracker = False
if tracker_info is not None and tracker_info.get('MAX_ID', 0) > 0:
    has_tracker = True
if not has_tracker:
    product_id = tracker.get_product_id()
    if product_id is not None and str(product_id).strip():
        has_tracker = True
if not has_tracker:
    print("Сервер запущен, но айтрекер не подключён (NOCAM). Подключите камеру и перезапустите.")
    tracker.close()
    sys.exit(1)

# Запускаем запись
print("Начинаю запись данных...")
tracker.start_recording()

# Читаем 50 сэмплов (около 1 секунды на 60 Гц)
for i in range(50):
    x, y = tracker.sample()
    try:
        psize = tracker.pupil_size()
    except KeyError:
        psize = None
    gaze_str = f"({x}, {y})" if (x is not None and y is not None) else "нет данных"
    pupil_str = psize if psize is not None else "нет данных"
    print(f"  Сэмпл {i}: gaze={gaze_str}, pupil={pupil_str}")
    time.sleep(0.016)  # ~60 Гц

# Тестируем маркер
print("Отправляю тестовый маркер...")
tracker.log("TEST_MARKER")
print("Маркер отправлен!")

# Останавливаем
tracker.stop_recording()
tracker.close()
print("Готово! Проверь файл", LOGFILE)
