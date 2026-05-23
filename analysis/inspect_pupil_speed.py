#!/usr/bin/env python3
"""inspect_pupil_speed.py — CLI-врапер над pupil_preprocessing.calibrate_from_data.

Запуск:
    python inspect_pupil_speed.py [DATA_DIR]
    python inspect_pupil_speed.py data/ --current-thr 0.5 --method auto

Эквивалент в Python:
    from pupil_preprocessing import calibrate_from_data
    cal = calibrate_from_data('data/', save_plot='data/pupil_speed.png',
                               current_thr=0.5, method='auto')
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pupil_preprocessing import calibrate_from_data, DEFAULT_PARAMS


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('data_dir', nargs='?', default='data',
                   help='путь к директории с gaze_*.tsv (по умолчанию: data)')
    p.add_argument('--current-thr', type=float,
                   default=DEFAULT_PARAMS['speed_thr_px_per_sample'],
                   help='текущий порог для сравнения')
    p.add_argument('--method', choices=['p99', 'p99.5', 'p99.9', 'auto'],
                   default='auto', help='метод выбора порога')
    p.add_argument('--out-png', default=None,
                   help='путь к PNG (по умолчанию: <data_dir>/pupil_speed_distribution.png)')
    args = p.parse_args()

    out_png = args.out_png or os.path.join(args.data_dir, 'pupil_speed_distribution.png')

    print(f'Анализ pupil speed в: {args.data_dir}')
    print(f'Текущий порог: {args.current_thr},  метод: {args.method}\n')

    cal = calibrate_from_data(
        args.data_dir,
        save_plot=out_png,
        current_thr=args.current_thr,
        method=args.method,
        verbose=True,
    )

    print('\n' + '=' * 60)
    print(f'ИТОГ: recommended_thr = {cal["recommended_thr"]:.3f}')
    print('=' * 60)
    print('\nЕсли распределение очевидно бимодальное на графике —')
    print('используйте рекомендованный порог. Иначе сравните с физиологией:')
    print('  ~ 1 пкс/сэмпл при baseline 40 пкс ≈ 0.4 мм/с реальной дилятации.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
