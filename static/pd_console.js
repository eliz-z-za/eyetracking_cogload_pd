// ===== pd_console.js =====
// Логика интерактивной консоли PD-эксперимента

// --- Глобальное состояние ---
let currentResourceType = null;
let currentResourceId = null;
let taskStartTime = null;
let clickCounter = 0;
// Для sections-условия: множество открытых секций
let openSections = new Set(['Основные параметры']);

// Дебаунс-таймер для AOI snapshot при скролле
let scrollDebounceTimer = null;

// --- Инициализация ---
document.addEventListener('DOMContentLoaded', function () {
    taskStartTime = performance.now();
    clickCounter = 0;

    sendEvent('PD_TASK_START', {
        page: 'pd_console',
        task_id: TASK.id,
        task_index: TASK_INDEX,
        task_type: TASK.type,
        condition: CONDITION,
        dataset: DATASET.name,
        block_num: BLOCK_NUM,
        ts: taskStartTime
    });

    // Начальный показ — список ресурсов типа первого упомянутого в задании
    const initialType = getResourceType(TASK.resources[0]);
    renderResourceList(initialType);
});

// Счётчик кликов: считаем все клики в основной части интерфейса
document.addEventListener('DOMContentLoaded', function () {
    document.getElementById('main-layout').addEventListener('click', function () {
        clickCounter++;
    });
});

// --- Вспомогательные функции ---

function getResourceType(id) {
    for (const [type, list] of Object.entries(DATASET.resources)) {
        if (list.find(r => r.id === id)) return type;
    }
    return 'vms';
}

function findResource(id) {
    for (const [type, list] of Object.entries(DATASET.resources)) {
        const res = list.find(r => r.id === id);
        if (res) return { ...res, type };
    }
    return null;
}

function getTypeLabel(type) {
    const labels = {
        vms: 'Виртуальные машины',
        dbs: 'Базы данных',
        firewall: 'Правила файрвола',
        loadbalancers: 'Балансировщики нагрузки',
        networks: 'Сети'
    };
    return labels[type] || type;
}

function getTypeIcon(type) {
    const icons = { vms: '⬡', dbs: '⊞', firewall: '⊘', loadbalancers: '◎', networks: '⬢' };
    return icons[type] || '●';
}

// --- Навигация: Sidebar ---

function onSidebarClick(type) {
    sendEvent('PD_SIDEBAR_CLICK', {
        page: 'pd_console',
        resource_type: type,
        task_id: TASK.id,
        ts: performance.now()
    });
    setActiveSidebar(type);
    renderResourceList(type);
}

function setActiveSidebar(type) {
    document.querySelectorAll('.sidebar-item').forEach(el => el.classList.remove('active'));
    const item = document.querySelector(`.sidebar-item[data-type="${type}"]`);
    if (item) item.classList.add('active');
}

// --- Рендер: Список ресурсов ---

function renderResourceList(type) {
    currentResourceType = type;
    currentResourceId = null;
    openSections = new Set(['Основные параметры']);

    const resources = DATASET.resources[type] || [];

    // Breadcrumb
    document.getElementById('breadcrumb').innerHTML =
        `<span class="breadcrumb-current">CloudPanel</span>` +
        `<span class="breadcrumb-sep">&gt;</span>` +
        `<span class="breadcrumb-current">${getTypeLabel(type)}</span>`;

    setActiveSidebar(type);

    // Таблица списка
    const rows = resources.map(r => {
        const statusClass = r.status.toLowerCase();
        return `<tr class="resource-row" onclick="onResourceClick('${r.id}')">
            <td class="resource-name">${r.id}</td>
            <td><span class="status-badge status-${statusClass}">${r.status}</span></td>
            <td class="key-info">${r.key_info}</td>
            <td>${r.region}</td>
        </tr>`;
    }).join('');

    document.getElementById('content-area').innerHTML = `
        <h1 class="content-heading">${getTypeLabel(type)}</h1>
        <table class="resource-table" data-aoi="resource-list">
            <thead>
                <tr>
                    <th style="width:30%">Имя</th>
                    <th style="width:14%">Статус</th>
                    <th style="width:36%">Ключевые параметры</th>
                    <th style="width:20%">Регион</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>`;

    reportAoiSnapshot();
}

// --- Навигация: клик по ресурсу ---

function onResourceClick(resourceId) {
    sendEvent('PD_RESOURCE_OPEN', {
        page: 'pd_console',
        resource_id: resourceId,
        task_id: TASK.id,
        ts: performance.now()
    });
    renderResourceDetail(resourceId);
}

// --- Рендер: Детальная страница ресурса ---

function renderResourceDetail(resourceId) {
    currentResourceId = resourceId;
    openSections = new Set(['Основные параметры']); // Сброс: только Основные параметры открыта

    const resource = findResource(resourceId);
    if (!resource) return;

    const typeLabel = getTypeLabel(resource.type);

    // Breadcrumb с кликабельным типом
    document.getElementById('breadcrumb').innerHTML =
        `<span class="breadcrumb-current">CloudPanel</span>` +
        `<span class="breadcrumb-sep">&gt;</span>` +
        `<span class="breadcrumb-link" onclick="onBreadcrumbClick('${resource.type}')">${typeLabel}</span>` +
        `<span class="breadcrumb-sep">&gt;</span>` +
        `<span class="breadcrumb-current">${resourceId}</span>`;

    setActiveSidebar(resource.type);

    // Заголовок ресурса
    const statusClass = resource.status.toLowerCase();
    const headerHtml = `
        <div class="resource-header">
            <span class="resource-type-icon">${getTypeIcon(resource.type)}</span>
            <h1 class="resource-id">${resourceId}</h1>
            <span class="status-badge status-${statusClass}">${resource.status}</span>
        </div>`;

    // Секции: «Основные параметры» всегда первыми, остальные в оригинальном порядке
    const sectionEntries = Object.entries(resource.sections);
    sectionEntries.sort((a, b) => {
        if (a[0] === 'Основные параметры') return -1;
        if (b[0] === 'Основные параметры') return 1;
        return 0;
    });
    const sectionsHtml = sectionEntries.map(([sectionName, sectionData]) => {
        const isOpen = openSections.has(sectionName);
        const contentHtml = renderSectionContent(sectionData, sectionName);

        if (CONDITION === 'flat') {
            return `
                <div class="section-block" data-section="${sectionName}">
                    <div class="section-header-flat"
                         data-aoi="section-header-${sectionName}">${sectionName}</div>
                    <div class="section-content"
                         data-aoi="section-content-${sectionName}">${contentHtml}</div>
                </div>`;
        } else {
            const chevron = isOpen ? '▾' : '▸';
            const openClass = isOpen ? ' open' : '';
            const collapsedClass = isOpen ? '' : ' collapsed';
            return `
                <div class="section-block" data-section="${sectionName}">
                    <button class="section-header-toggle${openClass}"
                            data-aoi="section-header-${sectionName}"
                            onclick="toggleSection('${sectionName}')">
                        <span class="chevron">${chevron}</span>${sectionName}
                    </button>
                    <div class="section-content${collapsedClass}"
                         data-aoi="section-content-${sectionName}">${contentHtml}</div>
                </div>`;
        }
    }).join('');

    document.getElementById('content-area').innerHTML = headerHtml + sectionsHtml;
    document.getElementById('content-area').scrollTop = 0;

    // Debounced AOI snapshot при скролле
    document.getElementById('content-area').addEventListener('scroll', function () {
        clearTimeout(scrollDebounceTimer);
        scrollDebounceTimer = setTimeout(reportAoiSnapshot, 200);
    }, { passive: true });

    reportAoiSnapshot();
}

// --- Рендер содержимого секции ---

function renderSectionContent(sectionData, sectionName) {
    // Для "Others": сохраняем оригинальное имя секции в data-aoi
    let html = '<div class="kv-table">';
    for (const [key, value] of Object.entries(sectionData)) {
        if (key.startsWith('__table_')) {
            html += renderEmbeddedTable(value);
        } else {
            html += `
                <div class="kv-row">
                    <span class="kv-key">${key}</span>
                    <span class="kv-value">${value}</span>
                </div>`;
        }
    }
    html += '</div>';
    return html;
}

function renderEmbeddedTable(tableData) {
    const labelHtml = tableData.label
        ? `<div class="table-label">${tableData.label}</div>`
        : '';
    const headers = tableData.headers.map(h => `<th>${h}</th>`).join('');
    const rows = tableData.rows.map(row => {
        const cells = row.map((cell, i) => {
            // Подсветка Unhealthy в колонке Health (обычно индекс 3)
            const cls = (cell === 'Unhealthy') ? ' class="unhealthy"' : '';
            return `<td${cls}>${cell}</td>`;
        }).join('');
        return `<tr>${cells}</tr>`;
    }).join('');
    return `${labelHtml}<table class="embedded-table">
        <thead><tr>${headers}</tr></thead>
        <tbody>${rows}</tbody>
    </table>`;
}

// --- Переключение секций (только Sections-условие) ---

function toggleSection(sectionName) {
    const block = document.querySelector(`.section-block[data-section="${sectionName}"]`);
    if (!block) return;

    const btn = block.querySelector('.section-header-toggle');
    const content = block.querySelector('.section-content');
    const chevron = btn.querySelector('.chevron');

    const isOpen = !content.classList.contains('collapsed');
    const newState = isOpen ? 'closed' : 'open';

    if (isOpen) {
        content.classList.add('collapsed');
        btn.classList.remove('open');
        chevron.textContent = '▸';
        openSections.delete(sectionName);
    } else {
        content.classList.remove('collapsed');
        btn.classList.add('open');
        chevron.textContent = '▾';
        openSections.add(sectionName);
    }

    sendEvent('PD_SECTION_TOGGLE', {
        page: 'pd_console',
        resource_id: currentResourceId,
        section_name: sectionName,
        new_state: newState,
        task_id: TASK.id,
        ts: performance.now()
    });

    reportAoiSnapshot();
}

// --- Навигация: Breadcrumb ---

function onBreadcrumbClick(target) {
    sendEvent('PD_BREADCRUMB_CLICK', {
        page: 'pd_console',
        target: target,
        task_id: TASK.id,
        ts: performance.now()
    });
    if (target in DATASET.resources) {
        renderResourceList(target);
    }
}

// --- Кнопка «Готово» ---

function onDoneClick() {
    const completionTime = Math.round(performance.now() - taskStartTime);
    sendEvent('PD_TASK_DONE', {
        page: 'pd_console',
        task_id: TASK.id,
        task_index: TASK_INDEX,
        task_type: TASK.type,
        condition: CONDITION,
        dataset: DATASET.name,
        block_num: BLOCK_NUM,
        completion_time_ms: completionTime,
        click_count: clickCounter
    });
    const btn = document.getElementById('done-btn');
    btn.disabled = true;
    btn.textContent = '✓';
}

// --- AOI Snapshot ---

function reportAoiSnapshot() {
    const aois = [];
    document.querySelectorAll('[data-aoi]').forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) {
            aois.push({
                name: el.dataset.aoi,
                x: Math.round(r.left),
                y: Math.round(r.top),
                w: Math.round(r.width),
                h: Math.round(r.height),
                visible: r.top < window.innerHeight && r.bottom > 0
            });
        }
    });
    sendEvent('PD_AOI_SNAPSHOT', {
        page: 'pd_console',
        task_id: TASK ? TASK.id : null,
        aois: aois,
        screen_w: window.innerWidth,
        screen_h: window.innerHeight,
        ts: performance.now()
    });
}
