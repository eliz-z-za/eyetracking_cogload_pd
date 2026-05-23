import sys
import os as _os
sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'PyOpenGaze'))
from opengaze import OpenGazeTracker

tracker = OpenGazeTracker(ip='127.0.0.1', port=4242, logfile='data/calib_test.tsv')

print("Запускаю калибровку...")
result = tracker.calibrate()
# result — список словарей с CALX, CALY, LX, LY, LV, RX, RY, RV

print(f"Калибровка завершена. Точек: {len(result)}")
for pt in result:
    print(f"  Точка ({pt['CALX']:.2f}, {pt['CALY']:.2f}): "
          f"L_valid={pt['LV']}, R_valid={pt['RV']}")

# Получаем среднюю ошибку
ave_error, valid_points = tracker.calibrate_result_summary()
print(f"Средняя ошибка: {ave_error}, Валидных точек: {valid_points}")

tracker.close()
