# ===== server.py =====
# Flask-сервер: хостит страницы-прототипы и принимает события от JS

from flask import Flask, request, render_template, jsonify, send_from_directory
import json
import os
import time
import threading

from frozen_utils import get_resource_dir, is_frozen, get_base_dir
from config import (
    FLASK_HOST, FLASK_PORT, PAINTINGS_DIR, PAINTING_DISPLAY_TIME,
    PAINTING_FEEDBACK_TIME, DATA_DIR,
    BASELINE_DURATION, CALIB_PAINTING_FILE, CALIB_PAINTING_DISPLAY_TIME,
)

# При frozen exe — шаблоны и static из бандла
if is_frozen():
    _root = get_resource_dir()
    app = Flask(__name__, template_folder=os.path.join(_root, 'templates'), static_folder=os.path.join(_root, 'static'))
else:
    app = Flask(__name__)

# Глобальная очередь событий (Thread-safe через Lock)
_events_lock = threading.Lock()
_events = []


def get_events():
    """Получить все события и очистить очередь"""
    with _events_lock:
        evts = _events.copy()
        _events.clear()
        return evts


def peek_events():
    """Посмотреть события, не удаляя их"""
    with _events_lock:
        return _events.copy()


def pop_event(event_name, page_filter=None):
    """Найти и удалить первое событие с данным именем (и страницей).
    Возвращает событие или None."""
    with _events_lock:
        for i, ev in enumerate(_events):
            if ev['event'] == event_name:
                if page_filter and ev.get('page', '') != page_filter:
                    continue
                return _events.pop(i)
        return None


def drain_page_events(page):
    """Удалить и вернуть все события с данной страницей (для записи в interactions.jsonl)."""
    with _events_lock:
        matching = [ev for ev in _events if ev.get('page') == page]
        remaining = [ev for ev in _events if ev.get('page') != page]
        _events.clear()
        _events.extend(remaining)
    return matching


def pop_all_events(event_name, page_filter=None):
    """Найти и удалить ВСЕ события с данным именем (и страницей).
    Возвращает список событий (может быть пустым)."""
    with _events_lock:
        matched = []
        remaining = []
        for ev in _events:
            if ev['event'] == event_name and (not page_filter or ev.get('page', '') == page_filter):
                matched.append(ev)
            else:
                remaining.append(ev)
        _events.clear()
        _events.extend(remaining)
        return matched


# ===== Маршруты =====

@app.route('/')
def index():
    return "Сервер работает. Ожидание запуска эксперимента."



@app.route('/baseline')
def baseline_page():
    duration = float(request.args.get('duration', BASELINE_DURATION))
    return render_template('baseline_fixation.html', duration=duration)



# ===== Маршруты: GUI-управление экспериментом =====

@app.route('/welcome')
def welcome_page():
    return render_template('welcome.html')



@app.route('/paintings_prompt')
def paintings_prompt_page():
    return render_template('paintings_prompt.html')


@app.route('/calibration_choice')
def calibration_choice_page():
    return render_template('calibration_choice.html')


@app.route('/tracker_prompt')
def tracker_prompt_page():
    return render_template('tracker_prompt.html')


@app.route('/status')
def status_page():
    title = request.args.get('title', 'Подождите...')
    message = request.args.get('message', '')
    return render_template('status.html', title=title, message=message)


@app.route('/experiment_done')
def experiment_done_page():
    files = request.args.getlist('f')
    return render_template('experiment_done.html', data_dir=DATA_DIR, files=files)


# ===== Маршруты: фаза картин =====

@app.route('/paintings/<path:filename>')
def serve_painting(filename):
    abs_dir = os.path.abspath(PAINTINGS_DIR)
    return send_from_directory(abs_dir, filename)


@app.route('/calib_painting.jpg')
def serve_calib_painting():
    return send_from_directory(os.path.dirname(os.path.abspath(CALIB_PAINTING_FILE)),
                               os.path.basename(CALIB_PAINTING_FILE))


@app.route('/painting/calib_instruction')
def painting_calib_instruction():
    return render_template('painting_calib_instruction.html')


@app.route('/painting/calib_display')
def painting_calib_display():
    return render_template(
        'painting_calib_display.html',
        display_time=CALIB_PAINTING_DISPLAY_TIME,
    )


@app.route('/painting/feedback_instruction')
def painting_feedback_instruction():
    return render_template('painting_feedback_instruction.html')


@app.route('/painting/display/<path:filename>')
def painting_display(filename):
    return render_template(
        'painting_display.html',
        filename=filename,
        display_time=PAINTING_DISPLAY_TIME,
        feedback_time=PAINTING_FEEDBACK_TIME,
    )


# ===== Маршруты: PD-эксперимент =====

@app.route('/pd/instructions')
def pd_instructions():
    return render_template('pd_instructions.html')


@app.route('/pd/practice_intro')
def pd_practice_intro():
    block_num = int(request.args.get('block_num', 1))
    return render_template('pd_practice_intro.html', block_num=block_num)


@app.route('/pd/block_intro/<int:block_num>')
def pd_block_intro(block_num):
    return render_template('pd_block_intro.html', block_num=block_num)


@app.route('/pd/console')
def pd_console():
    condition   = request.args.get('condition', 'flat')
    dataset_name = request.args.get('dataset', 'alpha')
    task_id     = request.args.get('task_id', '')
    task_index  = int(request.args.get('task_index', 1))
    task_total  = int(request.args.get('task_total', 6))
    block_num   = int(request.args.get('block_num', 1))

    data_root = app.static_folder if not is_frozen() else os.path.join(get_resource_dir(), 'static')

    with open(os.path.join(data_root, 'data', f'dataset_{dataset_name}.json'), 'r', encoding='utf-8') as f:
        dataset = json.load(f)

    with open(os.path.join(data_root, 'data', 'tasks.json'), 'r', encoding='utf-8') as f:
        all_tasks = json.load(f)

    if task_id == 'PRACTICE':
        task = all_tasks['practice'][dataset_name]
    else:
        task = next((t for t in all_tasks[dataset_name] if t['id'] == task_id), None)

    return render_template(
        'pd_console.html',
        condition=condition,
        dataset=dataset,
        task=task,
        task_index=task_index,
        task_total=task_total,
        block_num=block_num,
    )


@app.route('/pd/nasa_tlx/<int:block_num>')
def pd_nasa_tlx(block_num):
    return render_template('pd_nasa_tlx.html', block_num=block_num)


@app.route('/pd/break')
def pd_break():
    return render_template('pd_break.html')


@app.route('/pd/post_interview')
def pd_post_interview():
    return render_template('pd_post_interview.html')


@app.route('/event', methods=['POST'])
def receive_event():
    """Принимает события от JavaScript на страницах"""
    try:
        data = request.get_json(force=True)
    except Exception:
        data = json.loads(request.get_data(as_text=True))
    
    data['server_time'] = time.time()
    
    with _events_lock:
        _events.append(data)
    
    print(f"[EVENT] {data.get('event', '?')} | "
          f"page={data.get('page', '?')} | "
          f"server_time={data['server_time']:.3f}")
    
    return '', 204


def start_server():
    """Запускает Flask в фоновом потоке"""
    thread = threading.Thread(
        target=lambda: app.run(
            host=FLASK_HOST,
            port=FLASK_PORT,
            debug=False,
            use_reloader=False
        ),
        daemon=True
    )
    thread.start()
    # Подождать запуска
    time.sleep(1.0)
    print(f"[SERVER] Flask запущен на http://{FLASK_HOST}:{FLASK_PORT}")
    return thread
