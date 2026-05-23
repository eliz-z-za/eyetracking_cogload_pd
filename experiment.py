# ===== experiment.py =====
# Главный оркестратор эксперимента (GUI-версия через pywebview)

import sys
import os

# Ранний лог старта (до тяжёлых импортов) — чтобы на другом ПК видеть, где зависает
def _startup_log(msg: str) -> None:
    if getattr(sys, "frozen", False):
        try:
            path = os.path.join(os.path.dirname(sys.executable), "startup.log")
            with open(path, "a", encoding="utf-8") as f:
                import time as _t
                f.write(f"{_t.strftime('%H:%M:%S', _t.localtime())} {msg}\n")
        except Exception:
            pass

_startup_log("experiment.py: imports start")

import time
import socket
import random
import threading
import webview
import json as _json
import pandas as pd
from datetime import datetime
from typing import Any
from urllib.parse import urlencode, quote

_startup_log("experiment.py: webview/pandas imported")

# region agent log
from frozen_utils import get_base_dir, get_resource_dir, is_frozen
_script_dir = get_resource_dir() if is_frozen() else os.path.dirname(os.path.abspath(__file__))
_DLOG = os.path.join(get_base_dir(), "debug-c67b9f.log") if is_frozen() else os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug-c67b9f.log")
def _dlog(loc, msg, data=None, hid="A"):
    import time as _t; line = _json.dumps({"sessionId":"c67b9f","location":loc,"message":msg,"data":data or {},"timestamp":int(_t.time()*1000),"hypothesisId":hid})
    with open(_DLOG, "a", encoding="utf-8") as _f: _f.write(line + "\n")

def _webview_version():
    """Версия pywebview: у модуля webview нет __version__, берём из метаданных пакета."""
    try:
        import importlib.metadata
        return importlib.metadata.version("pywebview")
    except Exception:
        return getattr(webview, "__version__", "?")

_dlog("experiment.py:top", "module imported OK", {"webview_version": _webview_version()}, "A")
# endregion

_startup_log("experiment.py: loading opengaze, screen_recorder, config")

_pyopengaze_dir = os.path.join(get_resource_dir(), 'PyOpenGaze') if is_frozen() else os.path.join(os.path.dirname(os.path.abspath(__file__)), 'PyOpenGaze')
sys.path.insert(0, _pyopengaze_dir)
try:
    _startup_log("experiment.py: importing opengaze")
    from opengaze import OpenGazeTracker
    _startup_log("experiment.py: opengaze imported")

    _startup_log("experiment.py: importing screen_recorder")
    from screen_recorder import ScreenRecorder, verify_recording
    _startup_log("experiment.py: screen_recorder imported")

    _startup_log("experiment.py: importing config")
    from config import (
        GP3_HOST, GP3_PORT,
        MAX_CALIBRATION_ERROR, DATA_DIR,
        RECORD_SCREEN, VIDEO_FPS, VIDEO_MONITOR,
        EYE_TRACKER_MOCK, CURSOR_ONLY_MODE,
        PAINTINGS_DIR, PAINTING_DISPLAY_TIME, PAINTING_FEEDBACK_TIME,
        PAINTING_CORNER_CALIBRATION,
        CALIB_PAINTING_FILE, CALIB_PAINTING_DISPLAY_TIME,
        FLASK_HOST, FLASK_PORT,
        EXPERIMENT_MODE, PD_GROUP, PD_DATASET_ORDER, PD_TASK_ORDER_INDEX, PD_ITI_DURATION,
        PD_SKIP_BLOCKS,
    )
    _startup_log("experiment.py: config imported")
except Exception as _e:
    _startup_log(f"experiment.py: import failure: {_e!r}")
    try:
        import traceback as _tb
        _startup_log("experiment.py: import traceback start")
        for _line in _tb.format_exc().splitlines():
            _startup_log(_line)
        _startup_log("experiment.py: import traceback end")
    except Exception:
        pass
    raise

from server import start_server, pop_event, pop_all_events, peek_events, drain_page_events

_startup_log("experiment.py: all imports OK")

BASE_URL = f"http://{FLASK_HOST}:{FLASK_PORT}"

def _gp3_control_is_listening(host: str, port: int, timeout_sec: float = 0.35) -> bool:
    """Best-effort check that Gazepoint Control API port is reachable.

    This is used as a safety net: if CURSOR_ONLY_MODE was accidentally enabled,
    but Gazepoint Control is actually running, we prefer recording real GP3 data.
    """
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_sec):
            return True
    except Exception:
        return False


class Experiment:

    def __init__(self):
        self.window: Any = None
        self.tracker: Any = None
        self.screen_recorder = None
        self.events_log = []
        self.trial_metadata = []
        self.gaze_logfile = None
        self.painting_order = []
        self.painting_ratings = {}
        self.painting_decision_times = {}
        self.painting_bboxes = {}
        self.calib_circles = []
        self.paintings_video_meta = None

    def _navigate(self, path):
        """Navigate the pywebview window to a Flask route."""
        url = f"{BASE_URL}{path}"
        print(f"[GUI] -> {url}")
        self.window.load_url(url)

    def _safe_log(self, msg, timeout=2.0):
        """Вызов tracker.log() с таймаутом — PyOpenGaze.log() зависает, если Gazepoint не присылает сэмплы."""
        if self.tracker is None:
            return
        result: list[bool | None] = [None]
        def _do_log():
            try:
                self.tracker.log(msg)
                result[0] = True
            except Exception:
                pass
        t = threading.Thread(target=_do_log, daemon=True)
        t.start()
        t.join(timeout=timeout)
    
    def _drain_spacebar_events(self, safe_name, spacebar_list):
        """Забирает все SPACEBAR_PRESSED из очереди, логирует каждый в трекер и events_log."""
        pressed = pop_all_events('SPACEBAR_PRESSED', page_filter='painting_display')
        for ev in pressed:
            pn = ev.get('press_number', '?')
            if self.tracker is not None:
                self.tracker.log(f"SPACEBAR_{safe_name}_{pn}")
            self._log_event(f"SPACEBAR_{safe_name}_{pn}", "paintings")
            spacebar_list.append(ev)

    def _wait_for_event_with_spacebar_drain(self, event_name, safe_name, spacebar_list,
                                            page_filter=None, timeout=120):
        """Ждёт события от JS, параллельно дренируя SPACEBAR_PRESSED и логируя их в трекер."""
        start = time.time()
        while time.time() - start < timeout:
            self._drain_spacebar_events(safe_name, spacebar_list)
            events = peek_events()
            for ev in events:
                if ev['event'] == event_name:
                    if page_filter and ev.get('page', '') != page_filter:
                        continue
                    return pop_event(event_name, page_filter=page_filter)
            time.sleep(0.1)
        raise TimeoutError(
            f"Событие '{event_name}' (page={page_filter}) "
            f"не получено за {timeout}с"
        )

    def connect_tracker(self):
        """Подключение к Gazepoint GP3 (GUI)"""
        print("\n=== Подключение к Gazepoint GP3 ===")

        self._navigate('/tracker_prompt')
        self._wait_for_event('TRACKER_READY', page_filter='tracker_prompt', timeout=600)

        logfile = os.path.join(
            DATA_DIR,
            f"gaze_{self.participant_id}_{datetime.now():%Y%m%d_%H%M%S}.tsv"
        )

        self._navigate('/status?' + urlencode({
            'title': 'Подключение к айтрекеру...',
            'message': 'Подождите, идёт подключение к Gazepoint GP3',
        }))

        retry_interval = 2.0
        retry_timeout = 30.0
        deadline = time.time() + retry_timeout

        while True:
            try:
                self.tracker = OpenGazeTracker(
                    ip=GP3_HOST, port=GP3_PORT, logfile=logfile
                )
                break
            except (ConnectionRefusedError, OSError, socket.error) as e:
                if time.time() >= deadline:
                    print("\nОшибка: Gazepoint Control не запущен или не слушает порт 4242.")
                    raise
                print(f"  Не удалось подключиться: {e}")
                print(f"  Повтор через {retry_interval} с...")
                time.sleep(retry_interval)

        self.gaze_logfile = logfile
        print(f"[OK] Подключено. Лог: {logfile}")

        has_tracker = False
        product_id = self.tracker.get_product_id()
        print(f"  [DEBUG] get_product_id() => {product_id!r}")
        if product_id is not None and str(product_id).strip():
            has_tracker = True

        if not has_tracker:
            tracker_info = self.tracker.get_tracker_id()
            print(f"  [DEBUG] get_tracker_id() => {tracker_info!r}")
            if tracker_info is not None and tracker_info.get('MAX_ID', 0) > 0:
                has_tracker = True

        if not has_tracker:
            print("\nВнимание: айтрекер не подключён.")
            self._navigate('/tracker_prompt?section=no-tracker-section')
            ev = self._wait_for_event('TRACKER_SKIP', page_filter='tracker_prompt', timeout=600)
            if ev and ev.get('action') == 'continue':
                self.tracker.close()
                self.tracker = None
                print("[OK] Режим без айтрекера. Продолжаем.")
                return
            print("Завершение.")
            self.window.destroy()
            sys.exit(1)
    
    def calibrate(self):
        """Калибровка айтрекера (GUI)"""
        if self.tracker is None:
            return
        print("\n=== Калибровка ===")

        while True:
            self._navigate('/calibration_choice')
            ev = self._wait_for_event('CALIBRATION_CHOICE', page_filter='calibration_choice', timeout=600)
            method = ev.get('method', 'gui') if ev else 'gui'

            if method == 'programmatic':
                self._navigate('/status?' + urlencode({
                    'title': 'Калибровка...',
                    'message': 'Выполняется программная калибровка через PyOpenGaze',
                }))
                print("Запускаю калибровку через PyOpenGaze...")
                self.tracker.calibrate()
                ave_error, valid_points = self.tracker.calibrate_result_summary()
                print(f"  Средняя ошибка: {ave_error}")
                print(f"  Валидных точек: {valid_points}")

                if ave_error and float(ave_error) > MAX_CALIBRATION_ERROR:
                    print(f"  Ошибка выше порога ({MAX_CALIBRATION_ERROR})")
                    cal_msg = (f"Средняя ошибка: {ave_error} "
                               f"(порог: {MAX_CALIBRATION_ERROR}). "
                               f"Валидных точек: {valid_points}")
                    self._navigate('/tracker_prompt?' + urlencode({
                        'section': 'calibration-retry-section',
                        'cal_error': cal_msg,
                    }))
                    ev2 = self._wait_for_event('CALIBRATION_RETRY', page_filter='tracker_prompt', timeout=600)
                    if ev2 and ev2.get('action') == 'retry':
                        continue
                break
            else:
                self._navigate('/tracker_prompt?section=calibration-done-section')
                self._wait_for_event('CALIBRATION_DONE', page_filter='tracker_prompt', timeout=600)
                break

        print("[OK] Калибровка завершена")
    
    # ================================================================
    # Фаза 2: Показ картин
    # ================================================================

    def run_paintings_phase(self):
        """Фаза 2: показ картин с записью взгляда, экрана и оценкой."""
        print("\n" + "=" * 60)
        print("  ФАЗА 2: КАРТИНЫ")
        print("=" * 60)

        SUPPORTED_EXT = {'.jpg', '.jpeg', '.png', '.bmp', '.gif'}
        paintings = sorted([
            f for f in os.listdir(PAINTINGS_DIR)
            if os.path.splitext(f)[1].lower() in SUPPORTED_EXT
        ])
        if not paintings:
            print(f"[WARN] Папка '{PAINTINGS_DIR}' пуста или не содержит изображений.")
            return

        random.shuffle(paintings)
        self.painting_order = paintings
        print(f"Картин: {len(paintings)}, порядок рандомизирован.")

        order_path = os.path.join(
            DATA_DIR,
            f"paintings_{self.participant_id}_order_{datetime.now():%Y%m%d_%H%M%S}.csv"
        )
        pd.DataFrame({'order': range(1, len(paintings) + 1), 'filename': paintings}).to_csv(
            order_path, index=False
        )
        print(f"[OK] Порядок сохранён: {order_path}")

        # Запись экрана для фазы картин
        paintings_video = os.path.join(
            DATA_DIR,
            f"screen_{self.participant_id}_paintings_{datetime.now():%Y%m%d_%H%M%S}.mp4"
        )
        video_start_wall = None
        if RECORD_SCREEN:
            try:
                print(f"[VIDEO] Запускаю запись экрана (картины): {paintings_video}")
                import mss as _mss
                with _mss.mss() as _sct:
                    _mon = _sct.monitors[VIDEO_MONITOR]
                self.screen_recorder = ScreenRecorder()
                self.screen_recorder.start_recording(paintings_video, VIDEO_FPS, _mon)
                video_start_wall = time.time()
            except Exception as e:
                print(f"[VIDEO] Ошибка при запуске записи экрана: {e}")
                self.screen_recorder = None

        trial_start_wall = None
        if self.tracker is not None:
            self.tracker.start_recording()
            self.tracker.log("PAINTINGS_PHASE_START")
            trial_start_wall = time.time()
        self._log_event("PAINTINGS_PHASE_START", "paintings")

        painting_bboxes = {}

        # Калибровочная проверка -- тестовая картина с пронумерованными кружками
        if PAINTING_CORNER_CALIBRATION:
            print("\n--- Калибровка: проверочная картина ---")
            self._navigate("/painting/calib_instruction")
            self._wait_for_event(
                'CALIB_INSTRUCTION_DONE',
                page_filter='painting_calib_instruction',
                timeout=120,
            )

            safe_calib = os.path.basename(CALIB_PAINTING_FILE).replace(' ', '_')
            self._navigate("/painting/calib_display")
            loaded_event = self._wait_for_event(
                'CALIB_PAINTING_LOADED',
                page_filter='painting_calib_display',
                timeout=60,
            )
            if loaded_event:
                painting_bboxes[os.path.basename(CALIB_PAINTING_FILE)] = {
                    'img_x': loaded_event.get('img_x', 0),
                    'img_y': loaded_event.get('img_y', 0),
                    'img_w': loaded_event.get('img_w', 0),
                    'img_h': loaded_event.get('img_h', 0),
                    'screen_w': loaded_event.get('screen_w', 0),
                    'screen_h': loaded_event.get('screen_h', 0),
                    'fit_mode': loaded_event.get('fit_mode', 'contain'),
                }
                self.calib_circles = loaded_event.get('circles', [])
            self._safe_log(f"PAINTING_DISPLAYED_{safe_calib}")
            self._log_event(f"PAINTING_DISPLAYED_{safe_calib}", "paintings")

            timeout = int(CALIB_PAINTING_DISPLAY_TIME + 30)
            self._wait_for_event(
                'CALIB_PAINTING_VIEWED',
                page_filter='painting_calib_display',
                timeout=timeout,
            )
            self._safe_log(f"PAINTING_VIEWED_{safe_calib}")
            self._log_event(f"PAINTING_VIEWED_{safe_calib}", "paintings")
            print("[OK] Калибровочная картина просмотрена")

        # === Инструкция по обратной связи (один раз) ===
        self._navigate('/painting/feedback_instruction')
        self._wait_for_event(
            'FEEDBACK_INSTRUCTION_DONE',
            page_filter='painting_feedback_instruction',
            timeout=120,
        )
        print("[OK] Инструкция по обратной связи показана")

        # === Baseline для фазы картин ===
        self._navigate('/baseline')
        self._wait_for_event('BASELINE_READY', page_filter='baseline_fixation', timeout=120)
        self._safe_log("PAINTINGS_BASELINE_START")
        self._log_event("PAINTINGS_BASELINE_START", "paintings")

        self._wait_for_event('BASELINE_DONE', page_filter='baseline_fixation', timeout=30)
        self._safe_log("PAINTINGS_BASELINE_END")
        self._log_event("PAINTINGS_BASELINE_END", "paintings")

        for idx, filename in enumerate(paintings):
            print(f"\n--- Картина {idx + 1}/{len(paintings)}: {filename} ---")
            safe_name = filename.replace(' ', '_')

            if self.tracker is not None:
                self.tracker.log(f"PAINTING_TRIAL_START_{safe_name}")
            self._log_event(f"PAINTING_TRIAL_START_{safe_name}", "paintings")

            # Display the painting
            # region agent log
            _q = len(peek_events())
            _dlog("experiment.py:paintings_loop", f"painting {idx+1}/{len(paintings)} before navigate", {"filename": filename, "queue_len": _q}, "D")
            # endregion
            self._navigate(f"/painting/display/{quote(filename)}")
            _t0 = time.time()
            loaded_event = self._wait_for_event(
                'PAINTING_LOADED', page_filter='painting_display', timeout=60,
            )
            # region agent log
            _dlog("experiment.py:paintings_loop", f"PAINTING_LOADED received for painting {idx+1}", {"wait_sec": round(time.time()-_t0, 2), "filename": filename}, "D")
            # endregion
            if loaded_event:
                painting_bboxes[filename] = {
                    'img_x': loaded_event.get('img_x', 0),
                    'img_y': loaded_event.get('img_y', 0),
                    'img_w': loaded_event.get('img_w', 0),
                    'img_h': loaded_event.get('img_h', 0),
                    'screen_w': loaded_event.get('screen_w', 0),
                    'screen_h': loaded_event.get('screen_h', 0),
                    'fit_mode': loaded_event.get('fit_mode', 'contain'),
                }
            if self.tracker is not None:
                self.tracker.log(f"PAINTING_DISPLAYED_{safe_name}")
            self._log_event(f"PAINTING_DISPLAYED_{safe_name}", "paintings")

            # Wait for painting viewed (after DISPLAY_TIME) or ArrowRight skip,
            # draining SPACEBAR_PRESSED events into the tracker log on each cycle
            spacebar_presses: list[dict] = []
            timeout = int(PAINTING_DISPLAY_TIME + PAINTING_FEEDBACK_TIME + 30) if PAINTING_DISPLAY_TIME else 600
            self._wait_for_event_with_spacebar_drain(
                'PAINTING_VIEWED', safe_name, spacebar_presses,
                page_filter='painting_display', timeout=timeout,
            )
            if self.tracker is not None:
                self.tracker.log(f"PAINTING_VIEWED_{safe_name}")
            self._log_event(f"PAINTING_VIEWED_{safe_name}", "paintings")

            # Wait for rating (sent after FEEDBACK_TIME cross phase, or immediately on ArrowRight)
            rated_event = self._wait_for_event_with_spacebar_drain(
                'PAINTING_RATED', safe_name, spacebar_presses,
                page_filter='painting_display', timeout=30,
            )
            # Drain any remaining spacebar events that arrived with/after PAINTING_RATED
            self._drain_spacebar_events(safe_name, spacebar_presses)

            rating = rated_event.get('rating', '?') if rated_event else '?'
            self.painting_ratings[filename] = rating
            if self.tracker is not None:
                self.tracker.log(f"PAINTING_RATED_{safe_name}_{rating}")
            self._log_event(f"PAINTING_RATED_{safe_name}_{rating}", "paintings")

            decision_time_ms = spacebar_presses[0].get('ms_since_display') if spacebar_presses else None
            self.painting_decision_times[filename] = decision_time_ms

            dt_str = f"{decision_time_ms:.0f}ms" if decision_time_ms is not None else "N/A"
            print(f"  Оценка: {rating}  (decision_time: {dt_str}, пробелов: {len(spacebar_presses)})")

        if self.tracker is not None:
            self.tracker.log("PAINTINGS_PHASE_END")
            self.tracker.stop_recording()
        self._log_event("PAINTINGS_PHASE_END", "paintings")

        # Останавливаем запись экрана
        video_stop_wall = None
        if self.screen_recorder is not None:
            try:
                print("[VIDEO] Останавливаю запись экрана (картины)...")
                self.screen_recorder.stop_recording()
                video_stop_wall = time.time()
                if not self.screen_recorder.was_successful():
                    print("[VIDEO] Предупреждение: ffmpeg завершился с ошибкой")
                time.sleep(1.0)
            except Exception as e:
                print(f"[VIDEO] Ошибка при остановке записи экрана: {e}")
            finally:
                self.screen_recorder = None

        if RECORD_SCREEN and paintings_video:
            if os.path.isfile(paintings_video) and os.path.getsize(paintings_video) > 0:
                ok, msg = verify_recording(paintings_video)
                if not ok:
                    print(f"[VIDEO] Проверка записи не пройдена: {msg}")
                    paintings_video = None
                else:
                    print(f"[VIDEO] Запись картин сохранена: {paintings_video}")
            else:
                print(f"[VIDEO] Файл записи НЕ создан или пуст: {paintings_video}")
                paintings_video = None

        # Сохраняем метаданные для оверлея
        sync_info = None
        if video_start_wall and trial_start_wall and video_stop_wall:
            sync_info = {
                'video_start_wall': video_start_wall,
                'trial_start_wall': trial_start_wall,
                'video_stop_wall': video_stop_wall,
            }
        self.paintings_video_meta = {
            'video_path': paintings_video,
            'sync_info': sync_info,
        }

        # Save ratings alongside order
        ratings_path = os.path.join(
            DATA_DIR,
            f"paintings_{self.participant_id}_ratings_{datetime.now():%Y%m%d_%H%M%S}.csv"
        )
        rows = [
            {
                'order': i + 1,
                'filename': f,
                'rating': self.painting_ratings.get(f, '?'),
                'decision_time_ms': self.painting_decision_times.get(f),
            }
            for i, f in enumerate(paintings)
        ]
        pd.DataFrame(rows).to_csv(ratings_path, index=False)
        print(f"\n[OK] Рейтинги сохранены: {ratings_path}")

        # Save painting bounding boxes
        self.painting_bboxes = painting_bboxes
        if painting_bboxes:
            bbox_path = os.path.join(
                DATA_DIR,
                f"paintings_{self.participant_id}_bboxes_{datetime.now():%Y%m%d_%H%M%S}.csv"
            )
            bbox_rows = [{'filename': k, **v} for k, v in painting_bboxes.items()]
            pd.DataFrame(bbox_rows).to_csv(bbox_path, index=False)
            print(f"[OK] Bounding boxes сохранены: {bbox_path}")

        print(f"[OK] Фаза картин завершена ({len(paintings)} картин)")

    def _wait_for_event(self, event_name, page_filter=None, timeout=120):
        """Ждёт события от JS. Возвращает dict события."""
        start = time.time()
        while time.time() - start < timeout:
            events = peek_events()
            for ev in events:
                if ev['event'] == event_name:
                    # Если есть фильтр по странице
                    if page_filter and ev.get('page', '') != page_filter:
                        continue
                    # Нашли нужное событие — забираем его
                    return pop_event(event_name, page_filter=page_filter)
            time.sleep(0.1)
        raise TimeoutError(
            f"Событие '{event_name}' (page={page_filter}) "
            f"не получено за {timeout}с"
        )
    
    def _log_event(self, event_name, provider):
        """Записывает событие в лог для анализа"""
        self.events_log.append({
            'participant': self.participant_id,
            'provider': provider,
            'event': event_name,
            'wall_time': time.time(),
            'timestamp': datetime.now().isoformat()
        })
    
    def save_events_log(self):
        """Сохраняет лог событий в CSV"""
        filepath = os.path.join(
            DATA_DIR,
            f"events_{self.participant_id}_{datetime.now():%Y%m%d_%H%M%S}.csv"
        )
        df = pd.DataFrame(self.events_log)
        df.to_csv(filepath, index=False)
        print(f"[OK] Лог событий сохранён: {filepath}")

    def post_process_paintings(self):
        """Постобработка фазы картин: тепловые карты, траектории, XLS.
        Оверлей видео вынесен в generate_overlays.py — запускается отдельно."""
        print("\n=== Постобработка картин ===")

        has_gaze = (
            self.gaze_logfile is not None
            and os.path.isfile(self.gaze_logfile)
            and os.path.getsize(self.gaze_logfile) > 0
        )

        # --- Тепловые карты, траектории, XLS ---
        if not has_gaze:
            print("[POST-P] Нет данных взгляда — пропуск анализа картин")
            return
        if CURSOR_ONLY_MODE:
            print("[POST-P] Режим проверки курсора — пропуск анализа картин")
            return

        try:
            from analysis_paintings import analyze_paintings_participant
            analyze_paintings_participant(
                gaze_file=self.gaze_logfile,
                participant_id=self.participant_id,
                painting_order=self.painting_order,
                painting_ratings=self.painting_ratings,
                paintings_dir=PAINTINGS_DIR,
                data_dir=DATA_DIR,
                painting_bboxes=self.painting_bboxes if self.painting_bboxes else None,
                painting_decision_times=self.painting_decision_times if self.painting_decision_times else None,
            )
        except Exception as e:
            print(f"[POST-P] Ошибка при анализе картин: {e}")
            import traceback
            traceback.print_exc()

        # --- Тепловая карта и траектория для калибровочной картины ---
        calib_name = os.path.basename(CALIB_PAINTING_FILE)
        calib_bbox = self.painting_bboxes.get(calib_name) if self.painting_bboxes else None
        if os.path.isfile(CALIB_PAINTING_FILE):
            try:
                from analysis_paintings import (
                    load_gaze_data, extract_painting_trial,
                    generate_heatmap, generate_trajectory,
                )
                df = load_gaze_data(self.gaze_logfile)
                trial_df = extract_painting_trial(df, calib_name)
                if len(trial_df) > 0:
                    heatmap_dir = os.path.join(DATA_DIR, f"paintings_{self.participant_id}_heatmaps")
                    traj_dir = os.path.join(DATA_DIR, f"paintings_{self.participant_id}_trajectories")
                    os.makedirs(heatmap_dir, exist_ok=True)
                    os.makedirs(traj_dir, exist_ok=True)
                    base = os.path.splitext(calib_name)[0]
                    hm_ok = generate_heatmap(
                        trial_df, CALIB_PAINTING_FILE,
                        os.path.join(heatmap_dir, f"{base}_heatmap.png"),
                        bbox=calib_bbox,
                    )
                    tr_ok = generate_trajectory(
                        trial_df, CALIB_PAINTING_FILE,
                        os.path.join(traj_dir, f"{base}_trajectory.png"),
                        bbox=calib_bbox,
                    )
                    status = []
                    if hm_ok:
                        status.append("heatmap")
                    if tr_ok:
                        status.append("trajectory")
                    print(f"  {calib_name} (calib): {', '.join(status) if status else 'no visualizations'}")
                else:
                    print(f"  {calib_name} (calib): нет данных взгляда, пропуск")
            except Exception as e:
                print(f"[POST-P] Ошибка при анализе калибровочной картины: {e}")
                import traceback
                traceback.print_exc()

    def post_process_pd(self):
        """Create gaze/cursor overlays for PD block recordings."""
        from overlay import extract_trial_gaze, make_overlay
        if not self.gaze_logfile or not os.path.isfile(self.gaze_logfile):
            print("[POST] Нет файла взгляда для PD-оверлеев")
            return
        for trial in self.trial_metadata:
            if not str(trial.get('provider', '')).startswith('pd_block'):
                continue
            video_path = trial['video_path']
            if not video_path or not os.path.isfile(video_path):
                continue
            block_num = trial['pd_block_num']
            condition = trial['pd_condition']
            dataset   = trial['pd_dataset']
            marker_start = f"PD_BLOCK_START_B{block_num}_{condition.upper()}_{dataset.upper()}"
            marker_end   = f"PD_BLOCK_END_B{block_num}"
            trial_gaze_path = os.path.join(
                DATA_DIR,
                f"gaze_{self.participant_id}_pd_b{block_num}_trial.tsv",
            )
            try:
                extracted = extract_trial_gaze(
                    self.gaze_logfile, marker_start, marker_end, trial_gaze_path,
                    fallback_to_eof=True,
                )
                if extracted:
                    make_overlay(video_path, trial_gaze_path, sync_info=trial.get('sync_info'))
                    print(f"[POST] PD Block {block_num} оверлей создан")
                else:
                    print(f"[POST] PD Block {block_num}: гейз не извлечён (нет начального маркера)")
            except Exception as e:
                print(f"[POST] Ошибка оверлея PD Block {block_num}: {e}")

    # ================================================================
    # Фаза 3: Progressive Disclosure (PD) эксперимент
    # ================================================================

    def _save_pd_json(self, filename, data):
        """Немедленно сохраняет JSON-данные на диск (преквесционер, NASA-TLX)."""
        path = os.path.join(DATA_DIR, filename)
        with open(path, 'w', encoding='utf-8') as f:
            _json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[PD] Сохранено: {path}")

    def _load_pd_tasks(self, dataset_name):
        """Возвращает список ID заданий в нужном порядке для датасета."""
        tasks_file = os.path.join(_script_dir, 'static', 'data', 'tasks.json')
        with open(tasks_file, 'r', encoding='utf-8') as f:
            all_tasks = _json.load(f)
        task_ids = [t['id'] for t in all_tasks[dataset_name]]
        order_indices = all_tasks['valid_orders'][PD_TASK_ORDER_INDEX]
        return [task_ids[i] for i in order_indices]

    def run_pd_practice(self, condition, dataset_name, block_num=1):
        """Тренировочное задание (не записывается в трекер, нет видео)."""
        print(f"\n--- PD Practice: condition={condition}, dataset={dataset_name}, block={block_num} ---")
        self._navigate(f'/pd/practice_intro?block_num={block_num}')
        self._wait_for_event('PD_PRACTICE_INTRO_DONE', page_filter='pd_practice_intro', timeout=600)

        params = urlencode({
            'condition': condition, 'dataset': dataset_name,
            'task_id': 'PRACTICE', 'task_index': 0, 'task_total': 1, 'block_num': block_num,
        })
        self._navigate(f'/pd/console?{params}')
        self._wait_for_event('PAGE_LOADED', page_filter='pd_console', timeout=120)
        self._log_event('PD_PRACTICE_START', 'pd')

        self._wait_for_event('PD_TASK_DONE', page_filter='pd_console', timeout=600)
        self._log_event('PD_PRACTICE_END', 'pd')
        print("[OK] Тренировочное задание завершено")

    def run_pd_block(self, block_num, condition, dataset_name, task_order):
        """Один блок PD: запись трекера + экрана, 6 заданий с ITI."""
        print(f"\n{'='*50}")
        print(f"  PD Block {block_num}: {condition.upper()} | {dataset_name.upper()}")
        print(f"{'='*50}")

        # --- Экран начала блока ---
        self._navigate(f'/pd/block_intro/{block_num}')
        self._wait_for_event('PD_BLOCK_INTRO_DONE', page_filter='pd_block_intro', timeout=600)

        # --- Запись экрана ---
        video_filename = os.path.join(
            DATA_DIR,
            f"screen_{self.participant_id}_pd_b{block_num}_{condition}_{datetime.now():%Y%m%d_%H%M%S}.mp4"
        )
        video_start_wall = None
        if RECORD_SCREEN:
            try:
                import mss as _mss
                with _mss.mss() as _sct:
                    _mon = _sct.monitors[VIDEO_MONITOR]
                self.screen_recorder = ScreenRecorder()
                self.screen_recorder.start_recording(video_filename, VIDEO_FPS, _mon)
                video_start_wall = time.time()
                print(f"[VIDEO] Запись: {video_filename}")
            except Exception as e:
                print(f"[VIDEO] Ошибка запуска: {e}")
                self.screen_recorder = None

        # --- Запуск записи трекера ---
        trial_start_wall = None
        marker_block_start = f"PD_BLOCK_START_B{block_num}_{condition.upper()}_{dataset_name.upper()}"
        if self.tracker is not None:
            self.tracker.start_recording()
            self.tracker.log(marker_block_start)
            trial_start_wall = time.time()
        self._log_event(marker_block_start, f"pd_block{block_num}")

        # --- Цикл заданий ---
        _interaction_events = []  # буфер JS-событий для interactions.jsonl
        for i, task_id in enumerate(task_order):
            print(f"\n  Task {i+1}/{len(task_order)}: {task_id}")

            # ITI baseline
            self._navigate(f'/baseline?duration={PD_ITI_DURATION}')
            self._wait_for_event('BASELINE_READY', page_filter='baseline_fixation', timeout=120)
            self._safe_log(f"PD_ITI_START_{task_id}")
            self._log_event(f"PD_ITI_START_{task_id}", f"pd_block{block_num}")

            self._wait_for_event('BASELINE_DONE', page_filter='baseline_fixation', timeout=30)
            self._safe_log(f"PD_ITI_END_{task_id}")
            self._log_event(f"PD_ITI_END_{task_id}", f"pd_block{block_num}")

            # Загрузка задания
            params = urlencode({
                'condition': condition, 'dataset': dataset_name,
                'task_id': task_id,
                'task_index': i + 1, 'task_total': len(task_order),
                'block_num': block_num,
            })
            self._navigate(f'/pd/console?{params}')
            self._wait_for_event('PAGE_LOADED', page_filter='pd_console', timeout=120)

            # Маркер старта задания в поток GP3
            if self.tracker is not None:
                self.tracker.log(f"PD_TASK_START_{task_id}")
            self._log_event(f"PD_TASK_START_{task_id}", f"pd_block{block_num}")

            # Ждём «Готово» от участника
            done_ev = self._wait_for_event('PD_TASK_DONE', page_filter='pd_console', timeout=600)

            # Маркер конца задания в поток GP3
            if self.tracker is not None:
                self.tracker.log(f"PD_TASK_END_{task_id}")

            # Сохраняем поведенческие метрики в лог событий
            ct_ms    = done_ev.get('completion_time_ms', 0) if done_ev else 0
            n_clicks = done_ev.get('click_count', 0)       if done_ev else 0
            self._log_event(
                f"PD_TASK_END_{task_id}_ct{ct_ms}ms_clicks{n_clicks}",
                f"pd_block{block_num}"
            )
            print(f"  [OK] {task_id}: {ct_ms} ms, {n_clicks} clicks")

            # Сливаем накопленные JS-события (sidebar, AOI snapshots, etc.) в буфер
            _interaction_events.extend(drain_page_events('pd_console'))

        # --- Конец блока ---
        marker_block_end = f"PD_BLOCK_END_B{block_num}"
        if self.tracker is not None:
            self.tracker.log(marker_block_end)
            time.sleep(0.15)  # дать GP3 записать маркер до stop_recording()
            self.tracker.stop_recording()
        self._log_event(marker_block_end, f"pd_block{block_num}")

        # --- Сохранение JS-взаимодействий блока ---
        if _interaction_events:
            interactions_path = os.path.join(
                DATA_DIR,
                f"pd_{self.participant_id}_b{block_num}_interactions.jsonl",
            )
            try:
                with open(interactions_path, 'w', encoding='utf-8') as _f:
                    for ev in _interaction_events:
                        _f.write(_json.dumps(ev, ensure_ascii=False) + '\n')
                print(f"[PD] Interactions: {len(_interaction_events)} событий → {interactions_path}")
            except Exception as e:
                print(f"[PD] Ошибка записи interactions: {e}")

        # --- Остановка записи экрана ---
        video_stop_wall = None
        if self.screen_recorder is not None:
            try:
                self.screen_recorder.stop_recording()
                video_stop_wall = time.time()
                if not self.screen_recorder.was_successful():
                    print("[VIDEO] Предупреждение: ffmpeg завершился с ошибкой")
                time.sleep(1.0)
            except Exception as e:
                print(f"[VIDEO] Ошибка остановки: {e}")
            finally:
                self.screen_recorder = None

        # Верификация видео
        if RECORD_SCREEN and video_filename:
            if os.path.isfile(video_filename) and os.path.getsize(video_filename) > 0:
                ok, msg = verify_recording(video_filename)
                if not ok:
                    print(f"[VIDEO] Проверка не пройдена: {msg}")
                    video_filename = None
                else:
                    print(f"[VIDEO] Сохранено: {video_filename}")
            else:
                print(f"[VIDEO] Файл не создан: {video_filename}")
                video_filename = None

        # Метаданные блока для постобработки
        sync_info = None
        if video_start_wall and trial_start_wall and video_stop_wall:
            sync_info = {
                'video_start_wall': video_start_wall,
                'trial_start_wall': trial_start_wall,
                'video_stop_wall':  video_stop_wall,
            }
        self.trial_metadata.append({
            'video_path':   video_filename,
            'provider':     f"pd_block{block_num}_{condition}",
            'end_reason':   'BLOCK_COMPLETE',
            'sync_info':    sync_info,
            'pd_block_num': block_num,
            'pd_condition': condition,
            'pd_dataset':   dataset_name,
        })
        print(f"[OK] Блок {block_num} завершён")

    def _run_pd_experiment(self):
        """Полный сценарий PD-эксперимента."""
        print("\n" + "=" * 60)
        print("  PD ЭКСПЕРИМЕНТ: Progressive Disclosure")
        print("=" * 60)

        # 1. Калибровка
        self.calibrate()

        # 2. Инструкция
        self._navigate('/pd/instructions')
        self._wait_for_event('PD_INSTRUCTIONS_DONE', page_filter='pd_instructions', timeout=600)
        self._log_event('PD_INSTRUCTIONS_DONE', 'pd')

        # 3. Порядок блоков: условия и датасеты counterbalanced независимо
        # PD_GROUP        -> какое условие идёт первым (flat или sections)
        # PD_DATASET_ORDER -> какой датасет идёт первым (alpha или beta)
        if PD_GROUP == 1:
            cond1, cond2 = 'flat', 'sections'
        else:
            cond1, cond2 = 'sections', 'flat'

        if PD_DATASET_ORDER == 1:
            ds1, ds2 = 'alpha', 'beta'
        else:
            ds1, ds2 = 'beta', 'alpha'

        order1 = self._load_pd_tasks(ds1)
        order2 = self._load_pd_tasks(ds2)

        # 4. Тренировочное задание (в условии блока 1)
        self.run_pd_practice(cond1, ds1, block_num=1)

        # 5. Блок 1
        self.run_pd_block(1, cond1, ds1, order1)

        # 6. NASA-TLX — блок 1
        self._navigate('/pd/nasa_tlx/1')
        tlx1_ev = self._wait_for_event('PD_NASA_TLX_DONE', page_filter='pd_nasa_tlx', timeout=600)
        if tlx1_ev:
            self._save_pd_json(
                f"pd_{self.participant_id}_nasa_tlx_b1_{datetime.now():%Y%m%d_%H%M%S}.json",
                {
                    'scores':  tlx1_ev.get('scores', {}),
                    'weights': tlx1_ev.get('weights', {}),
                    'pairs':   tlx1_ev.get('pairs', {}),
                }
            )
        self._log_event('PD_NASA_TLX_B1_DONE', 'pd')

        # 7. Перерыв + рекалибровка
        self._navigate('/pd/break')
        self._wait_for_event('PD_BREAK_DONE', page_filter='pd_break', timeout=600)
        self._log_event('PD_BREAK_DONE', 'pd')
        self.calibrate()

        # 8. Тренировочное задание (в условии блока 2)
        self.run_pd_practice(cond2, ds2, block_num=2)

        # 9. Блок 2
        self.run_pd_block(2, cond2, ds2, order2)

        # 10. NASA-TLX — блок 2
        self._navigate('/pd/nasa_tlx/2')
        tlx2_ev = self._wait_for_event('PD_NASA_TLX_DONE', page_filter='pd_nasa_tlx', timeout=600)
        if tlx2_ev:
            self._save_pd_json(
                f"pd_{self.participant_id}_nasa_tlx_b2_{datetime.now():%Y%m%d_%H%M%S}.json",
                {
                    'scores':  tlx2_ev.get('scores', {}),
                    'weights': tlx2_ev.get('weights', {}),
                    'pairs':   tlx2_ev.get('pairs', {}),
                }
            )
        self._log_event('PD_NASA_TLX_B2_DONE', 'pd')

        # 11. Интервью
        self._navigate('/pd/post_interview')
        interview_ev = self._wait_for_event(
            'PD_INTERVIEW_DONE', page_filter='pd_post_interview', timeout=1200
        )
        if interview_ev and interview_ev.get('notes'):
            self._save_pd_json(
                f"pd_{self.participant_id}_interview_{datetime.now():%Y%m%d_%H%M%S}.json",
                {'notes': interview_ev['notes']}
            )
        self._log_event('PD_INTERVIEW_DONE', 'pd')

        print("\n[OK] PD-эксперимент завершён")

    def _experiment_thread(self):
        """Experiment logic — runs in a background thread while pywebview owns the main thread."""
        # region agent log
        _dlog("experiment.py:_experiment_thread", "thread started", {}, "C")
        # endregion
        try:
            self._run_experiment()
        except Exception as e:
            # region agent log
            _dlog("experiment.py:_experiment_thread", "FATAL", {"error": str(e)}, "C")
            # endregion
            print(f"\n[FATAL] {e}")
            import traceback
            traceback.print_exc()

    def _run_experiment(self):
        """Полный запуск эксперимента для одного участника (GUI)"""
        print("=" * 60)
        print("  ЭКСПЕРИМЕНТ: Cognitive Load in Cloud Provider Consoles")
        print("=" * 60)

        # Создаём папку данных
        os.makedirs(DATA_DIR, exist_ok=True)

        # Ввод ID участника (через GUI)
        self._navigate('/welcome')
        ev = self._wait_for_event('PARTICIPANT_ID', page_filter='welcome', timeout=600)
        self.participant_id = ev.get('participant_id', '').strip() if ev else ''
        if not self.participant_id:
            print("Ошибка: не введён ID участника")
            return
        print(f"[OK] ID участника: {self.participant_id}")

        # Подключение к GP3 / режим проверки / отладка
        cursor_only_requested = bool(CURSOR_ONLY_MODE)
        gp3_reachable = _gp3_control_is_listening(GP3_HOST, GP3_PORT)

        if cursor_only_requested and not gp3_reachable:
            print("\nРежим проверки оверлея: запись только курсора мыши, айтрекер не используется.")
            self.gaze_logfile = os.path.join(
                DATA_DIR,
                f"gaze_{self.participant_id}_{datetime.now():%Y%m%d_%H%M%S}.tsv"
            )
            from cursor_recorder import CursorRecorder
            self.tracker = CursorRecorder(self.gaze_logfile)
        elif EYE_TRACKER_MOCK:
            print("\nРежим отладки: айтрекер отключён, данные взгляда записываться не будут.")
            self.tracker = None
        else:
            if cursor_only_requested and gp3_reachable:
                print(
                    "\n[INFO] CURSOR_ONLY_MODE=True, но Gazepoint Control доступен — "
                    "переключаюсь на запись реальных данных GP3."
                )
            self.connect_tracker()
            self.calibrate()

        # ---- Ветвление по режиму эксперимента ----
        if EXPERIMENT_MODE == 'pd':
            if PD_SKIP_BLOCKS:
                print("\n[SKIP] PD-блоки пропущены (PD_SKIP_BLOCKS=True)")
            else:
                self._run_pd_experiment()

            # Опциональная фаза картин (аналогично не-PD ветке)
            self._navigate('/paintings_prompt')
            ev = self._wait_for_event('PAINTINGS_DECISION', page_filter='paintings_prompt', timeout=600)
            if ev and ev.get('run_paintings'):
                self.calibrate()
                self.run_paintings_phase()

            # Закрываем трекер
            if self.tracker is not None:
                self.tracker.close()
                self.tracker = None
            self.save_events_log()

            # Постобработка: хитмапы и траектории картин
            if self.painting_order:
                self.post_process_paintings()

            print("\n" + "=" * 60)
            print("  ЭКСПЕРИМЕНТ ЗАВЕРШЁН")
            print("=" * 60)

            files = ["events_*.csv  (лог событий)"]
            if self.gaze_logfile and os.path.isfile(self.gaze_logfile):
                files.insert(0, "gaze_*.tsv  (сырые данные взгляда)")
            files += [
                "pd_*_nasa_tlx_*.json",
                "pd_*_interview_*.json",
                "screen_*_pd_b*.mp4  (видеозаписи блоков)",
            ]
            if self.painting_order:
                files.append(f"paintings_{self.participant_id}_*.csv  (порядок + рейтинги)")
            self._navigate('/experiment_done?' + urlencode([('f', f) for f in files]))
            self._wait_for_event('CLOSE_WINDOW', page_filter='experiment_done', timeout=3600)
            self.window.destroy()

    def run(self):
        """Создаёт fullscreen-окно pywebview и запускает эксперимент в фоновом потоке."""
        # region agent log
        _dlog("experiment.py:run", "creating window", {"base_url": BASE_URL}, "B")
        # endregion
        self.window = webview.create_window(
            'Experiment',
            url=f'{BASE_URL}/',
            fullscreen=True,
        )
        # region agent log
        _dlog("experiment.py:run", "window created, starting flask", {}, "B")
        # endregion
        start_server()
        # region agent log
        _dlog("experiment.py:run", "flask started, calling webview.start", {}, "B")
        # endregion
        webview.start(self._experiment_thread, debug=False)


if __name__ == '__main__':
    try:
        _startup_log("main: start")
        if is_frozen():
            os.makedirs(DATA_DIR, exist_ok=True)
        _startup_log("main: creating Experiment")
        exp = Experiment()
        _startup_log("main: calling exp.run()")
        exp.run()
        _startup_log("main: run() returned")
    except Exception as e:
        _startup_log(f"main: ERROR {type(e).__name__}: {e}")
        raise
