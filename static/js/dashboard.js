/* static/js/dashboard.js */

document.addEventListener("DOMContentLoaded", function() {
    // Проверка, передалась ли конфигурация из HTML
    if (!window.DASHBOARD_CONFIG) {
        console.error("DASHBOARD_CONFIG not found! Check dashboard.html");
        return;
    }
    
    console.log("Dashboard JS loaded. Config:", window.DASHBOARD_CONFIG);
    
    initWindCompass();
    updateGraph(); // Загружаем график при старте
    initWindRose();
});

// --- 1. Компас ветра (Wind Compass) ---
function initWindCompass() {
    const face = document.getElementById('wind-face');
    if (!face) return;

    // Рисуем деления
    for (let a = 0; a < 360; a += 30) {
        const t = document.createElement('div');
        t.className = 'tick';
        t.style.transform = "rotate(" + a + "deg)";
        face.appendChild(t);
    }

    // Рисуем метки сторон света
    const labels = [
        ['N', '50%', '6px', 'translate(-50%,0)'],
        ['E', 'calc(100% - 16px)', '50%', 'translate(0,-50%)'],
        ['S', '50%', 'calc(100% - 16px)', 'translate(-50%,0)'],
        ['W', '6px', '50%', 'translate(0,-50%)'],
    ];

    labels.forEach(([txt, left, top, tr]) => {
        const l = document.createElement('div');
        l.className = 'label';
        l.innerText = txt;
        l.style.left = left;
        l.style.top = top;
        l.style.transform = tr;
        face.appendChild(l);
    });

    // Устанавливаем стрелку
    const needle = document.getElementById('wind-needle');
    const cfg = window.DASHBOARD_CONFIG;
    
    // Используем значения из конфига
    const deg = cfg.last_dm;
    const spd = cfg.last_sm;
    const colors = cfg.colors;

    if (deg !== null && deg !== undefined) {
        // Логика цвета стрелки в зависимости от скорости
        let color = colors.pale_blue;
        if (spd !== null) {
            if (spd >= 8) color = colors.dark_green;
            else if (spd >= 3) color = colors.slate;
        }

        needle.innerHTML =
            `<svg width="10" height="140" viewBox="0 0 10 140">
                <polygon points="5,5 9,68 5,74 1,68" fill="${color}" />
                <rect x="4" y="74" width="2" height="50" fill="${color}"></rect>
                <circle cx="5" cy="74" r="4" fill="#333"></circle>
            </svg>`;
        needle.style.transform = "rotate(" + deg + "deg)";
    }
}

// --- 2. Логика Графика (Plotly) ---

// Хелпер: если ничего не выбрано, выбираем первый элемент
function ensureSelection() {
    const selEl = document.getElementById('metrics-select');
    if (!selEl) return false;
    const selected = Array.from(selEl.selectedOptions || []);
    if (selected.length > 0) return false;
    if (selEl.options.length > 0) {
        selEl.options[0].selected = true;
        return true;
    }
    return false;
}

function updateGraph() {
    const changedByEnsure = ensureSelection();

    const selEl = document.getElementById('metrics-select');
    // Безопасная проверка на существование элемента
    if (!selEl) return;
    
    const sel = Array.from(selEl.selectedOptions || []).map(o => o.value);
    const el = document.getElementById('plotly-graph');
    if (!el) return;

    // Индикатор загрузки
    el.innerHTML = '<div class="m-3 text-muted">Загрузка…</div>';

    if (!sel.length) {
        el.innerHTML = '<div class="alert alert-warning m-3">Нет данных для отображения</div>';
        return;
    }

    const r = document.getElementById('range-select')?.value || '7d';
    const a = document.getElementById('agg-select')?.value || '1h';
    const sensorKey = window.DASHBOARD_CONFIG.sensor_key;

    const params = new URLSearchParams();
    params.append('metrics', JSON.stringify(sel));
    params.append('range', r);
    params.append('agg', a);

    console.log(`Fetching data for ${sensorKey}...`);

    fetch(`/api/data/${sensorKey}?` + params.toString())
        .then(r => r.json())
        .then(resp => {
            if (!resp || !resp.length) {
                // Если данных нет, пробуем перевыбрать (на случай ошибки UI)
                if (!changedByEnsure) {
                    const changed = ensureSelection();
                    if (changed) return updateGraph();
                }
                el.innerHTML = '<div class="alert alert-warning m-3">Нет данных для отображения за выбранный период</div>';
                return;
            }
            el.innerHTML = '';

            const traces = resp.map(m => ({
                x: m.timestamps.map(ts => new Date(ts)),
                y: m.values,
                name: m.desc + (m.unit ? ' (' + m.unit + ')' : ''),
                type: 'scatter',
                mode: 'lines',
                line: { color: m.color, width: 1.5 },
                yaxis: 'y'
            }));

            // Вычисляем диапазон Y с отступами
            const allVals = resp.flatMap(m => m.values).filter(v => Number.isFinite(v));
            const minY = allVals.length ? Math.min(...allVals) : null;
            const maxY = allVals.length ? Math.max(...allVals) : null;
            const pad = (minY !== null && maxY !== null) ? (maxY - minY) * 0.1 : 0;

            Plotly.newPlot('plotly-graph', traces, {
                margin: { t: 25, r: 50, b: 50, l: 60 },
                font: { family: 'Inter', size: 12 },
                showlegend: true,
                legend: {
                    orientation: 'h',
                    y: 1.1,
                    x: 0
                },
                plot_bgcolor: '#ffffff',
                paper_bgcolor: '#ffffff',
                xaxis: {
                    type: 'date',
                    gridcolor: '#f0f0f0',
                    zeroline: false
                },
                yaxis: {
                    automargin: true,
                    gridcolor: '#f0f0f0',
                    zeroline: false,
                    range: [
                        (Number.isFinite(minY - pad) ? (minY - pad) : null),
                        (Number.isFinite(maxY + pad) ? (maxY + pad) : null)
                    ]
                }
            }, { responsive: true });
        })
        .catch((err) => {
            console.error(err);
            el.innerHTML = '<div class="alert alert-danger m-3">Ошибка загрузки данных</div>';
        });
}

// Слушатели событий
document.getElementById('metrics-select')?.addEventListener('change', updateGraph);
document.getElementById('range-select')?.addEventListener('change', updateGraph);
document.getElementById('agg-select')?.addEventListener('change', updateGraph);

// --- 3. Роза ветров (Wind Rose) ---
function initWindRose() {
    const el = document.getElementById('wind-rose');
    if (!el) return;

    const roseData = window.DASHBOARD_CONFIG.rose_data;
    const colors = window.DASHBOARD_CONFIG.colors;

    if (!roseData || !roseData.theta || !roseData.theta.length) {
        el.innerHTML = '<div class="alert alert-warning m-3 small">Недостаточно данных для розы ветров</div>';
        return;
    }

    const trace = {
        type: 'barpolar',
        theta: roseData.theta,
        r: roseData.r,
        marker: {
            color: colors.dark_green,
            opacity: 0.85,
            line: { color: colors.pale_blue, width: 1 }
        },
        hovertemplate: 'Сектор %{theta}°<br>Частота: %{r}<br>Средняя скорость: %{customdata} м/с<extra></extra>',
        customdata: roseData.c
    };

    const layout = {
        polar: {
            angularaxis: {
                direction: 'clockwise',
                thetaunit: 'degrees',
                tick0: 0,
                dtick: 45,
                gridcolor: '#e9ecef',
                linecolor: '#adb5bd'
            },
            radialaxis: {
                gridcolor: '#e9ecef',
                linecolor: '#adb5bd'
            },
            bgcolor: '#ffffff'
        },
        margin: { t: 10, r: 10, b: 10, l: 10 },
        showlegend: false,
        paper_bgcolor: '#ffffff',
        font: { family: 'Inter' }
    };

    Plotly.newPlot('wind-rose', [trace], layout, { responsive: true });
}