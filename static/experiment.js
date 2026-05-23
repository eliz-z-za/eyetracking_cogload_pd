// ===== experiment.js =====
// Модуль связи страницы-стимула с Python-оркестратором

const EVENTS_URL = '/event';

// Отправка события на Flask-сервер
function sendEvent(eventName, extra = {}) {
    const payload = {
        event: eventName,
        timestamp: performance.now(),
        url: window.location.pathname,
        ...extra
    };

    fetch(EVENTS_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    }).catch(() => {});
}

// ===== Idle-детектор курсора =====
let idleTimer = null;
const IDLE_THRESHOLD_MS = 10000; // 10 секунд

function startIdleDetection() {
    function resetTimer() {
        clearTimeout(idleTimer);
        idleTimer = setTimeout(() => {
            sendEvent('CURSOR_IDLE', { idle_ms: IDLE_THRESHOLD_MS });
        }, IDLE_THRESHOLD_MS);
    }
    
    document.addEventListener('mousemove', resetTimer);
    document.addEventListener('mousedown', resetTimer);
    document.addEventListener('keydown', resetTimer);
    document.addEventListener('scroll', resetTimer);
    
    // Запускаем таймер сразу
    resetTimer();
}

// ===== Событие загрузки страницы =====
window.addEventListener('load', () => {
    const pageName = document.body.dataset.pageName || 'unknown';
    sendEvent('PAGE_LOADED', { page: pageName });
    startIdleDetection();
});
