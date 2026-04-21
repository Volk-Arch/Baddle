// Cone live-renderer — pulls CognitiveState и рисует когнитивный конус
// в реальном времени. Два таргета:
//   #baddle-cone-svg + #baddle-cone-label     — mini-viz в Baddle header
//   #dash-cone-svg + #dash-cone-state + sub   — полноразмерный в dashboard
//
// Poll /assist/state каждые 700мс → tween текущих значений к целевым через
// requestAnimationFrame. Анимация:
//   precision → (half-angle + length) плавно. Низкая → широкий короткий,
//     высокая → узкий длинный. Так видна ФОРМА, а не только угол.
//   pump → плавно раздвигаем apex'ы от центра к краям (single → dual)
//   thinking !== idle → CSS pulse на wrapper
//   bridge-found → overlap-зона зажигается
// Конус вписан в рамку с margin, не режется.

(function() {
  const NS = 'http://www.w3.org/2000/svg';

  // ── Состояние: target = что читаем с бэка, cur = что рисуем (lerp'им) ──
  const target = {
    precision: 0.5,
    state: 'exploration',
    thinking: 'idle',
    pumpT: 0,          // 0..1 — сила раздвижения apex'ов (single=0, dual=1)
    freeze: false,
    gamma: 1.0,
    syncRegime: null,
  };
  const cur = { ...target };

  const STATE_COLORS = {
    exploration:       { stroke: '#818cf8', fill: 'rgba(129,140,248,0.18)' },
    execution:         { stroke: '#10b981', fill: 'rgba(16,185,129,0.18)' },
    recovery:          { stroke: '#f59e0b', fill: 'rgba(245,158,11,0.18)' },
    integration:       { stroke: '#a78bfa', fill: 'rgba(167,139,250,0.18)' },
    stabilize:         { stroke: '#06b6d4', fill: 'rgba(6,182,212,0.18)' },
    conflict:          { stroke: '#ef4444', fill: 'rgba(239,68,68,0.22)' },
    protective_freeze: { stroke: '#dc2626', fill: 'rgba(220,38,38,0.32)' },
  };

  const STATE_LABELS_RU = {
    exploration:       'исследует',
    execution:         'действует',
    recovery:          'восстановление',
    integration:       'собирает',
    stabilize:         'стабилизирует',
    conflict:          'противоречия',
    protective_freeze: 'защита',
  };

  const THINKING_LABELS_RU = {
    idle:       null,            // не показываем отдельно
    pump:       'ищет мост',
    elaborate:  'углубляет',
    smartdc:    'проверяет',
    scout:      'бродит ночью',
    synthesize: 'собирает итог',
    think:      'думает',
  };

  function mkEl(tag, attrs) {
    const e = document.createElementNS(NS, tag);
    if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  // Конус от apex в направлении (dirX,dirY), длина `length`, половина
  // угла раствора `halfAngleRad`. Возвращает path d-строку треугольника.
  function conePath(apexX, apexY, dirX, dirY, length, halfAngleRad) {
    const baseCx = apexX + dirX * length;
    const baseCy = apexY + dirY * length;
    // Perpendicular
    const px = -dirY, py = dirX;
    const spread = length * Math.tan(halfAngleRad);
    const leftX = baseCx + px * spread, leftY = baseCy + py * spread;
    const rightX = baseCx - px * spread, rightY = baseCy - py * spread;
    return `M${apexX},${apexY} L${leftX},${leftY} L${rightX},${rightY} Z`;
  }

  // precision → (halfAngleDeg, length) так, чтобы конус ВСЕГДА помещался
  // в рамки viewBox minus margin. Низкая precision = широкий + короткий,
  // высокая = узкий + длинный. Это даёт видимую «динамику формы».
  function shapeFromPrecision(p, maxLength, maxSpread) {
    // Диапазон угла: 10° (точный лазер) .. 52° (широкий exploration)
    const halfAngleDeg = 10 + (1 - p) * 42;
    const halfAngleRad = halfAngleDeg * Math.PI / 180;
    const tanA = Math.tan(halfAngleRad);
    // Нужная длина: 55..92% maxLength в зависимости от precision
    const wantLength = maxLength * (0.55 + p * 0.37);
    // Safety cap: spread не должен выйти за maxSpread
    const capByWidth = maxSpread / tanA;
    return { halfAngleRad, length: Math.min(wantLength, capByWidth) };
  }

  // Уникальный gradient id для каждого SVG (иначе дефолт defs переопределяет)
  function ensureGradient(svg, color, key) {
    const gradId = `cone-grad-${key}-${svg.id || 'x'}`;
    // Remove предыдущий чтобы обновить цвет
    const existing = svg.querySelector(`#${gradId}`);
    if (existing) existing.remove();
    let defs = svg.querySelector('defs');
    if (!defs) { defs = mkEl('defs'); svg.appendChild(defs); }
    const grad = mkEl('linearGradient', {
      id: gradId, gradientUnits: 'objectBoundingBox',
      x1: '0.5', y1: '0', x2: '0.5', y2: '1',
    });
    // Top (apex) bright, base fade
    grad.appendChild(mkEl('stop', {offset: '0%', 'stop-color': color.stroke, 'stop-opacity': '0.9'}));
    grad.appendChild(mkEl('stop', {offset: '50%', 'stop-color': color.stroke, 'stop-opacity': '0.25'}));
    grad.appendChild(mkEl('stop', {offset: '100%', 'stop-color': color.stroke, 'stop-opacity': '0.05'}));
    defs.appendChild(grad);
    return `url(#${gradId})`;
  }

  function renderCone(svg, size) {
    if (!svg) return;
    // Hard clear всего содержимого
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    const color = STATE_COLORS[cur.state] || STATE_COLORS.exploration;
    const cx = size / 2, cy = size / 2;
    const pumpT = Math.max(0, Math.min(1, cur.pumpT));

    // Рамка безопасности: margin 8% по периметру
    const margin = size * 0.08;
    const innerW = size - 2 * margin;

    // Шейп конуса
    const maxLength = innerW * (pumpT > 0.5 ? 0.55 : 0.82);
    const maxSpread = innerW * 0.36;
    const shape = shapeFromPrecision(cur.precision, maxLength, maxSpread);

    // Apex'ы: при pumpT=0 они сливаются в центре сверху (single cone от центра,
    // идёт вниз). При pumpT=1 — два apex'а по горизонтали, конусы навстречу.
    // Interpolate: apex отделяются от центра к (left, cy) и (right, cy).
    const apexDist = innerW * 0.42 * pumpT;  // 0 → 0, 1 → 42% ширины от центра
    const apexY = cy + (1 - pumpT) * (-size * 0.22);  // single: сверху; pump: на середине

    const apex1 = [cx - apexDist, apexY];
    const apex2 = [cx + apexDist, apexY];
    // Direction: single — вниз (0,1); pump — навстречу по горизонтали.
    // Interpolate через pumpT: dir = (pumpT, 1-pumpT) normalized.
    const dirLen = Math.hypot(pumpT, 1 - pumpT) || 1;
    const dir1 = [pumpT / dirLen, (1 - pumpT) / dirLen];
    const dir2 = [-pumpT / dirLen, (1 - pumpT) / dirLen];

    // Gradient fill (один раз на state+svg)
    const fillUrl = ensureGradient(svg, color, cur.state);

    // Главный конус(ы)
    svg.appendChild(mkEl('path', {
      d: conePath(apex1[0], apex1[1], dir1[0], dir1[1], shape.length, shape.halfAngleRad),
      fill: fillUrl, stroke: color.stroke, 'stroke-width': 1.2,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round',
    }));
    if (pumpT > 0.05) {
      // Второй конус — появляется из того же места, расходится
      svg.appendChild(mkEl('path', {
        d: conePath(apex2[0], apex2[1], dir2[0], dir2[1], shape.length, shape.halfAngleRad),
        fill: fillUrl, stroke: color.stroke, 'stroke-width': 1.2,
        'stroke-linejoin': 'round', 'stroke-linecap': 'round',
      }));
    }

    // Apex glow dot (размер ≈ gamma/2)
    const dotR = Math.max(2, Math.min(size * 0.06, 2 + cur.gamma * 2));
    svg.appendChild(mkEl('circle', {
      cx: apex1[0], cy: apex1[1], r: dotR,
      fill: color.stroke,
      style: `filter: drop-shadow(0 0 ${dotR * 1.5}px ${color.stroke});`,
    }));
    if (pumpT > 0.05) {
      svg.appendChild(mkEl('circle', {
        cx: apex2[0], cy: apex2[1], r: dotR,
        fill: color.stroke,
        style: `filter: drop-shadow(0 0 ${dotR * 1.5}px ${color.stroke});`,
      }));
    }

    // Overlap diamond when pump близко к 1 — сияющий romb в центре
    if (pumpT > 0.3) {
      const alpha = Math.pow(pumpT, 1.5);  // appear ease-in
      const overlapR = shape.length * Math.tan(shape.halfAngleRad) * 0.55 * alpha;
      const diamond = mkEl('path', {
        d: `M${cx - overlapR},${cy} L${cx},${cy - overlapR} L${cx + overlapR},${cy} L${cx},${cy + overlapR} Z`,
        fill: `rgba(16,185,129,${0.55 * alpha})`,
        stroke: '#10b981', 'stroke-width': 1.2,
        style: `filter: drop-shadow(0 0 ${6 * alpha}px rgba(16,185,129,0.95));`,
      });
      svg.appendChild(diamond);
    }
  }

  function applyWrapperClasses() {
    const baddleWrap = document.querySelector('.baddle-cone-wrap');
    const dashWrap = document.getElementById('dash-cone-block');
    const isPumping = cur.pumpT > 0.4;
    [baddleWrap, dashWrap].forEach(w => {
      if (!w) return;
      w.classList.toggle('thinking', cur.thinking && cur.thinking !== 'idle');
      w.classList.toggle('pump', isPumping);
      w.classList.toggle('freeze', cur.freeze);
    });
    const baddleLabel = document.getElementById('baddle-cone-label');
    if (baddleLabel) {
      const tl = THINKING_LABELS_RU[cur.thinking];
      baddleLabel.textContent = tl || STATE_LABELS_RU[cur.state] || cur.state;
    }
    const dashState = document.getElementById('dash-cone-state');
    if (dashState) {
      const tl = THINKING_LABELS_RU[cur.thinking];
      dashState.textContent = tl ? `${tl}…` : (STATE_LABELS_RU[cur.state] || cur.state);
    }
    const dashSub = document.getElementById('dash-cone-sub');
    if (dashSub) {
      dashSub.textContent = `precision ${cur.precision.toFixed(2)} · γ ${cur.gamma.toFixed(2)}`
        + (cur.syncRegime ? ` · sync ${cur.syncRegime}` : '');
    }
  }

  function tickFrame() {
    // Lerp к target — плавная интерполяция на каждом кадре.
    // Разные скорости для разных величин: precision/gamma — медленно (дышит),
    // pumpT — чуть быстрее (быстрый визуальный отклик на bridge), state — snap.
    const aSlow = 0.08;
    const aFast = 0.14;
    cur.precision += (target.precision - cur.precision) * aSlow;
    cur.gamma += (target.gamma - cur.gamma) * aSlow;
    cur.pumpT += (target.pumpT - cur.pumpT) * aFast;
    // State/thinking/freeze/sync — дискретные, обновляем сразу
    cur.state = target.state;
    cur.thinking = target.thinking;
    cur.freeze = target.freeze;
    cur.syncRegime = target.syncRegime;

    renderCone(document.getElementById('baddle-cone-svg'), 80);
    renderCone(document.getElementById('dash-cone-svg'), 200);
    // В /lab — тот же конус вместо статичной graphRenderCone. SVG имеет
    // width=180 height=180 в HTML, viewBox ещё не установлен — работаем
    // в координатах 180.
    const labSvg = document.getElementById('graph-cone-viz');
    if (labSvg) {
      if (!labSvg.getAttribute('viewBox')) {
        labSvg.setAttribute('viewBox', '0 0 180 180');
      }
      renderCone(labSvg, 180);
    }
    applyWrapperClasses();
    requestAnimationFrame(tickFrame);
  }

  // Adaptive poll: быстрый когда идёт видимая работа, медленный в idle.
  // FAST — для плавной анимации dual-cone при pump/scout/smartdc/elaborate.
  // SLOW — когда thinking=idle и нет freeze. Сокращает server load в ~4 раза
  // при обычном use (большую часть времени система idle).
  const POLL_FAST_MS = 700;
  const POLL_SLOW_MS = 3000;
  let _pollTimer = null;
  let _pollMode = 'fast';

  async function pollState() {
    let newMode = 'fast';
    try {
      const r = await fetch('/assist/state');
      const d = await r.json();
      target.precision = typeof d.effective_precision === 'number'
        ? d.effective_precision : (d.precision || 0.5);
      target.state = d.state || 'exploration';
      target.gamma = (d.neurochem && d.neurochem.gamma) || d.gamma || 1.0;
      target.syncRegime = d.sync_regime || null;
      target.freeze = !!(d.neurochem && d.neurochem.freeze_active);
      const t = d.thinking || {};
      target.thinking = t.kind || 'idle';
      // pumpT: 0 → single cone, 1 → full dual. Плавный переход через tween.
      const isPump = target.thinking === 'pump' || target.state === 'integration';
      target.pumpT = isPump ? 1 : 0;
      // Mode: fast если есть видимая активность, иначе slow.
      const isActive = target.thinking && target.thinking !== 'idle';
      newMode = (isActive || target.freeze) ? 'fast' : 'slow';
    } catch(e) { /* silent — сервер может дышать */ }

    // Переключаем interval если режим сменился
    if (newMode !== _pollMode) {
      _pollMode = newMode;
      if (_pollTimer) clearTimeout(_pollTimer);
      const delay = newMode === 'fast' ? POLL_FAST_MS : POLL_SLOW_MS;
      _pollTimer = setTimeout(pollState, delay);
    } else {
      const delay = newMode === 'fast' ? POLL_FAST_MS : POLL_SLOW_MS;
      _pollTimer = setTimeout(pollState, delay);
    }
  }

  // Старт: poll + animation. Не запускаем если нет ни одной cone-цели на странице.
  function startConeLive() {
    const hasTarget = document.getElementById('baddle-cone-svg')
                   || document.getElementById('dash-cone-svg')
                   || document.getElementById('graph-cone-viz');
    if (!hasTarget) return;
    pollState();  // первый запуск ставит timeout сам
    requestAnimationFrame(tickFrame);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startConeLive);
  } else {
    startConeLive();
  }
})();
