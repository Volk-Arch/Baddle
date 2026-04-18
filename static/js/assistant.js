// ── Baddle Assistant — chat-first interface ──────────────────────────

let _assistEnergy = { energy: 100, max: 100, decisions_today: 0 };
let _assistHRV = null;
let _assistAlertsPolling = false;

// ── Chat persistence (localStorage) ────────────────────────────────
const CHAT_STORE_KEY = 'baddle-chat-history';
const CHAT_STORE_MAX = 100;  // keep last N turns

function _chatStoreLoad() {
  try { return JSON.parse(localStorage.getItem(CHAT_STORE_KEY) || '[]'); }
  catch { return []; }
}

function _chatStorePush(entry) {
  try {
    const hist = _chatStoreLoad();
    hist.push(entry);
    while (hist.length > CHAT_STORE_MAX) hist.shift();
    localStorage.setItem(CHAT_STORE_KEY, JSON.stringify(hist));
  } catch(e) { console.warn('[chat] persist failed:', e); }
}

function _chatStoreDedupMorning() {
  // Убирает дубликаты morning_briefing'ов в chat history — один раз при
  // рестарте сервер пересылал briefing заново, старые дубли уже в
  // localStorage. Оставляем только ПОСЛЕДНИЙ briefing за день.
  try {
    const hist = _chatStoreLoad();
    if (!hist.length) return;
    // Индекс последнего briefing'а (по mode_name)
    let lastBriefingIdx = -1;
    hist.forEach((e, i) => {
      if (e?.kind === 'msg' && e?.meta?.mode_name === 'Утро') lastBriefingIdx = i;
    });
    if (lastBriefingIdx < 0) return;
    const filtered = hist.filter((e, i) => {
      if (e?.kind === 'msg' && e?.meta?.mode_name === 'Утро' && i !== lastBriefingIdx) return false;
      return true;
    });
    if (filtered.length !== hist.length) {
      localStorage.setItem(CHAT_STORE_KEY, JSON.stringify(filtered));
      console.info(`[chat] dedup morning briefings: removed ${hist.length - filtered.length}`);
    }
  } catch(e) { /* silent */ }
}

function assistClearChat() {
  if (!confirm('Очистить историю чата?')) return;
  localStorage.removeItem(CHAT_STORE_KEY);
  const container = document.getElementById('assist-messages');
  if (container) container.innerHTML = '<div class="assist-empty">Baddle готов. Напиши что угодно — цель, вопрос, гипотезу.</div>';
}

function _restoreChatHistory() {
  _chatStoreDedupMorning();
  const hist = _chatStoreLoad();
  if (!hist.length) return false;
  const container = document.getElementById('assist-messages');
  if (!container) return false;
  // Clear empty-state placeholder
  const empty = container.querySelector('.assist-empty');
  if (empty) empty.remove();
  hist.forEach(entry => {
    if (entry.kind === 'msg') {
      assistAddMsg(entry.role, entry.content, entry.meta, /*persist=*/false);
    } else if (entry.kind === 'card') {
      const el = assistRenderCard(entry.card);
      container.appendChild(el);
    }
    // Skip 'warning' entries on restore — they're ephemeral alerts from /assist/alerts polling
  });
  container.scrollTop = container.scrollHeight;
  return true;
}

// ── Message rendering ──────────────────────────────────────────────────

function assistAddMsg(role, content, meta, persist) {
  if (persist !== false) {
    _chatStorePush({ kind: 'msg', role: role, content: content, meta: meta });
  }
  const container = document.getElementById('assist-messages');
  if (!container) return null;
  // Remove empty-state placeholder on first real message
  const empty = container.querySelector('.assist-empty');
  if (empty) empty.remove();
  const div = document.createElement('div');
  div.className = 'assist-msg assist-' + role;
  div.style.cssText = 'max-width:85%;padding:12px 16px;border-radius:16px;font-size:14px;line-height:1.5;margin-bottom:12px;';

  if (role === 'user') {
    div.style.background = '#4f46e5';
    div.style.color = 'white';
    div.style.alignSelf = 'flex-end';
    div.style.borderBottomRightRadius = '4px';
  } else if (role === 'assistant') {
    div.style.background = '#1f1f23';
    div.style.color = '#e4e4e7';
    div.style.alignSelf = 'flex-start';
    div.style.borderBottomLeftRadius = '4px';
  } else if (role === 'system') {
    div.style.background = '#27272a';
    div.style.color = '#a1a1aa';
    div.style.fontSize = '12px';
    div.style.alignSelf = 'center';
  }

  // Content
  const contentDiv = document.createElement('div');
  contentDiv.style.whiteSpace = 'pre-wrap';
  contentDiv.style.wordBreak = 'break-word';
  contentDiv.textContent = content;
  div.appendChild(contentDiv);

  // Meta (mode badge, energy cost, etc.)
  if (meta) {
    const metaDiv = document.createElement('div');
    metaDiv.style.cssText = 'font-size:11px;color:#52525b;margin-top:6px;display:flex;gap:8px;flex-wrap:wrap;';
    if (meta.mode) {
      const badge = document.createElement('span');
      badge.style.cssText = 'background:#312e81;color:#a5b4fc;padding:2px 8px;border-radius:6px;font-weight:500';
      badge.textContent = meta.mode_name || meta.mode;
      metaDiv.appendChild(badge);
    }
    if (meta.energy_cost) {
      const e = document.createElement('span');
      e.textContent = '⚡ ' + meta.energy_cost;
      metaDiv.appendChild(e);
    }
    if (meta.hrv_note) {
      const h = document.createElement('span');
      h.style.color = '#10b981';
      h.textContent = meta.hrv_note;
      metaDiv.appendChild(h);
    }
    div.appendChild(metaDiv);
  }

  // Step-deeper toolbar для assistant-сообщений с meta (исключаем «Утро»/
  // команды чтобы не спамить). Только для сообщений с полноценным mode.
  if (role === 'assistant' && meta && (meta.mode || meta.mode_name)
      && meta.mode_name !== 'Утро'
      && meta.mode_name !== 'Команды'
      && meta.mode_name !== 'Check-in'
      && meta.mode_name !== 'Ошибка'
      && typeof assistAttachStepActions === 'function') {
    assistAttachStepActions(div);
  }

  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return div;
}

// ── Render structured cards ─────────────────────────────────────────────

function assistRenderCard(card) {
  const wrapper = document.createElement('div');
  wrapper.className = 'assist-card';
  wrapper.style.cssText = 'align-self:stretch;max-width:100%;margin-bottom:12px;';

  if (card.type === 'status_briefing') {
    // Unified sections card (использует brief-* стили) — status, план,
    // food-history, help и т.д. Разницы с morning briefing по стилю нет,
    // просто без header «Доброе утро».
    const sections = card.sections || [];
    const _briefActionHtml = (actions) => {
      if (!Array.isArray(actions) || !actions.length) return '';
      return '<div class="brief-actions">' + actions.map(a => {
        const fn = _BRIEF_ACTION_MAP[a.action] || 'console.warn';
        return `<button class="activity-btn activity-btn-primary" onclick="${fn}()">${_esc(a.label || 'OK')}</button>`;
      }).join('') + '</div>';
    };
    const sectionsHtml = sections.map(s => {
      const kind = s.kind || 'neutral';
      return `<div class="brief-section brief-${_esc(kind)}">
        <span class="brief-emoji">${_esc(s.emoji || '•')}</span>
        <div class="brief-body">
          <div class="brief-title">${_esc(s.title || '')}</div>
          ${s.subtitle ? `<div class="brief-subtitle">${_esc(s.subtitle)}</div>` : ''}
          ${_briefActionHtml(s.actions)}
        </div>
      </div>`;
    }).join('');
    wrapper.className = 'assist-msg assist-assistant brief-card';
    wrapper.style.cssText = '';
    wrapper.innerHTML = `<div class="brief-sections">${sectionsHtml}</div>`;
    return wrapper;
  }

  if (card.type === 'open_modal') {
    // Chat-command card: «открываю check-in» — UI авто-открывает модал.
    if (card.modal === 'checkin') setTimeout(() => { try { openCheckin(); } catch(e){} }, 200);
    wrapper.remove(); // визуально не показываем — модал сам всплывёт
    return document.createDocumentFragment();
  }

  if (card.type === 'activity_action') {
    // Start/stop подтверждение — простая inline plaque
    const dur = card.duration_min ? ` · ${card.duration_min} мин` : '';
    const cat = card.category ? ` · ${_esc(card.category)}` : '';
    wrapper.innerHTML = `<div style="padding:10px 14px;background:#052e16;border:1px solid #166534;border-radius:10px;font-size:12px;color:#10b981">
      ${card.action === 'started' ? '▶' : '⏹'} ${_esc(card.name || '')}${cat}${dur}
    </div>`;
    // Trigger UI refresh
    setTimeout(() => { try { activityRefresh(); } catch(e){} }, 100);
    return wrapper;
  }

  if (card.type === 'morning_briefing') {
    // Structured briefing restore from history — делегируем render-функции
    const sections = card.sections || [];
    const dateStr = new Date().toLocaleDateString('ru-RU',
      { weekday: 'long', day: 'numeric', month: 'long' });
    const _briefActionHtml = (actions) => {
      if (!Array.isArray(actions) || !actions.length) return '';
      return '<div class="brief-actions">' + actions.map(a => {
        const fn = _BRIEF_ACTION_MAP[a.action] || 'console.warn';
        return `<button class="activity-btn activity-btn-primary" onclick="${fn}()">${_esc(a.label || 'OK')}</button>`;
      }).join('') + '</div>';
    };
    const sectionsHtml = sections.map(s => {
      const kind = s.kind || 'neutral';
      return `<div class="brief-section brief-${_esc(kind)}">
        <span class="brief-emoji">${_esc(s.emoji || '•')}</span>
        <div class="brief-body">
          <div class="brief-title">${_esc(s.title || '')}</div>
          ${s.subtitle ? `<div class="brief-subtitle">${_esc(s.subtitle)}</div>` : ''}
          ${_briefActionHtml(s.actions)}
        </div>
      </div>`;
    }).join('');
    wrapper.className = 'assist-msg assist-assistant brief-card';
    wrapper.style.cssText = '';
    wrapper.innerHTML = `
      <div class="brief-header">
        <span class="brief-greeting">☀️ Доброе утро</span>
        <span class="brief-date">${_esc(dateStr)}</span>
      </div>
      <div class="brief-sections">${sectionsHtml}</div>`;
    return wrapper;
  }

  if (card.type === 'dialectic') {
    wrapper.innerHTML = `
      <div style="padding:14px;background:#1f1f23;border-radius:14px;">
        <div style="display:flex;gap:10px;margin-bottom:10px;flex-wrap:wrap;">
          <div style="flex:1;min-width:200px;padding:10px;background:#052e16;border:1px solid #166534;border-radius:10px;">
            <div style="font-size:10px;color:#10b981;font-weight:600;margin-bottom:4px;">FOR</div>
            <div style="font-size:13px;color:#e4e4e7;">${_esc(card.thesis)}</div>
          </div>
          <div style="flex:1;min-width:200px;padding:10px;background:#1c1917;border:1px solid #78350f;border-radius:10px;">
            <div style="font-size:10px;color:#f59e0b;font-weight:600;margin-bottom:4px;">AGAINST</div>
            <div style="font-size:13px;color:#e4e4e7;">${_esc(card.antithesis)}</div>
          </div>
        </div>
        ${card.neutral ? `<div style="padding:10px;background:#1e293b;border:1px solid #334155;border-radius:10px;margin-bottom:10px;">
          <div style="font-size:10px;color:#94a3b8;font-weight:600;margin-bottom:4px;">NEUTRAL</div>
          <div style="font-size:12px;color:#cbd5e1;">${_esc(card.neutral)}</div>
        </div>` : ''}
        <div style="padding:10px;background:#1e1b4b;border:1px solid #4338ca;border-radius:10px;">
          <div style="font-size:10px;color:#818cf8;font-weight:600;margin-bottom:4px;">SYNTHESIS${card.confidence ? ` · confidence ${Math.round(card.confidence*100)}%` : ''}</div>
          <div style="font-size:13px;color:#e4e4e7;">${_esc(card.synthesis)}</div>
        </div>
        ${_feedbackButtons()}
      </div>`;
  }
  else if (card.type === 'comparison') {
    const optionsHtml = card.options.map((opt, i) => {
      const isWinner = i === card.winner_idx;
      const bg = isWinner ? '#1e1b4b' : '#27272a';
      const border = isWinner ? '#4338ca' : '#3f3f46';
      const color = isWinner ? '#818cf8' : '#a1a1aa';
      const star = isWinner ? ' ⭐' : '';
      return `<div style="padding:10px;background:${bg};border:1px solid ${border};border-radius:10px;margin-bottom:6px;">
        <div style="font-size:13px;color:${color};font-weight:${isWinner ? '600' : '400'};">${_esc(opt)}${star}</div>
      </div>`;
    }).join('');
    wrapper.innerHTML = `
      <div style="padding:14px;background:#1f1f23;border-radius:14px;">
        ${optionsHtml}
        ${card.reason ? `<div style="margin-top:10px;font-size:12px;color:#a1a1aa;"><b style="color:#818cf8;">Почему:</b> ${_esc(card.reason)}</div>` : ''}
        ${card.risk ? `<div style="margin-top:6px;font-size:12px;color:#f59e0b;"><b>⚠ Риск:</b> ${_esc(card.risk)}</div>` : ''}
        ${_feedbackButtons()}
      </div>`;
  }
  else if (card.type === 'bayesian') {
    const pct = Math.round((card.posterior || card.prior) * 100);
    const color = pct > 70 ? '#10b981' : pct < 30 ? '#ef4444' : '#f59e0b';
    wrapper.innerHTML = `
      <div style="padding:14px;background:#1f1f23;border-radius:14px;">
        <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:10px;">
          <span style="font-size:10px;color:#52525b;text-transform:uppercase;letter-spacing:1px;">Prior → Posterior</span>
          <span style="font-size:32px;font-weight:700;color:${color};">${pct}%</span>
        </div>
        <div style="font-size:13px;color:#cbd5e1;margin-bottom:8px;">${_esc(card.hypothesis)}</div>
        ${card.prior_reason ? `<div style="font-size:11px;color:#71717a;font-style:italic;">${_esc(card.prior_reason)}</div>` : ''}
        ${card.observations && card.observations.length ? `<div style="margin-top:10px;">
          ${card.observations.map(o => `<div style="font-size:12px;color:#a1a1aa;padding:3px 0;">• ${_esc(o)}</div>`).join('')}
        </div>` : `<div style="margin-top:10px;font-size:11px;color:#52525b;">Добавь наблюдение чтобы обновить вероятность.</div>`}
      </div>`;
  }
  else if (card.type === 'ideas_list') {
    const ideasHtml = card.ideas.map(i => `<div style="padding:8px 10px;background:#27272a;border-radius:8px;margin-bottom:4px;font-size:13px;color:#e4e4e7;">${_esc(i)}</div>`).join('');
    let verifiedHtml = '';
    if (card.verified_first && card.verified_first.synthesis) {
      verifiedHtml = `<div style="margin-top:10px;padding:10px;background:#1e1b4b;border:1px solid #4338ca;border-radius:10px;">
        <div style="font-size:10px;color:#818cf8;font-weight:600;margin-bottom:4px;">SMART DC: "${_esc(card.verified_first.text.substring(0,50))}..."</div>
        <div style="font-size:12px;color:#cbd5e1;">${_esc(card.verified_first.synthesis)}</div>
      </div>`;
    }
    wrapper.innerHTML = `
      <div style="padding:14px;background:#1f1f23;border-radius:14px;">
        ${ideasHtml}
        ${verifiedHtml}
        ${_feedbackButtons()}
      </div>`;
  }
  else if (card.type === 'decompose_suggestion') {
    const cta = _esc(card.cta || 'Разбить');
    const hint = _esc(card.hint || 'Разбить на подзадачи?');
    const msgEsc = _esc(card.message || '');
    wrapper.innerHTML = `
      <div style="padding:10px 14px;background:#1e1b4b;border:1px solid #4338ca;border-radius:12px;display:flex;align-items:center;gap:12px;">
        <span style="font-size:18px;">↯</span>
        <div style="flex:1;font-size:13px;color:#c7d2fe;">${hint}</div>
        <button onclick="_assistInlineDecompose(this, ${JSON.stringify(card.message || '').replace(/"/g, '&quot;')})"
          style="background:#4f46e5;color:white;border:0;padding:6px 14px;border-radius:8px;font-size:13px;cursor:pointer;font-weight:500;">
          ${cta}
        </button>
      </div>`;
  }
  else if (card.type === 'clarify') {
    wrapper.innerHTML = `
      <div style="padding:14px;background:#1c1917;border:1px solid #78350f;border-radius:12px;">
        <div style="font-size:10px;color:#f59e0b;font-weight:600;margin-bottom:6px;letter-spacing:0.5px;">УТОЧНЯЮ</div>
        <div style="font-size:14px;color:#fbbf24;">${_esc(card.question)}</div>
        <div style="font-size:11px;color:#a1a1aa;margin-top:8px;">Ответь — я пойму что ты имел в виду.</div>
      </div>`;
  }
  else if (card.type === 'profile_clarify') {
    const origAttr = (card.original_message || '').replace(/"/g, '&quot;');
    wrapper.innerHTML = `
      <div class="card-profile-clarify" data-category="${_esc(card.category || '')}" data-original="${origAttr}">
        <div class="pc-q">👤 ${_esc(card.question)}</div>
        <textarea placeholder="например: не ем орехи, люблю курицу"></textarea>
        <div class="pc-actions">
          <button class="secondary" onclick="profileClarifyDismiss(this.closest('.card-profile-clarify'))">Пропустить</button>
          <button class="primary" onclick="profileClarifySubmit(this.closest('.card-profile-clarify'))">Сохранить</button>
        </div>
      </div>`;
  }
  else if (card.type === 'habit') {
    wrapper.innerHTML = `
      <div style="padding:14px;background:#1f1f23;border-radius:14px;display:flex;align-items:center;gap:16px;">
        <div style="text-align:center;">
          <div style="font-size:32px;font-weight:700;color:#10b981;">${card.streak}</div>
          <div style="font-size:10px;color:#52525b;">streak</div>
        </div>
        <div style="flex:1;">
          <div style="font-size:14px;color:#e4e4e7;margin-bottom:4px;">${_esc(card.habit)}</div>
          <div style="font-size:11px;color:#71717a;">${_esc(card.message || '')}</div>
        </div>
      </div>`;
  }
  else {
    wrapper.innerHTML = `<div style="padding:10px;background:#27272a;border-radius:10px;font-size:12px;color:#71717a;">${_esc(JSON.stringify(card))}</div>`;
  }

  return wrapper;
}

function _esc(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── User feedback buttons on cards (FE-5) ──────────────────────────────
// Sends feedback to CognitiveState → DA_phasic + S drift.
async function assistFeedback(kind) {
  try {
    const r = await fetch('/assist/feedback', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ feedback: kind })
    });
    const d = await r.json();
    // Immediate visual hint — refresh neurochem panel
    if (d.ok) {
      fetch('/assist/state').then(r => r.json()).then(_updateNeurochemPanel).catch(()=>{});
    }
    return d;
  } catch(e) {
    console.warn('[feedback] failed:', e);
  }
}

function _feedbackButtons() {
  return `<div class="assist-feedback-row">
    <button onclick="assistFeedback('accepted')" class="assist-fb-btn assist-fb-accept" title="Полезно: +DA, S↑">👍</button>
    <button onclick="assistFeedback('rejected')" class="assist-fb-btn assist-fb-reject" title="Не то: −DA, S↓">👎</button>
    <button onclick="assistFeedback('ignored')" class="assist-fb-btn" title="Нейтрально">—</button>
  </div>`;
}

// ── Steps rendering (visible thinking) ─────────────────────────────

function assistAddSteps(steps, onComplete) {
  if (!steps || !steps.length) { if (onComplete) onComplete(); return; }
  const container = document.getElementById('assist-messages');
  if (!container) { if (onComplete) onComplete(); return; }

  const stepsDiv = document.createElement('div');
  stepsDiv.style.cssText = 'align-self:flex-start;max-width:85%;padding:10px 14px;border-radius:12px;background:#18181b;border:1px dashed #3f3f46;margin-bottom:12px;font-size:12px;color:#71717a;';
  container.appendChild(stepsDiv);
  container.scrollTop = container.scrollHeight;

  let i = 0;
  function next() {
    if (i >= steps.length) { if (onComplete) onComplete(); return; }
    const step = document.createElement('div');
    step.style.cssText = 'display:flex;align-items:center;gap:6px;padding:2px 0;opacity:0;transition:opacity 0.3s;';
    step.innerHTML = `<span style="color:#10b981;">✓</span> ${_esc(steps[i])}`;
    stepsDiv.appendChild(step);
    requestAnimationFrame(() => { step.style.opacity = '1'; });
    container.scrollTop = container.scrollHeight;
    i++;
    setTimeout(next, 250);
  }
  next();
}

function assistAddWarning(text, persist) {
  // Warnings are ephemeral alerts (energy/coherence state) — re-emitted by /assist/alerts polling.
  // Don't persist them; otherwise they accumulate across sessions and spam on reload.
  const container = document.getElementById('assist-messages');
  if (!container) return;
  const div = document.createElement('div');
  div.style.cssText = 'align-self:center;max-width:80%;background:#1c1917;border:1px solid #f59e0b;padding:10px 14px;border-radius:12px;font-size:12px;color:#f59e0b;margin-bottom:12px;';
  div.textContent = '⚠ ' + text;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

// ── Send message ──────────────────────────────────────────────────────

async function assistSend() {
  const input = document.getElementById('assist-input');
  if (!input) return;
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  if (input.tagName === 'TEXTAREA') input.style.height = 'auto';

  assistAddMsg('user', text);

  // If a clarifying question is pending, this message is the answer → route it
  if (_pendingAssistQuestion) {
    await _assistSubmitAnswer(text);
    return;
  }

  const sendBtn = document.getElementById('assist-send-btn');
  if (sendBtn) { sendBtn.disabled = true; sendBtn.textContent = '...'; }

  // Show pending indicator
  const container = document.getElementById('assist-messages');
  const pending = document.createElement('div');
  pending.className = 'assist-pending';
  pending.style.cssText = 'align-self:flex-start;max-width:85%;padding:10px 14px;border-radius:12px;background:#18181b;color:#71717a;font-size:13px;margin-bottom:12px;font-style:italic;';
  pending.innerHTML = '<span class="assist-dots">думаю</span>';
  container.appendChild(pending);
  container.scrollTop = container.scrollHeight;

  // Animate dots
  let dots = 0;
  const dotInterval = setInterval(() => {
    dots = (dots + 1) % 4;
    const el = pending.querySelector('.assist-dots');
    if (el) el.textContent = 'думаю' + '.'.repeat(dots);
  }, 400);

  try {
    const lang = (document.getElementById('lang-select') || {}).value || 'ru';
    const body = { message: text, lang: lang };
    // Forced mode — если юзер явно выбрал режим, отправляем его вместо LLM-classify
    if (_forcedMode && _forcedMode !== 'auto') {
      body.mode = _forcedMode;
    }
    const r = await fetch('/assist', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    const d = await r.json();
    // Reset forced mode после отправки — каждый message explicit
    if (_forcedMode && _forcedMode !== 'auto') {
      _setForcedMode('auto', /*silent=*/true);
    }

    clearInterval(dotInterval);
    pending.remove();

    if (d.error) {
      assistAddMsg('assistant', 'Ошибка: ' + d.error, { mode: d.mode, mode_name: d.mode_name });
    } else {
      // Show steps with animation
      if (d.steps && d.steps.length) {
        assistAddSteps(d.steps, () => {
          _assistShowResponse(d, text, lang);
        });
      } else {
        _assistShowResponse(d, text, lang);
      }
    }
  } catch(e) {
    clearInterval(dotInterval);
    pending.remove();
    assistAddMsg('assistant', 'Error: ' + e.message);
  } finally {
    if (sendBtn) { sendBtn.disabled = false; sendBtn.textContent = '→'; }
  }
}

function _assistShowResponse(d, originalText, lang) {
  // Main text
  assistAddMsg('assistant', d.text, { mode: d.mode, mode_name: d.mode_name });

  // Update action indicator with last system action (if any)
  if (d.mode_name) _updateNeurochemAction(d.mode, d.mode_name);

  // Cards
  if (d.cards && d.cards.length) {
    const container = document.getElementById('assist-messages');
    d.cards.forEach(card => {
      _chatStorePush({ kind: 'card', card: card });
      const el = assistRenderCard(card);
      container.appendChild(el);
    });
    container.scrollTop = container.scrollHeight;
  }

  // Warnings
  if (d.warnings && d.warnings.length) {
    d.warnings.forEach(w => assistAddWarning(w.text));
  }

  // Update energy/HRV display
  if (d.energy) _assistEnergy = d.energy;
  if (d.hrv !== undefined) _assistHRV = d.hrv;
  assistUpdateHeader();

  // Background: add to graph (non-blocking)
  _assistRunGraph(originalText, d.mode, lang).catch(() => {});
}

async function _assistRunGraph(text, mode, lang) {
  // For now: create a goal node in the graph based on the mode
  // This makes the graph reflect what the chat is doing
  try {
    await fetch('/graph/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        text: text,
        node_type: 'goal',
        mode: mode,
        lang: lang
      })
    });
  } catch(e) {
    console.warn('[assist] graph add failed:', e);
  }
}

// ── Ask: system asks a clarifying question (third loop — dialogical) ──

let _pendingAssistQuestion = null;

async function assistAsk() {
  const askBtn = document.getElementById('assist-ask-btn');
  if (askBtn) { askBtn.disabled = true; }
  const lang = (document.getElementById('lang-select') || {}).value || 'ru';
  try {
    const r = await fetch('/graph/assist', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ lang: lang })
    });
    const d = await r.json();
    if (d.error) {
      assistAddMsg('assistant', 'Ошибка: ' + d.error);
      return;
    }
    _pendingAssistQuestion = {
      question: d.question,
      mode: d.mode,
      answer_kind: d.answer_kind,
    };
    // Render as assistant question with kind badge
    const kindLabel = {
      evidence: 'evidence',
      subgoal: 'подцель',
      seed: 'seed',
    }[d.answer_kind] || d.answer_kind;
    assistAddMsg('assistant', '? ' + d.question, {
      mode_name: 'вопрос · ответ → ' + kindLabel
    });
    // Focus input, hint the user what will happen
    const input = document.getElementById('assist-input');
    if (input) {
      input.placeholder = 'Ответь — станет ' + kindLabel + '...';
      input.focus();
    }
  } catch(e) {
    assistAddMsg('assistant', 'Ошибка запроса: ' + e.message);
  } finally {
    if (askBtn) askBtn.disabled = false;
  }
}

async function _assistSubmitAnswer(answer) {
  // If a question is pending, route this message as an answer → node
  if (!_pendingAssistQuestion) return false;
  const pending = _pendingAssistQuestion;
  _pendingAssistQuestion = null;
  const lang = (document.getElementById('lang-select') || {}).value || 'ru';
  try {
    const r = await fetch('/graph/assist', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        lang: lang,
        answer: answer,
        question: pending.question,
        mode: pending.mode,
      })
    });
    const d = await r.json();
    if (d.ok) {
      const kindLabel = { evidence: 'evidence', subgoal: 'подцель', seed: 'seed' }[d.kind] || d.kind;
      let confirm = 'Записал как ' + kindLabel + ' (#' + d.node_idx + ')';
      if (d.kind === 'evidence' && d.prior !== undefined && d.posterior !== undefined) {
        const arrow = d.posterior > d.prior ? '↑' : (d.posterior < d.prior ? '↓' : '=');
        confirm += ` · ${d.relation} → confidence ${Math.round(d.prior*100)}% ${arrow} ${Math.round(d.posterior*100)}%`;
      }
      assistAddMsg('assistant', confirm, { mode_name: 'диалог' });
    } else {
      assistAddMsg('assistant', 'Не удалось записать ответ: ' + (d.error || 'unknown'));
    }
  } catch(e) {
    assistAddMsg('assistant', 'Ошибка: ' + e.message);
  } finally {
    const input = document.getElementById('assist-input');
    if (input) input.placeholder = 'Спроси или поставь задачу...';
  }
  return true;
}

// ── Decompose: split goal into subtasks via /assist/decompose ─────────

async function assistDecomposeUI() {
  const input = document.getElementById('assist-input');
  const text = (input && input.value.trim()) || '';
  if (!text) {
    assistAddMsg('system', 'Введи задачу, которую разбить, и нажми ↯');
    return;
  }
  const btn = document.getElementById('assist-decompose-btn');
  if (btn) btn.disabled = true;
  const lang = (document.getElementById('lang-select') || {}).value || 'ru';
  assistAddMsg('user', text);
  input.value = '';
  if (input.tagName === 'TEXTAREA') input.style.height = 'auto';
  try {
    const r = await fetch('/assist/decompose', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ message: text, lang: lang })
    });
    const d = await r.json();
    if (d.error) {
      assistAddMsg('assistant', 'Ошибка: ' + d.error);
      return;
    }
    assistAddMsg('assistant',
      'Разбил на ' + (d.subgoals || []).length + ' подзадач. Подтвердишь — создам цель.',
      { mode_name: 'декомпозиция' }
    );
    // Render subgoals card with confirm button
    const container = document.getElementById('assist-messages');
    const card = document.createElement('div');
    card.className = 'assist-card';
    card.style.cssText = 'align-self:stretch;margin-bottom:12px;';
    const id = 'decomp-' + Date.now();
    card.innerHTML = `
      <div style="padding:14px;background:#1f1f23;border-radius:14px;">
        <div style="font-size:10px;color:#818cf8;font-weight:600;margin-bottom:8px;">DECOMPOSITION</div>
        ${(d.subgoals || []).map((s, i) => `
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
            <span style="color:#52525b;font-size:11px;">${i+1}.</span>
            <input type="text" value="${_esc(s)}" class="decomp-sg"
              style="flex:1;background:#27272a;border:1px solid #3f3f46;border-radius:6px;
              padding:6px 8px;color:#e4e4e7;font-size:13px;"/>
          </div>`).join('')}
        <div style="margin-top:10px;display:flex;gap:8px;">
          <button onclick="_assistConfirmDecompose('${id}', ${JSON.stringify(text).replace(/"/g, '&quot;')})"
            style="background:#4f46e5;color:white;border:0;padding:6px 12px;border-radius:6px;font-size:13px;cursor:pointer;">
            Создать цель + подзадачи
          </button>
          <button onclick="this.parentElement.parentElement.parentElement.remove()"
            style="background:#27272a;color:#a1a1aa;border:1px solid #3f3f46;padding:6px 12px;border-radius:6px;font-size:13px;cursor:pointer;">
            Отмена
          </button>
        </div>
      </div>`;
    card.id = id;
    container.appendChild(card);
    container.scrollTop = container.scrollHeight;
  } catch(e) {
    assistAddMsg('assistant', 'Ошибка декомпозиции: ' + e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Inline decompose from suggestion card (FE replace of ↯ button)
async function _assistInlineDecompose(btnEl, message) {
  if (!message) return;
  btnEl.disabled = true;
  btnEl.textContent = '...';
  const lang = (document.getElementById('lang-select') || {}).value || 'ru';
  try {
    const r = await fetch('/assist/decompose', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ message: message, lang: lang })
    });
    const d = await r.json();
    if (d.error) { alert('Ошибка: ' + d.error); return; }
    // Render editable subgoals card — reuses existing flow visually
    const container = document.getElementById('assist-messages');
    const card = document.createElement('div');
    card.className = 'assist-card';
    card.style.cssText = 'align-self:stretch;margin-bottom:12px;';
    const id = 'decomp-' + Date.now();
    card.id = id;
    card.innerHTML = `
      <div style="padding:14px;background:#1f1f23;border-radius:14px;">
        <div style="font-size:10px;color:#818cf8;font-weight:600;margin-bottom:8px;">DECOMPOSITION</div>
        ${(d.subgoals || []).map((s, i) => `
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
            <span style="color:#52525b;font-size:11px;">${i+1}.</span>
            <input type="text" value="${_esc(s)}" class="decomp-sg"
              style="flex:1;background:#27272a;border:1px solid #3f3f46;border-radius:6px;
              padding:6px 8px;color:#e4e4e7;font-size:13px;"/>
          </div>`).join('')}
        <div style="margin-top:10px;display:flex;gap:8px;">
          <button onclick="_assistConfirmDecompose('${id}', ${JSON.stringify(message).replace(/"/g, '&quot;')})"
            style="background:#4f46e5;color:white;border:0;padding:6px 12px;border-radius:6px;font-size:13px;cursor:pointer;">
            Создать цель + подзадачи
          </button>
          <button onclick="this.parentElement.parentElement.parentElement.remove()"
            style="background:#27272a;color:#a1a1aa;border:1px solid #3f3f46;padding:6px 12px;border-radius:6px;font-size:13px;cursor:pointer;">
            Отмена
          </button>
        </div>
      </div>`;
    container.appendChild(card);
    container.scrollTop = container.scrollHeight;
    // Remove the suggestion card itself
    btnEl.closest('.assist-card').remove();
  } catch(e) {
    alert('Ошибка: ' + e.message);
    btnEl.disabled = false;
    btnEl.textContent = 'Разбить';
  }
}

async function _assistConfirmDecompose(cardId, goalText) {
  const card = document.getElementById(cardId);
  if (!card) return;
  const inputs = card.querySelectorAll('input.decomp-sg');
  const subs = Array.from(inputs).map(i => i.value.trim()).filter(Boolean);
  if (!subs.length) { alert('Нет подзадач'); return; }
  const combined = [goalText, ...subs].join('\n');
  const lang = (document.getElementById('lang-select') || {}).value || 'ru';
  try {
    await fetch('/graph/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        text: combined,
        node_type: 'goal',
        mode: 'builder',
        lang: lang,
        threshold: 0.91,
        sim_mode: 'embedding',
      })
    });
    card.remove();
    assistAddMsg('assistant', 'Создал цель "' + goalText.substring(0, 40) + '" + ' + subs.length + ' подзадач в графе.',
                 { mode_name: 'builder' });
  } catch(e) {
    alert('Ошибка: ' + e.message);
  }
}

// ── Header: energy + HRV display ───────────────────────────────────────

// ── Rolling HRV history for the header chart ─────────────────────────
var _assistHRVHistory = [];
const HRV_HISTORY_LEN = 10;

function assistUpdateHeader() {
  const energyEl = document.getElementById('assist-energy-value');
  const energyMaxEl = document.getElementById('assist-energy-max');
  const energyBar = document.getElementById('assist-energy-bar');
  const energyBig = document.getElementById('assist-energy-value');
  const hrvEl = document.getElementById('assist-hrv-status') || document.getElementById('assist-hrv-value');

  // Energy — daily (быстрый пул) + long_reserve (общий медленный)
  if (energyEl) energyEl.textContent = Math.round(_assistEnergy.energy);
  if (energyMaxEl) energyMaxEl.textContent = '/' + Math.round(_assistEnergy.max);
  if (energyBar && energyBig) {
    const pct = (_assistEnergy.energy / _assistEnergy.max) * 100;
    energyBar.style.width = pct + '%';
    let color = '#10b981';
    if (pct < 20) color = '#ef4444';
    else if (pct < 50) color = '#f59e0b';
    energyBar.style.background = color;
    energyBig.style.color = color;
  }

  // Long reserve (общий пул, max 2000). Big число = процент, subtitle = N/2000.
  const reserveBar = document.getElementById('assist-reserve-bar');
  const reserveValue = document.getElementById('assist-reserve-value');
  const reserveBig = document.getElementById('assist-reserve-big');
  if (reserveBar && reserveValue && reserveBig) {
    const lr = _assistEnergy.long_reserve;
    const lrMax = _assistEnergy.long_reserve_max || 2000;
    const lrPct = _assistEnergy.long_reserve_pct;
    if (typeof lr === 'number' && typeof lrPct === 'number') {
      const pct100 = Math.round(lrPct * 100);
      reserveBar.style.width = pct100 + '%';
      reserveBig.textContent = pct100;
      let lrColor = '#818cf8';
      if (lrPct < 0.3) lrColor = '#ef4444';
      else if (lrPct < 0.7) lrColor = '#f59e0b';
      reserveBar.style.background = lrColor;
      reserveBig.style.color = lrColor;
      reserveValue.textContent = `${Math.round(lr)}/${Math.round(lrMax)}`;
    } else {
      reserveBar.style.width = '0%';
      reserveBig.textContent = '—';
      reserveValue.textContent = '—';
    }
  }

  // HRV status text
  if (hrvEl) {
    if (_assistHRV && _assistHRV.coherence !== null && _assistHRV.coherence !== undefined) {
      const coh = _assistHRV.coherence;
      const rmssd = _assistHRV.rmssd !== undefined && _assistHRV.rmssd !== null ? Math.round(_assistHRV.rmssd) + 'ms' : '';
      hrvEl.textContent = 'coherence ' + coh.toFixed(2) + (rmssd ? ' · RMSSD ' + rmssd : '');
      hrvEl.classList.add('on');

      // Push to rolling history
      _assistHRVHistory.push(coh);
      if (_assistHRVHistory.length > HRV_HISTORY_LEN) _assistHRVHistory.shift();
    } else {
      hrvEl.textContent = 'HRV off';
      hrvEl.classList.remove('on');
    }
  }

  // HRV bar chart
  const chart = document.getElementById('assist-hrv-chart');
  if (chart) {
    if (_assistHRVHistory.length > 0) {
      chart.classList.add('active');
      // Rebuild bars
      chart.innerHTML = '';
      // Pad with zeros if less than HRV_HISTORY_LEN
      const padded = Array(HRV_HISTORY_LEN - _assistHRVHistory.length).fill(0).concat(_assistHRVHistory);
      padded.forEach((v, i) => {
        const bar = document.createElement('div');
        bar.className = 'hrv-bar';
        const h = Math.max(4, Math.min(28, v * 28));
        bar.style.height = h + 'px';
        // Latest is highlighted
        if (i === padded.length - 1) bar.style.background = '#6366f1';
        else bar.style.background = v > 0.6 ? '#10b981' : v > 0.3 ? '#f59e0b' : '#ef4444';
        chart.appendChild(bar);
      });
    } else {
      chart.classList.remove('active');
      chart.innerHTML = '';
    }
  }

  // HRV button state — кнопка это ACTION (Stop/Start), текст статуса
  // живёт отдельно в assist-brand-status.
  const hrvBtn = document.querySelector('.assist-hrv-btn');
  if (hrvBtn) {
    const running = !!(_assistHRV && _assistHRV.coherence !== null && _assistHRV.coherence !== undefined);
    if (running) {
      hrvBtn.textContent = 'Stop HRV';
      hrvBtn.classList.add('running');
    } else {
      hrvBtn.textContent = 'Start HRV';
      hrvBtn.classList.remove('running');
    }
  }
}

// ── Neurochem panel polling (v5d) ──────────────────────────────────────

let _neurochemPolling = false;
let _lastCameraState = false;

// Human-readable labels — technical terms stay in tooltips
const _MODE_LABELS = {
  exploration: 'исследует',
  execution: 'фокус',
  recovery: 'восстанавливается',
  integration: 'собирает',
  stabilize: 'стабилизация',
  conflict: 'конфликт',
  protective_freeze: 'защитный режим',
  shift: 'сдвиг',
};
const _ACTION_LABELS = {
  smartdc: 'проверка',
  elaborate: 'углубляю',
  think_toward: 'генерирую',
  collapse: 'объединяю',
  compare: 'сравниваю',
  pump: 'ищу мост',
  stable: 'готово',
  ask: 'спрашиваю',
  synthesize: 'синтез',
  none: '—',
};
const _ORIGIN_LABELS = {
  '1_rest': '◌ покой',
  '1_held': '● в работе',
};

// ── Chip popover: клик по чипу → объяснение + все sibling-состояния ───
// UX-мотив: «исследует» (horizon state), «нейтральное» (Voronoi region),
// «покой» (state_origin), «overload» (activity zone) — 4 разных оси без
// подсказки что вообще может быть. Popup показывает что этот тип
// значит + список всех возможных вариантов с описаниями.

const _HORIZON_STATE_INFO = {
  exploration:       {label: 'исследует', desc: 'Низкая precision, широкий кону с — ищем варианты'},
  execution:         {label: 'фокус',     desc: 'Высокая precision, узкий конус — идём к цели'},
  recovery:          {label: 'восстанавливается', desc: 'NE низкий, DA дрейфует — паузa'},
  integration:       {label: 'собирает',  desc: 'Верифицируем соединения между нодами'},
  stabilize:         {label: 'стабилизация', desc: 'Насыщение — удерживаем достигнутое'},
  conflict:          {label: 'конфликт',  desc: 'Противоречия в графе — требуют разрешения'},
  protective_freeze: {label: 'защитный режим', desc: 'Много отказов — переход в режим охраны'},
  shift:             {label: 'сдвиг',     desc: 'Переход между режимами'},
};

const _ORIGIN_INFO = {
  '1_rest': {label: '◌ покой',    desc: 'Система в фоне, DMN может бродить по графу'},
  '1_held': {label: '● в работе', desc: 'Активный запрос юзера — Horizon держит фокус'},
};

const _NAMED_STATE_INFO = {
  flow:       {label: '🌊 поток',       desc: 'Оптимум: активность ↔ вовлечённость'},
  curiosity:  {label: '🧭 любопытство', desc: 'Ищу новое, низкая усталость'},
  stress:     {label: '⚠ стресс',       desc: 'NE высокий, устойчивость падает'},
  burnout:    {label: '🔥 выгорание',   desc: 'Высокое burnout + низкий DA'},
  apathy:     {label: '💤 апатия',      desc: 'Низкие DA + активность + resolve'},
  meditation: {label: '🧘 медитация',   desc: 'Низкая активность, высокая устойчивость'},
  excitement: {label: '✨ возбуждение', desc: 'Высокая активность + positive'},
  frustration:{label: '😤 раздражение', desc: 'Частые rejects, NE растёт'},
  calm:       {label: '😊 покой',       desc: 'Стабильно, нейтральная валентность'},
  neutral:    {label: '😐 нейтральное', desc: 'Default / недостаточно сигнала'},
};

const _ACTIVITY_ZONE_INFO = {
  recovery:    {label: '🟢 восстановление', desc: 'HRV ok + движения нет — здоровая пауза'},
  stress_rest: {label: '🟡 стресс в покое', desc: 'Низкий HRV при неподвижности — тревога'},
  healthy_load:{label: '🔵 здоровая нагрузка', desc: 'HRV ok + движение — продуктивная активность'},
  overload:    {label: '🔴 перегрузка',     desc: 'Низкий HRV + высокая активность — риск'},
};

let _chipPopupCurrentEl = null;

function _chipPopupClose() {
  const pop = document.getElementById('chip-info-popup');
  if (pop) pop.remove();
  _chipPopupCurrentEl = null;
  document.removeEventListener('click', _chipPopupClickOutside, true);
  document.removeEventListener('keydown', _chipPopupEsc, true);
}
function _chipPopupClickOutside(e) {
  const pop = document.getElementById('chip-info-popup');
  if (!pop) return;
  if (pop.contains(e.target) || (_chipPopupCurrentEl && _chipPopupCurrentEl.contains(e.target))) return;
  _chipPopupClose();
}
function _chipPopupEsc(e) { if (e.key === 'Escape') _chipPopupClose(); }

function _showChipInfo(anchor, title, items, currentKey, extraSection) {
  _chipPopupClose();
  _chipPopupCurrentEl = anchor;
  const pop = document.createElement('div');
  pop.id = 'chip-info-popup';
  pop.className = 'chip-info-popup';
  const entries = Object.entries(items).map(([k, info]) => {
    const isCurr = k === currentKey;
    return `<li class="${isCurr ? 'current' : ''}">
      <span class="chip-info-label">${_esc(info.label)}</span>
      <span class="chip-info-desc">${_esc(info.desc)}</span>
    </li>`;
  }).join('');
  pop.innerHTML = `
    <div class="chip-info-title">${_esc(title)}</div>
    <ul class="chip-info-list">${entries}</ul>
    ${extraSection || ''}
    <div class="chip-info-hint">Esc или клик вне — закрыть</div>`;
  document.body.appendChild(pop);
  // Position под anchor
  const r = anchor.getBoundingClientRect();
  pop.style.top = (r.bottom + window.scrollY + 4) + 'px';
  const left = Math.min(r.left + window.scrollX, window.innerWidth - 320);
  pop.style.left = Math.max(8, left) + 'px';
  setTimeout(() => {
    document.addEventListener('click', _chipPopupClickOutside, true);
    document.addEventListener('keydown', _chipPopupEsc, true);
  }, 10);
}

async function chipInfoHorizonState(el) {
  // В дополнение к 8 horizon states показываем 14 thinking-mode'ов (graph modes)
  let modesHtml = '';
  try {
    const modes = await (await fetch('/modes')).json();
    const lis = modes.map(m => `<li><span class="chip-info-label">${_esc(m.name || m.id)}</span>
      <span class="chip-info-desc">${_esc(m.intro || '')}</span></li>`).join('');
    modesHtml = `<div class="chip-info-title" style="margin-top:10px">Thinking modes (14)</div>
      <ul class="chip-info-list">${lis}</ul>`;
  } catch(e) {}
  const curKey = (el.dataset.stateKey || '').trim();
  _showChipInfo(el, 'Horizon state (внутренний режим)', _HORIZON_STATE_INFO, curKey, modesHtml);
}

function chipInfoOrigin(el) {
  const curKey = (el.dataset.stateKey || '').trim();
  _showChipInfo(el, 'State origin (тонус системы)', _ORIGIN_INFO, curKey);
}

function chipInfoNamedState(el) {
  const curKey = (el.dataset.stateKey || '').trim();
  _showChipInfo(el, 'Named state (Voronoi регион юзера)', _NAMED_STATE_INFO, curKey);
}

function chipInfoActivityZone(el) {
  const curKey = (el.dataset.stateKey || '').trim();
  _showChipInfo(el, 'Activity zone (HRV × движение)', _ACTIVITY_ZONE_INFO, curKey);
}

async function assistPollNeurochem() {
  if (!_neurochemPolling) return;
  try {
    const r = await fetch('/assist/state');
    const d = await r.json();
    _updateNeurochemPanel(d);
    _updateLmBadge(d.api_health);
  } catch(e) { /* silent */ }
  // Подтягиваем фоновый статус (DMN + heartbeat) — реже, только если dashboard открыт
  if (_baddleSub === 'dashboard') {
    _refreshBackgroundStatus();
  }
  // Also refresh timeline when open (cheaper than fetching /graph/self every time)
  if (_timelineOpen) _refreshTimeline();
  setTimeout(assistPollNeurochem, 3000);
}

async function _refreshBackgroundStatus() {
  try {
    const r = await fetch('/loop/status');
    const st = await r.json();
    const dashBG = document.getElementById('dash-background');
    const dashDMN = document.getElementById('dash-dmn');
    if (!dashBG || !dashDMN) return;

    const dmn = st.dmn || {};
    const hbAge = st.last_heartbeat ? (Date.now()/1000 - st.last_heartbeat) : null;
    const dmnAge = st.last_dmn ? (Date.now()/1000 - st.last_dmn) : null;

    // Main value: DMN status
    if (dmn.eligible_now) {
      dashBG.textContent = 'DMN ready';
      dashBG.style.color = '#10b981';
    } else if (dmnAge !== null && dmnAge < 300) {
      dashBG.textContent = `DMN бодрствовал ${Math.round(dmnAge)}с назад`;
      dashBG.style.color = '#818cf8';
    } else {
      dashBG.textContent = 'DMN ждёт';
      dashBG.style.color = '#a1a1aa';
    }

    // Sub: heartbeat + blocked reason
    const hb = hbAge !== null ? `heartbeat ${Math.round(hbAge/60)}м назад` : 'heartbeat —';
    const blockedShort = dmn.blocked_by ? dmn.blocked_by.split(' (')[0] : 'готов';
    dashDMN.textContent = `${hb} · ${blockedShort}`;
  } catch(e) { /* silent */ }
}

function _updateLmBadge(health) {
  const el = document.getElementById('assist-lm-status');
  if (!el || !health) return;
  const st = health.status;
  el.classList.remove('lm-ok','lm-degraded','lm-offline');
  if (st === 'ok') {
    // Показываем короткое «LM ok» только если раньше был offline/degraded
    if (el.dataset.prev && el.dataset.prev !== 'ok') {
      el.classList.add('lm-ok');
      el.textContent = 'LM ok';
      el.style.display = '';
      setTimeout(() => { if (el.classList.contains('lm-ok')) el.style.display = 'none'; }, 3000);
    } else {
      el.style.display = 'none';
    }
  } else if (st === 'degraded') {
    el.classList.add('lm-degraded');
    el.textContent = `LM degraded (${health.consecutive_failures} fails)`;
    el.title = health.last_error || '';
    el.style.display = '';
  } else if (st === 'offline') {
    el.classList.add('lm-offline');
    el.textContent = '⚠ LM offline';
    el.title = health.last_error || '';
    el.style.display = '';
  } else {
    el.style.display = 'none';
  }
  el.dataset.prev = st;
}

function assistStartNeurochemPolling() {
  if (_neurochemPolling) return;
  _neurochemPolling = true;
  assistPollNeurochem();
}

function _updateNeurochemPanel(metrics) {
  if (!metrics) return;
  const neuro = metrics.neurochem || {};
  const serotonin = neuro.serotonin || 0;
  const norepi    = neuro.norepinephrine || 0;
  const dopamine  = neuro.dopamine || 0;
  const burnout   = neuro.burnout || 0;

  const setBar = (fillId, valId, value, isBurnout) => {
    const fill = document.getElementById(fillId);
    const val = document.getElementById(valId);
    if (!fill || !val) return;
    const v = typeof value === 'number' ? value : 0;
    fill.style.width = (v * 100).toFixed(0) + '%';
    val.textContent = v.toFixed(2);
    if (isBurnout && v > 0.15) {
      fill.style.background = '#dc2626';  // darker red when over freeze threshold
    }
  };
  setBar('neuro-s-fill',    'neuro-s-val',    serotonin);
  setBar('neuro-ne-fill',   'neuro-ne-val',   norepi);
  setBar('neuro-da-fill',   'neuro-da-val',   dopamine);
  setBar('neuro-burn-fill', 'neuro-burn-val', burnout, true);

  // User-side (symbiosis mirror) — same bars, different source
  const user = metrics.user_state || {};
  setBar('user-da-fill',   'user-da-val',   user.dopamine || 0);
  setBar('user-s-fill',    'user-s-val',    user.serotonin || 0);
  setBar('user-ne-fill',   'user-ne-val',   user.norepinephrine || 0);
  setBar('user-burn-fill', 'user-burn-val', user.burnout || 0);

  // Sync indicator (prime-directive в одном бейдже)
  const regime = metrics.sync_regime || 'flow';
  const syncErr = typeof metrics.sync_error === 'number' ? metrics.sync_error : 0;
  const regimeEl = document.getElementById('sync-regime');
  if (regimeEl) {
    regimeEl.textContent = regime.toUpperCase();
    regimeEl.className = 'sync-regime ' + regime;
  }
  const errEl = document.getElementById('sync-error');
  if (errEl) {
    // Max L2 на 4D в [0,1] ≈ 2.0 → пересчёт в percent «синхронизации»
    const pct = Math.max(0, Math.min(100, Math.round((1 - syncErr / 2) * 100)));
    errEl.textContent = 'sync ' + pct + '%';
  }

  // Dopamine phasic arrow (legacy — new dopamine is single scalar, so always hidden)
  const phasicEl = document.getElementById('neuro-da-phasic');
  if (phasicEl) phasicEl.style.display = 'none';

  // Mode badge + freeze animation
  const modeEl = document.getElementById('neuro-mode');
  if (modeEl) {
    const state = metrics.state || 'exploration';
    modeEl.textContent = _MODE_LABELS[state] || state;
    modeEl.title = 'Horizon state: ' + state + ' — клик для полного списка';
    modeEl.dataset.stateKey = state;
    modeEl.classList.toggle('freeze', state === 'protective_freeze');
  }

  // State origin badge
  const originEl = document.getElementById('neuro-origin');
  if (originEl) {
    const origin = neuro.state_origin || '1_rest';
    originEl.textContent = _ORIGIN_LABELS[origin] || origin;
    originEl.title = 'state_origin: ' + origin + ' — клик для описания';
    originEl.dataset.stateKey = origin;
  }

  // Named user-state badge (Voronoi)
  const namedEl = document.getElementById('neuro-user-named');
  if (namedEl && metrics.user_state && metrics.user_state.named_state) {
    const ns = metrics.user_state.named_state;
    const emojis = {
      flow: '🌊', inspiration: '✨', curiosity: '🔍', gratitude: '🙏',
      neutral: '😐', meditation: '🧘', apathy: '😶', stress: '😰',
      disappointment: '😔', burnout: '🥀',
    };
    const em = emojis[ns.key] || '◯';
    namedEl.textContent = `${em} ${(ns.label || ns.key).toLowerCase()}`;
    namedEl.title = (ns.advice || ns.key) + ' — клик для списка всех регионов';
    namedEl.dataset.stateKey = ns.key || 'neutral';
  }

  // Dashboard status strip — 4 живых индикатора
  try {
    const regime = metrics.sync_regime || '—';
    const syncErr = metrics.sync_error;
    const dashSR = document.getElementById('dash-sync-regime');
    const dashSE = document.getElementById('dash-sync-error');
    if (dashSR) dashSR.textContent = regime.toUpperCase();
    if (dashSE) dashSE.textContent = (syncErr !== undefined && syncErr !== null)
      ? `sync ${Math.round((1 - Math.min(1, syncErr)) * 100)}% · err ${syncErr.toFixed(2)}`
      : 'sync —';

    const ns = (metrics.user_state || {}).named_state || {};
    const dashN = document.getElementById('dash-named');
    const dashNA = document.getElementById('dash-named-advice');
    if (dashN) dashN.textContent = (ns.label || '—');
    if (dashNA) dashNA.textContent = ns.advice || '—';

    const dashH = document.getElementById('dash-horizon');
    const dashO = document.getElementById('dash-origin');
    const stateKey = metrics.state || 'exploration';
    if (dashH) dashH.textContent = (_MODE_LABELS[stateKey] || stateKey);
    if (dashO) dashO.textContent = (_ORIGIN_LABELS[neuro.state_origin] || neuro.state_origin || '◌ покой');
  } catch(e) {}

  // Activity zone badge (HRV × activity — 4 зоны)
  const zoneEl = document.getElementById('neuro-activity-zone');
  if (zoneEl) {
    const az = metrics.user_state?.activity_zone;
    if (az && az.key) {
      zoneEl.style.display = 'inline-block';
      zoneEl.textContent = `${az.emoji || ''} ${(az.label || az.key).toLowerCase()}`;
      zoneEl.title = (az.advice || az.key) + ' — клик для 4 зон';
      zoneEl.dataset.stateKey = az.key;
      zoneEl.className = 'neuro-zone-badge clickable-chip zone-' + az.key;
    } else {
      // HRV не запущен или нет зоны — прячем badge
      zoneEl.style.display = 'none';
    }
  }

  // Camera mode badge + button
  const camBadge = document.getElementById('neuro-camera');
  const camBtn = document.getElementById('neuro-camera-btn');
  const camOn = !!metrics.llm_disabled;
  if (camBadge) camBadge.style.display = camOn ? 'inline-block' : 'none';
  if (camBtn) camBtn.classList.toggle('active', camOn);
  _lastCameraState = camOn;

  // Refresh sparkline + sync-dashboard if open
  if (_sparklineOpen) _refreshSparkline();
  if (_syncDashOpen) _refreshSyncDash();
}

// ── Neurochem sparkline (30-tick history) ────────────────────────────

let _sparklineOpen = false;

function assistToggleSparkline() {
  const panel = document.getElementById('neuro-sparklines');
  const btn = document.getElementById('neuro-spark-btn');
  if (!panel) return;
  _sparklineOpen = panel.style.display === 'none';
  panel.style.display = _sparklineOpen ? 'block' : 'none';
  if (btn) btn.classList.toggle('active', _sparklineOpen);
  if (_sparklineOpen) _refreshSparkline();
}

async function _refreshSparkline() {
  try {
    const r = await fetch('/assist/history?limit=30');
    const d = await r.json();
    const svg = document.getElementById('neuro-sparkline-svg');
    if (!svg || !d.entries || !d.entries.length) {
      if (svg) svg.innerHTML = '<text x="120" y="22" text-anchor="middle" fill="#52525b" font-size="9">no history yet</text>';
      return;
    }
    const series = [
      {key: 'dopamine',        color: '#10b981', y: 0 },   // 4 strips x 10px each
      {key: 'serotonin',       color: '#a78bfa', y: 10 },
      {key: 'norepinephrine',  color: '#f59e0b', y: 20 },
      {key: 'burnout',         color: '#ef4444', y: 30 },
    ];
    const n = d.entries.length;
    const W = 240, H = 40;
    const stepX = n > 1 ? W / (n - 1) : W;
    let paths = '';
    for (const s of series) {
      const pts = d.entries.map((e, i) => {
        const v = typeof e[s.key] === 'number' ? e[s.key] : 0.5;
        const x = i * stepX;
        const y = s.y + (1 - Math.max(0, Math.min(1, v))) * 8 + 1;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(' ');
      paths += `<polyline fill="none" stroke="${s.color}" stroke-width="1.4" points="${pts}"/>`;
      paths += `<line x1="0" x2="${W}" y1="${s.y + 9.5}" y2="${s.y + 9.5}" stroke="#27272a" stroke-width="0.4"/>`;
    }
    svg.innerHTML = paths;
  } catch(e) { /* silent */ }
}

// ── Sync-dashboard ──────────────────────────────────────────────────

let _syncDashOpen = false;

function assistToggleSyncDash() {
  const panel = document.getElementById('sync-dashboard');
  const btn = document.getElementById('neuro-sync-btn');
  if (!panel) return;
  _syncDashOpen = panel.style.display === 'none';
  panel.style.display = _syncDashOpen ? 'block' : 'none';
  if (btn) btn.classList.toggle('active', _syncDashOpen);
  if (_syncDashOpen) _refreshSyncDash();
}

async function _refreshSyncDash() {
  try {
    const r = await fetch('/assist/history?limit=80');
    const d = await r.json();
    const svg = document.getElementById('sync-dash-svg');
    const top = document.getElementById('sync-dash-top');
    const count = document.getElementById('sync-dash-count');
    if (count) count.textContent = `· ${d.count || 0} ticks`;
    if (!svg) return;
    if (!d.entries || !d.entries.length) {
      svg.innerHTML = '<text x="180" y="42" text-anchor="middle" fill="#52525b" font-size="10">нет данных</text>';
      if (top) top.innerHTML = '';
      return;
    }
    const W = 360, H = 80, pad = 4;
    const n = d.entries.length;
    const stepX = n > 1 ? (W - 2*pad) / (n - 1) : 0;
    // sync_error L2-norm может быть 0..2, рисуем 0..1.5 вертикально
    const maxSync = 1.5;
    const pts = d.entries.map((e, i) => {
      const v = typeof e.sync_error === 'number' ? e.sync_error : 0;
      const x = pad + i * stepX;
      const y = pad + (1 - Math.max(0, Math.min(maxSync, v)) / maxSync) * (H - 2*pad);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    // 0.3 threshold line (sync-high boundary)
    const threshY = pad + (1 - 0.3/maxSync) * (H - 2*pad);
    svg.innerHTML = `
      <line x1="${pad}" x2="${W-pad}" y1="${threshY}" y2="${threshY}"
            stroke="#6366f1" stroke-width="0.6" stroke-dasharray="3,3" opacity="0.6"/>
      <text x="${W-pad-4}" y="${threshY-2}" text-anchor="end" fill="#6366f1"
            font-size="8" opacity="0.8">0.3 sync</text>
      <polyline fill="none" stroke="#eab308" stroke-width="1.2" points="${pts}"/>
      <text x="${pad+2}" y="${H-3}" fill="#52525b" font-size="8">older ——→ newer</text>
    `;
    // Top rejected modes
    if (top) {
      const list = d.top_rejected_modes || [];
      if (!list.length) {
        top.innerHTML = '<div style="color:#52525b;font-size:10px">отказов нет</div>';
      } else {
        top.innerHTML = list.map(r =>
          `<div class="dash-top-row"><span class="dash-top-mode">${_esc(r.mode)}</span>` +
          `<span class="dash-top-count">${r.count}×</span></div>`
        ).join('');
      }
    }
  } catch(e) { /* silent */ }
}

// ── Weekly review modal ─────────────────────────────────────────────

async function assistWeeklyReview() {
  try {
    const r = await fetch('/assist/weekly', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({lang: 'ru'}),
    });
    const d = await r.json();
    document.getElementById('weekly-modal').style.display = 'flex';
    const sum = document.getElementById('weekly-summary');
    if (sum) sum.textContent = d.text || '';
    _renderWeeklyDaily(d.daily_series || []);
    _renderWeeklyModes(d.mode_counts || {});
    _renderWeeklyStreaks(d.streaks || {});
    _renderWeeklyRecommendations(d.recommendations || []);
    _renderWeeklyDigest(d.digest || {});
  } catch(e) { console.warn('[weekly] failed:', e); }
}

function _renderWeeklyDigest(digest) {
  let host = document.getElementById('weekly-digest');
  if (!host) {
    const body = document.querySelector('#weekly-modal .weekly-body') || document.getElementById('weekly-modal');
    if (!body) return;
    host = document.createElement('div');
    host.id = 'weekly-digest';
    host.className = 'weekly-chart-block';
    host.style.cssText = 'margin-top:8px';
    body.appendChild(host);
  }
  if (!digest || Object.keys(digest).length === 0) { host.innerHTML = ''; return; }

  const blocks = [];

  // Habits
  if (digest.habits && !digest.habits.error) {
    const h = digest.habits;
    const rate = h.rate !== null ? Math.round(h.rate * 100) + '%' : '—';
    const topHtml = (h.top || []).map(t => `<li>${_esc(t.name)} <span style="color:#71717a">${t.done}/${t.planned}</span></li>`).join('');
    blocks.push(`<div class="digest-block">
      <div class="digest-title">🔁 Habits · ${h.completed}/${h.planned} · ${rate}</div>
      ${topHtml ? `<ul class="digest-list">${topHtml}</ul>` : ''}
    </div>`);
  }

  // Food
  if (digest.food && !digest.food.error) {
    const f = digest.food;
    const topHtml = (f.top_names || []).slice(0, 5).map(([n, c]) => `<li>${_esc(n)} <span style="color:#71717a">×${c}</span></li>`).join('');
    blocks.push(`<div class="digest-block">
      <div class="digest-title">🍽 Food · ${f.entries} записей · ${f.unique_names} уникальных · ${f.total_minutes}мин</div>
      ${topHtml ? `<ul class="digest-list">${topHtml}</ul>` : '<div style="color:#52525b;font-size:11px">Еду не трекал — попробуй записывать активность «Обед»/«Завтрак»</div>'}
    </div>`);
  }

  // Scout bridges
  if (digest.scout_bridges && digest.scout_bridges.length) {
    const br = digest.scout_bridges.map(b => `<li style="font-size:11px">«${_esc(b.text)}» <span style="color:#71717a">· ${b.source}</span></li>`).join('');
    blocks.push(`<div class="digest-block">
      <div class="digest-title">🌙 Scout нашёл за неделю · ${digest.scout_bridges.length} мостов</div>
      <ul class="digest-list">${br}</ul>
    </div>`);
  }

  // Check-in averages
  if (digest.checkin && digest.checkin.n) {
    const c = digest.checkin;
    blocks.push(`<div class="digest-block">
      <div class="digest-title">📝 Check-in · ${c.n} записей за 7 дней</div>
      <div style="font-size:11px;color:#a1a1aa">
        energy ${c.energy_mean ?? '—'} · focus ${c.focus_mean ?? '—'} · stress ${c.stress_mean ?? '—'} · surprise ${c.surprise_mean ?? '—'}
      </div>
    </div>`);
  }

  // Patterns
  if (digest.patterns && digest.patterns.length) {
    const ps = digest.patterns.map(p => `<li style="font-size:11px">${_esc(p.hint_ru || p.kind)}</li>`).join('');
    blocks.push(`<div class="digest-block">
      <div class="digest-title">💡 Паттерны</div>
      <ul class="digest-list">${ps}</ul>
    </div>`);
  }

  host.innerHTML = `<div class="weekly-chart-title">Дайджест недели</div>${blocks.join('')}`;
}

function _renderWeeklyRecommendations(recs) {
  let host = document.getElementById('weekly-recommendations');
  if (!host) {
    // Inject один раз если ещё нет слота
    const body = document.querySelector('#weekly-modal .weekly-body') || document.getElementById('weekly-modal');
    if (!body) return;
    host = document.createElement('div');
    host.id = 'weekly-recommendations';
    host.className = 'weekly-chart-block';
    host.style.cssText = 'margin-top:8px';
    body.appendChild(host);
  }
  if (!recs.length) { host.innerHTML = ''; return; }
  const items = recs.map(r => {
    const colour = r.kind === 'insufficient_data' ? '#52525b'
                 : r.kind === 'work_heavy'        ? '#f59e0b'
                                                  : '#818cf8';
    return `<div style="padding:10px 12px;background:#1e1b4b;border-left:3px solid ${colour};border-radius:8px;margin-bottom:6px">
              <div style="font-size:10px;color:${colour};font-weight:600;margin-bottom:4px;text-transform:uppercase;">${r.kind.replace('_',' ')}</div>
              <div style="font-size:13px;color:#e4e4e7;">${_esc(r.text || '')}</div>
            </div>`;
  }).join('');
  host.innerHTML = `<div class="weekly-chart-title">Рекомендации</div>${items}`;
}

function assistCloseWeekly(ev) {
  if (ev && ev.target.closest('.weekly-content') && !ev.target.classList.contains('weekly-close')) return;
  document.getElementById('weekly-modal').style.display = 'none';
}

function _renderWeeklyDaily(series) {
  const svg = document.getElementById('weekly-daily-svg');
  if (!svg) return;
  if (!series.length) { svg.innerHTML = ''; return; }
  const W = 360, H = 80, pad = 8;
  const maxV = Math.max(1, ...series.map(s => s.count));
  const barW = (W - 2*pad) / series.length - 4;
  let bars = '';
  series.forEach((s, i) => {
    const x = pad + i * ((W - 2*pad) / series.length) + 2;
    const barH = (s.count / maxV) * (H - 2*pad - 10);
    const y = H - pad - barH;
    const dayLabel = s.date.slice(-2);
    bars += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${barH.toFixed(1)}"
                   fill="${s.count > 0 ? '#6366f1' : '#27272a'}" rx="2"/>`;
    bars += `<text x="${(x + barW/2).toFixed(1)}" y="${H-2}" text-anchor="middle" fill="#71717a" font-size="8">${dayLabel}</text>`;
    if (s.count > 0) {
      bars += `<text x="${(x + barW/2).toFixed(1)}" y="${(y-2).toFixed(1)}" text-anchor="middle" fill="#a5b4fc" font-size="8">${s.count}</text>`;
    }
  });
  svg.innerHTML = bars;
}

function _renderWeeklyModes(counts) {
  const el = document.getElementById('weekly-modes');
  if (!el) return;
  const entries = Object.entries(counts).sort((a,b) => b[1] - a[1]);
  if (!entries.length) { el.innerHTML = '<div style="color:#52525b;font-size:10px">пусто</div>'; return; }
  const max = entries[0][1] || 1;
  el.innerHTML = entries.map(([mode, count]) => {
    const pct = (count / max * 100).toFixed(0);
    return `<div class="weekly-bar-row">
      <span class="weekly-bar-label">${_esc(mode)}</span>
      <span class="weekly-bar-track"><span class="weekly-bar-fill" style="width:${pct}%"></span></span>
      <span class="weekly-bar-value">${count}</span>
    </div>`;
  }).join('');
}

function _renderWeeklyStreaks(streaks) {
  const el = document.getElementById('weekly-streaks');
  if (!el) return;
  const entries = Object.entries(streaks).sort((a,b) => b[1] - a[1]);
  if (!entries.length) { el.innerHTML = '<div style="color:#52525b;font-size:10px">нет привычек</div>'; return; }
  const max = Math.max(1, ...entries.map(e => e[1]));
  el.innerHTML = entries.map(([habit, days]) => {
    const pct = (days / max * 100).toFixed(0);
    const name = habit.length > 20 ? habit.slice(0, 20) + '…' : habit;
    return `<div class="weekly-bar-row">
      <span class="weekly-bar-label">${_esc(name)}</span>
      <span class="weekly-bar-track"><span class="weekly-bar-fill" style="width:${pct}%;background:linear-gradient(90deg,#10b981,#84cc16)"></span></span>
      <span class="weekly-bar-value">${days}d</span>
    </div>`;
  }).join('');
}

// ── Profile modal ────────────────────────────────────────────────────

async function profileOpen() {
  document.getElementById('profile-modal').style.display = 'flex';
  await _refreshProfile();
}

function profileClose(ev) {
  if (ev && ev.target.closest('.weekly-content') && !ev.target.classList.contains('weekly-close')) return;
  document.getElementById('profile-modal').style.display = 'none';
}

async function _refreshProfile() {
  try {
    const r = await fetch('/profile');
    const d = await r.json();
    const body = document.getElementById('profile-body');
    if (!body) return;
    const profile = d.profile || {};
    const cats = d.categories || [];
    const labels = d.labels_ru || {};
    const ctx = profile.context || {};
    // Context block (wake/sleep/profession) — в начале
    const ctxHtml = `<div class="profile-category">
      <div class="profile-cat-title">Контекст</div>
      <div class="profile-add" style="margin-bottom:6px">
        <span style="font-size:11px;color:#a1a1aa;align-self:center;width:95px">Подъём (час)</span>
        <input type="number" min="0" max="23" id="profile-wake-hour" value="${ctx.wake_hour ?? 7}">
        <button onclick="profileSetContext('wake_hour', parseInt(document.getElementById('profile-wake-hour').value))">OK</button>
      </div>
      <div class="profile-add" style="margin-bottom:6px">
        <span style="font-size:11px;color:#a1a1aa;align-self:center;width:95px">Отбой (час)</span>
        <input type="number" min="0" max="23" id="profile-sleep-hour" value="${ctx.sleep_hour ?? 23}">
        <button onclick="profileSetContext('sleep_hour', parseInt(document.getElementById('profile-sleep-hour').value))">OK</button>
      </div>
      <div class="profile-add">
        <span style="font-size:11px;color:#a1a1aa;align-self:center;width:95px">Профессия</span>
        <input type="text" id="profile-profession" value="${_esc(ctx.profession || '')}" placeholder="разработчик, врач, ...">
        <button onclick="profileSetContext('profession', document.getElementById('profile-profession').value)">OK</button>
      </div>
      <div class="profile-add" title="Автоматически запускать HRV-симулятор при старте сервера — не нужно жать «Start HRV» каждое утро">
        <span style="font-size:11px;color:#a1a1aa;align-self:center;width:95px">HRV auto-start</span>
        <label style="display:flex;align-items:center;gap:6px;font-size:11px;color:#a1a1aa">
          <input type="checkbox" id="profile-hrv-autostart" ${ctx.hrv_autostart ? 'checked' : ''}
            onchange="profileSetContext('hrv_autostart', this.checked)">
          при старте процесса
        </label>
      </div>
    </div>`;
    body.innerHTML = ctxHtml + cats.map(cat => {
      const entry = (profile.categories || {})[cat] || {preferences:[], constraints:[]};
      const prefs = entry.preferences || [];
      const cons = entry.constraints || [];
      const prefChips = prefs.map(t => `<span class="profile-chip pref">${_esc(t)}<button class="remove" onclick="profileRemove('${cat}','preferences',${JSON.stringify(t).replace(/'/g,"\\'").replace(/"/g,'&quot;')})">×</button></span>`).join('');
      const consChips = cons.map(t => `<span class="profile-chip constraint">${_esc(t)}<button class="remove" onclick="profileRemove('${cat}','constraints',${JSON.stringify(t).replace(/'/g,"\\'").replace(/"/g,'&quot;')})">×</button></span>`).join('');
      return `<div class="profile-category">
        <div class="profile-cat-title">${_esc(labels[cat] || cat)}</div>
        <div class="profile-kind">
          <div class="profile-kind-label">Нравится</div>
          <div class="profile-items" id="profile-pref-${cat}">${prefChips || '<span style="color:#52525b;font-size:10px">пусто</span>'}</div>
          <div class="profile-add">
            <input type="text" id="profile-input-pref-${cat}" placeholder="добавить что любишь">
            <button onclick="profileAdd('${cat}','preferences')">+</button>
          </div>
        </div>
        <div class="profile-kind">
          <div class="profile-kind-label">Избегаю</div>
          <div class="profile-items" id="profile-cons-${cat}">${consChips || '<span style="color:#52525b;font-size:10px">пусто</span>'}</div>
          <div class="profile-add">
            <input type="text" id="profile-input-cons-${cat}" placeholder="добавить ограничение">
            <button onclick="profileAdd('${cat}','constraints')">+</button>
          </div>
        </div>
      </div>`;
    }).join('');
  } catch(e) { console.warn('[profile] load failed:', e); }
}

async function profileAdd(cat, kind) {
  const inputId = kind === 'preferences' ? `profile-input-pref-${cat}` : `profile-input-cons-${cat}`;
  const input = document.getElementById(inputId);
  if (!input) return;
  const text = input.value.trim();
  if (!text) return;
  try {
    await fetch('/profile/add', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({category: cat, kind, text}),
    });
    input.value = '';
    await _refreshProfile();
  } catch(e) { console.warn('[profile] add failed:', e); }
}

async function profileRemove(cat, kind, text) {
  try {
    await fetch('/profile/remove', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({category: cat, kind, text}),
    });
    await _refreshProfile();
  } catch(e) { console.warn('[profile] remove failed:', e); }
}

async function profileSetContext(key, value) {
  try {
    await fetch('/profile/context', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key, value}),
    });
    await _refreshProfile();
  } catch(e) { console.warn('[profile] context set failed:', e); }
}

// ── Goals modal ──────────────────────────────────────────────────────

async function goalsOpen() {
  document.getElementById('goals-modal').style.display = 'flex';
  await _refreshGoals();
}

function goalsClose(ev) {
  if (ev && ev.target.closest('.weekly-content') && !ev.target.classList.contains('weekly-close')) return;
  document.getElementById('goals-modal').style.display = 'none';
}

async function _refreshGoals() {
  try {
    const [openR, solvedR, statsR] = await Promise.all([
      fetch('/goals?status=open').then(r => r.json()),
      fetch('/goals/solved').then(r => r.json()),
      fetch('/goals/stats').then(r => r.json()),
    ]);
    const statsEl = document.getElementById('goals-stats');
    if (statsEl) {
      statsEl.innerHTML = `Всего: <b>${statsR.total || 0}</b> · открыто: <b>${statsR.open || 0}</b> · завершено: <b>${statsR.done || 0}</b> · заброшено: <b>${statsR.abandoned || 0}</b> · completion-rate: <b>${((statsR.completion_rate || 0) * 100).toFixed(0)}%</b>` +
        (statsR.avg_time_to_done_h != null ? ` · avg time: <b>${statsR.avg_time_to_done_h}ч</b>` : '');
    }
    const openEl = document.getElementById('goals-open');
    if (openEl) {
      const items = openR.goals || [];
      openEl.innerHTML = items.length ? items.map(g => {
        const date = g.created_at ? new Date(g.created_at * 1000).toLocaleDateString() : '';
        return `<div class="goals-row status-open">
          <span class="goal-status"></span>
          <span class="goal-text" title="${_esc(g.text)}">${_esc(g.text)}</span>
          <span class="goal-meta">${_esc(g.mode || '?')} · ${_esc(g.workspace || 'main')} · ${date}</span>
          <span class="goal-actions">
            <button onclick="goalComplete('${g.id}')">✓</button>
            <button onclick="goalAbandon('${g.id}')">×</button>
          </span>
        </div>`;
      }).join('') : '<div style="color:#52525b;font-size:10px">нет открытых целей</div>';
    }
    const solvedEl = document.getElementById('goals-solved');
    if (solvedEl) {
      const items = solvedR.solved || [];
      solvedEl.innerHTML = items.length ? items.slice(0, 15).map(s => {
        const date = s.archived_at ? new Date(s.archived_at * 1000).toLocaleDateString() : '';
        return `<div class="goals-row status-done" title="${_esc(s.reason || '')}">
          <span class="goal-status"></span>
          <span class="goal-text">${_esc(s.goal_text || s.snapshot_ref)}</span>
          <span class="goal-meta">${s.nodes_count || 0} нод · ${date}</span>
        </div>`;
      }).join('') : '<div style="color:#52525b;font-size:10px">архив пуст</div>';
    }
  } catch(e) { console.warn('[goals] load failed:', e); }
}

async function goalComplete(id) {
  try {
    await fetch('/goals/complete', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id, reason: 'manually marked'}),
    });
    await _refreshGoals();
  } catch(e) { console.warn('[goals] complete failed:', e); }
}

async function goalAbandon(id) {
  if (!confirm('Отметить цель как заброшенную?')) return;
  try {
    await fetch('/goals/abandon', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id, reason: 'manually abandoned'}),
    });
    await _refreshGoals();
  } catch(e) { console.warn('[goals] abandon failed:', e); }
}

// ── Profile-clarify card handler (when /assist returns profile_clarify) ──

async function profileClarifySubmit(cardEl) {
  const ta = cardEl.querySelector('textarea');
  const answer = (ta && ta.value || '').trim();
  if (!answer) return;
  const category = cardEl.dataset.category;
  const originalMsg = cardEl.dataset.original || '';
  const btn = cardEl.querySelector('button.primary');
  if (btn) { btn.disabled = true; btn.textContent = '...'; }
  try {
    const r = await fetch('/profile/learn', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({category, answer, original_message: originalMsg, lang: 'ru'}),
    });
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    // Показываем what was saved + авто-ретраим original message
    cardEl.innerHTML = `<div class="pc-q">✓ Запомнил: ${(d.added.preferences || []).length} предпочтений, ${(d.added.constraints || []).length} ограничений. Повторяю запрос...</div>`;
    // Повторный assist с тем же message (теперь profile не пустой)
    if (originalMsg) {
      setTimeout(() => {
        const inp = document.getElementById('assist-input');
        if (inp) { inp.value = originalMsg; assistSend(); }
      }, 400);
    }
  } catch(e) {
    if (btn) { btn.disabled = false; btn.textContent = 'Сохранить'; }
    console.warn('[profile] learn failed:', e);
  }
}

function profileClarifyDismiss(cardEl) {
  cardEl.innerHTML = '<div class="pc-q" style="color:#71717a">Пропущено. Можешь заполнить профиль вручную 👤</div>';
}

// ── Meta-graph overlay (SVG circular layout) ─────────────────────────

let _metaOpen = false;

async function metaGraphToggle() {
  const el = document.getElementById('meta-graph-svg');
  if (!el) return;
  _metaOpen = el.style.display === 'none';
  el.style.display = _metaOpen ? 'block' : 'none';
  if (_metaOpen) await _refreshMetaGraph();
}

async function _refreshMetaGraph() {
  const el = document.getElementById('meta-graph-svg');
  if (!el) return;
  try {
    const r = await fetch('/workspace/meta');
    const d = await r.json();
    const nodes = d.nodes || [];
    const edges = d.edges || [];
    const W = 260, H = 180, cx = W/2, cy = H/2, R = Math.min(cx, cy) - 20;
    if (!nodes.length) {
      el.innerHTML = `<text x="${cx}" y="${cy}" text-anchor="middle" fill="#52525b" font-size="10">нет workspaces</text>`;
      return;
    }
    // Circular layout
    const positions = {};
    nodes.forEach((n, i) => {
      const angle = (i / nodes.length) * 2 * Math.PI - Math.PI/2;
      positions[n.id] = {
        x: cx + R * Math.cos(angle),
        y: cy + R * Math.sin(angle),
      };
    });
    // Edges first (underneath nodes)
    let edgesSvg = '';
    for (const e of edges) {
      const p1 = positions[e.from], p2 = positions[e.to];
      if (!p1 || !p2) continue;
      const w = Math.min(4, 0.5 + (e.count || 1) * 0.3);
      edgesSvg += `<line x1="${p1.x.toFixed(1)}" y1="${p1.y.toFixed(1)}"
                          x2="${p2.x.toFixed(1)}" y2="${p2.y.toFixed(1)}"
                          stroke="#6366f1" stroke-width="${w.toFixed(1)}" opacity="0.5"/>`;
    }
    let nodesSvg = '';
    for (const n of nodes) {
      const p = positions[n.id];
      const isActive = n.id === d.active;
      const fill = isActive ? '#a855f7' : '#3f3f46';
      const stroke = isActive ? '#e9d5ff' : '#71717a';
      const r_ = 6 + Math.log(Math.max(1, n.node_count || 0)) * 1.5;
      nodesSvg += `<g><title>${_esc(n.title || n.id)} · ${n.node_count || 0} nodes</title>
        <circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${r_.toFixed(1)}"
                fill="${fill}" stroke="${stroke}" stroke-width="1.2"/>
        <text x="${p.x.toFixed(1)}" y="${(p.y + r_ + 10).toFixed(1)}"
              text-anchor="middle" fill="#e4e4e7" font-size="9">${_esc(n.id.slice(0, 10))}</text>
      </g>`;
    }
    el.innerHTML = `<text x="8" y="12" fill="#a1a1aa" font-size="9">meta-graph · ${nodes.length} ws · ${edges.length} bridges</text>${edgesSvg}${nodesSvg}`;
  } catch(e) {
    el.innerHTML = `<text x="${130}" y="90" text-anchor="middle" fill="#ef4444" font-size="10">error: ${_esc(String(e))}</text>`;
  }
}

function _updateNeurochemAction(action, reason) {
  const el = document.getElementById('neuro-action');
  if (!el) return;
  if (!action || action === 'idle' || action === 'none') {
    el.style.display = 'none';
    return;
  }
  el.style.display = 'inline-block';
  el.textContent = _ACTION_LABELS[action] || action;
  el.title = reason || ('action: ' + action);
}

// Timeline toggle + render (FE-2)
let _timelineOpen = false;

async function assistToggleTimeline() {
  const panel = document.getElementById('neuro-timeline');
  const btn = document.getElementById('neuro-timeline-btn');
  if (!panel) return;
  _timelineOpen = panel.style.display === 'none';
  panel.style.display = _timelineOpen ? 'block' : 'none';
  if (btn) btn.classList.toggle('active', _timelineOpen);
  if (_timelineOpen) await _refreshTimeline();
}

async function _refreshTimeline() {
  try {
    const r = await fetch('/graph/self?limit=20&tail=true');
    const d = await r.json();
    const list = document.getElementById('neuro-timeline-list');
    const count = document.getElementById('neuro-timeline-count');
    if (count) count.textContent = d.total || 0;
    if (!list) return;
    if (!d.entries || !d.entries.length) {
      list.innerHTML = '<div style="color:#52525b;font-size:11px">No actions yet — start the chat or Run.</div>';
      return;
    }
    list.innerHTML = d.entries.reverse().map(e => {
      const t = (e.timestamp || '').substring(11, 19);
      const originRaw = e.state_origin || '1_rest';
      const originLabel = originRaw === '1_held' ? '● работа' : '◌ покой';
      const actionLabel = _ACTION_LABELS[e.action] || (e.action || '?');
      const reason = (e.reason || '').substring(0, 60);
      // Дополнительные классы для цветового мазка (state_origin + action)
      const originCls = originRaw === '1_held' ? 'origin-held' : 'origin-rest';
      const actionCls = e.action === 'ask' ? 'action-ask'
                      : e.action === 'stable' ? 'action-stable' : '';
      return `<div class="neuro-timeline-item ${originCls} ${actionCls}" title="${_esc(e.action)} · ${_esc(originRaw)}">
        <span class="neuro-timeline-time">${t}</span>
        <span class="neuro-timeline-action">${_esc(actionLabel)}</span>
        <span class="neuro-timeline-origin ${originRaw === '1_held' ? 'held' : ''}">${originLabel}</span>
        <span class="neuro-timeline-reason">${_esc(reason)}</span>
      </div>`;
    }).join('');
  } catch(e) { /* silent */ }
}

async function assistToggleCamera() {
  const next = !_lastCameraState;
  try {
    await fetch('/assist/camera', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ enabled: next })
    });
    _lastCameraState = next;
    // Immediate refresh
    fetch('/assist/state').then(r => r.json()).then(_updateNeurochemPanel).catch(()=>{});
  } catch(e) {
    console.warn('[camera] toggle failed:', e);
  }
}

// ── Workspace tabs (FE-4) ──────────────────────────────────────────────

async function workspaceRefresh() {
  try {
    const r = await fetch('/workspace/list');
    const d = await r.json();
    const sel = document.getElementById('workspace-select');
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = (d.workspaces || []).map(w =>
      `<option value="${w.id}">${_esc(w.title || w.id)} (${w.node_count || 0})</option>`
    ).join('');
    sel.value = d.active || 'main';
  } catch(e) { /* silent */ }
}

async function workspaceSwitch(wsId) {
  if (!wsId) return;
  try {
    const r = await fetch('/workspace/switch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ id: wsId })
    });
    const d = await r.json();
    if (d.error) {
      alert('Ошибка: ' + d.error);
      return;
    }
    // Full UI refresh: graph reloaded, state reset
    if (typeof graphDrawSvg === 'function') {
      // Graph data gets loaded fresh from server on next call
      setTimeout(() => window.location.reload(), 200);
    } else {
      window.location.reload();
    }
  } catch(e) {
    alert('Switch failed: ' + e.message);
  }
}

// Workspaces management modal — полный список графов с node counts
async function workspacesOpen() {
  const modal = document.getElementById('workspaces-modal');
  if (!modal) return;
  modal.style.display = 'flex';
  const host = document.getElementById('workspaces-list');
  if (host) host.innerHTML = '<div style="padding:20px;text-align:center;color:#52525b;font-size:12px">Загружаю…</div>';
  try {
    const r = await fetch('/workspace/list');
    const d = await r.json();
    const list = d.workspaces || [];
    if (!list.length) {
      if (host) host.innerHTML = '<div style="padding:20px;text-align:center;color:#52525b;font-size:12px">Нет workspace. Создай новый.</div>';
      return;
    }
    // Sort: active first, then by last_active desc
    list.sort((a, b) => {
      if (a.active && !b.active) return -1;
      if (!a.active && b.active) return 1;
      return (b.last_active || '').localeCompare(a.last_active || '');
    });
    host.innerHTML = list.map(w => {
      const activeClass = w.active ? 'active' : '';
      const tagsStr = (w.tags || []).join(', ');
      const last = w.last_active ? new Date(w.last_active).toLocaleString('ru-RU', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'}) : '';
      const activeBadge = w.active ? '<span class="ws-badge-active">активный</span>' : '';
      const canDelete = w.id !== 'main' && !w.active;
      return `<div class="ws-item ${activeClass}" data-ws="${_esc(w.id)}">
        <div class="ws-title">
          <div class="ws-name">${_esc(w.title || w.id)} ${activeBadge} <span class="ws-id">${_esc(w.id)}</span></div>
          <div class="ws-meta">${last ? 'последний доступ: ' + last : ''}${tagsStr ? ' · тэги: ' + _esc(tagsStr) : ''}</div>
        </div>
        <div>
          <div class="ws-nodes">${w.node_count || 0}</div>
          <div class="ws-nodes-label">нод</div>
        </div>
        <div class="ws-actions">
          <button class="ws-btn" onclick="workspacesSwitchAndOpenGraph('${_esc(w.id)}', event)" title="Переключиться и открыть в Graph">${w.active ? 'в Graph →' : 'Открыть'}</button>
          ${canDelete ? `<button class="ws-btn danger" onclick="workspacesDelete('${_esc(w.id)}', event)" title="Удалить">🗑</button>` : ''}
        </div>
      </div>`;
    }).join('');
  } catch (e) {
    if (host) host.innerHTML = '<div style="color:#ef4444;padding:20px">Ошибка: ' + _esc(String(e)) + '</div>';
  }
}

function workspacesClose(ev) {
  if (ev && ev.target.closest('.weekly-content') && !ev.target.classList.contains('weekly-close')) return;
  const m = document.getElementById('workspaces-modal');
  if (m) m.style.display = 'none';
}

async function workspacesSwitchAndOpenGraph(wsId, ev) {
  if (ev) ev.stopPropagation();
  if (!wsId) return;
  try {
    const r = await fetch('/workspace/switch', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({id: wsId}),
    });
    const d = await r.json();
    if (d.error) { alert('Ошибка: ' + d.error); return; }
    // Переключить top-level tab на graph — перезагрузить страницу чтобы все
    // JS-модули (graph.js) подхватили новый workspace
    try { localStorage.setItem('open-graph-after-load', '1'); } catch(e) {}
    window.location.reload();
  } catch (e) {
    alert('Switch failed: ' + e.message);
  }
}

async function workspacesDelete(wsId, ev) {
  if (ev) ev.stopPropagation();
  if (!confirm(`Удалить workspace "${wsId}"? Все ноды и state_graph будут потеряны.`)) return;
  try {
    const r = await fetch('/workspace/delete', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({id: wsId}),
    });
    const d = await r.json();
    if (d.error) { alert('Ошибка: ' + d.error); return; }
    await workspacesOpen();
  } catch (e) {
    alert('Delete failed: ' + e.message);
  }
}

async function workspaceNewPrompt() {
  const title = prompt('Название нового workspace?');
  if (!title) return;
  const id = title.toLowerCase().replace(/[^a-z0-9_]/g, '_').substring(0, 20);
  try {
    const r = await fetch('/workspace/create', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ id: id, title: title })
    });
    const d = await r.json();
    if (d.error) { alert('Ошибка: ' + d.error); return; }
    await workspaceRefresh();
    if (confirm(`Workspace "${title}" создан. Переключиться?`)) {
      workspaceSwitch(id);
    }
  } catch(e) {
    alert('Create failed: ' + e.message);
  }
}

// ── Status refresh ─────────────────────────────────────────────────────

async function assistRefreshStatus() {
  try {
    const r = await fetch('/assist/status');
    const d = await r.json();
    if (d.energy) _assistEnergy = d.energy;
    if (d.hrv !== undefined) _assistHRV = d.hrv;
    assistUpdateHeader();
  } catch(e) {}
}

// ── Morning briefing on first open ─────────────────────────────────────

async function assistMorningBriefing() {
  try {
    const lang = (document.getElementById('lang-select') || {}).value || 'ru';
    const r = await fetch('/assist/morning', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ lang: lang })
    });
    const d = await r.json();
    if (d.text) {
      assistAddMsg('assistant', d.text, { mode_name: 'утренний брифинг' });
    }
    if (d.energy) _assistEnergy = d.energy;
    if (d.hrv !== undefined) _assistHRV = d.hrv;
    assistUpdateHeader();
  } catch(e) {
    console.warn('[assist] morning briefing error:', e);
  }
}

// ── Proactive alerts polling ───────────────────────────────────────────

let _assistLastAlertTypes = new Set();

async function assistPollAlerts() {
  if (!_assistAlertsPolling) return;
  try {
    const r = await fetch('/assist/alerts');
    const d = await r.json();
    if (d.alerts && d.alerts.length) {
      const lang = (document.getElementById('lang-select') || {}).value || 'ru';
      d.alerts.forEach(a => {
        // Scout/DMN bridges — render as chat message with card
        // Morning briefing — render as primary assistant message
        if (a.type === 'morning_briefing') {
          const key = 'morning_briefing:' + (a.hour || 0) + ':' + new Date().toDateString();
          if (_assistLastAlertTypes.has(key)) return;
          _assistLastAlertTypes.add(key);
          // Rich sections → карточки; fallback на plain text если секций нет
          const sections = Array.isArray(a.sections) ? a.sections : [];
          if (sections.length) {
            renderMorningBriefingCard(sections, a.hour);
          } else {
            const text = lang === 'ru' ? (a.text || 'Доброе утро.') : (a.text_en || a.text || 'Good morning.');
            assistAddMsg('assistant', text, { mode_name: lang === 'ru' ? 'Утро' : 'Morning' });
          }
          if (typeof _incrChatUnread === 'function') _incrChatUnread();
          return;
        }
        if ((a.type === 'scout_bridge' || a.type === 'dmn_bridge') && a.bridge) {
          const key = a.type + ':' + (a.bridge.text || '').substring(0, 30);
          if (_assistLastAlertTypes.has(key)) return;
          _assistLastAlertTypes.add(key);

          const intro = a.type === 'scout_bridge'
            ? (lang === 'ru' ? '💡 Пока ты не смотрел, я нашёл связь:' : '💡 While you were away, I found a connection:')
            : (lang === 'ru' ? '🔗 DMN-инсайт:' : '🔗 DMN insight:');
          assistAddMsg('assistant', intro, { mode_name: a.type === 'scout_bridge' ? 'Scout' : 'DMN' });
          // Bridge as card
          const container = document.getElementById('assist-messages');
          const card = document.createElement('div');
          card.style.cssText = 'align-self:stretch;margin-bottom:12px;padding:12px;background:#1e1b4b;border:1px solid #4338ca;border-radius:12px;';
          card.innerHTML = `
            <div style="font-size:10px;color:#818cf8;font-weight:600;margin-bottom:6px;">BRIDGE · quality ${Math.round((a.bridge.quality||0)*100)}%</div>
            <div style="font-size:14px;color:#e4e4e7;margin-bottom:8px;">${_esc(a.bridge.text)}</div>
            ${a.bridge.synthesis ? `<div style="font-size:12px;color:#cbd5e1;font-style:italic;">${_esc(a.bridge.synthesis.substring(0, 200))}${a.bridge.synthesis.length > 200 ? '...' : ''}</div>` : ''}
          `;
          container.appendChild(card);
          container.scrollTop = container.scrollHeight;
          return;
        }

        // Plan reminder: за N минут до события — карточка с «Начать сейчас»
        if (a.type === 'plan_reminder' && a.plan_id) {
          const key = 'plan_reminder:' + a.plan_id + ':' + (a.for_date || '');
          if (_assistLastAlertTypes.has(key)) return;
          _assistLastAlertTypes.add(key);
          const container = document.getElementById('assist-messages');
          if (!container) return;
          const empty = container.querySelector('.assist-empty');
          if (empty) empty.remove();
          const card = document.createElement('div');
          card.className = 'assist-msg assist-assistant';
          card.style.cssText = 'max-width:90%;padding:12px 14px;background:#1c1917;border:1px solid #78350f;border-radius:12px;margin-bottom:12px;';
          const cat = a.plan_category ? `<span style="font-size:10px;color:#a1a1aa;background:#27272a;padding:2px 6px;border-radius:4px;margin-left:6px">${_esc(a.plan_category)}</span>` : '';
          card.innerHTML = `
            <div style="font-size:10px;color:#f59e0b;font-weight:600;margin-bottom:6px">⏰ НАПОМИНАНИЕ · через ${a.minutes_before} мин</div>
            <div style="font-size:14px;color:#e4e4e7;margin-bottom:10px">${_esc(a.plan_name)}${cat}</div>
            <div style="display:flex;gap:8px;flex-wrap:wrap">
              <button class="activity-btn activity-btn-primary" onclick="planReminderStart('${_esc(a.plan_id)}','${_esc(a.plan_name)}','${_esc(a.plan_category || '')}','${_esc(a.for_date || '')}',this)">Начать сейчас</button>
              <button class="activity-btn" onclick="planSkipClick('${_esc(a.plan_id)}','${_esc(a.for_date || '')}'); this.closest('.assist-msg').remove()">Пропустить</button>
              <button class="activity-btn" onclick="this.closest('.assist-msg').remove()">Позже</button>
            </div>`;
          container.appendChild(card);
          container.scrollTop = container.scrollHeight;
          if (typeof _incrChatUnread === 'function') _incrChatUnread();
          return;
        }

        // Evening retrospective: «Ретро дня» → open check-in + показ unfinished
        if (a.type === 'evening_retro') {
          const key = 'evening_retro:' + new Date().toDateString();
          if (_assistLastAlertTypes.has(key)) return;
          _assistLastAlertTypes.add(key);
          const container = document.getElementById('assist-messages');
          if (!container) return;
          const empty = container.querySelector('.assist-empty');
          if (empty) empty.remove();
          const un = a.unfinished || [];
          const unList = un.length ? un.map(u => {
            const t = u.planned_ts ? new Date(u.planned_ts*1000).toLocaleTimeString('ru-RU', {hour:'2-digit', minute:'2-digit'}) : '';
            return `<div style="padding:6px 10px;background:#27272a;border-radius:6px;margin-bottom:4px;display:flex;gap:8px;font-size:12px">
              <span style="color:#71717a;min-width:42px">${t}</span>
              <span style="flex:1;color:#e4e4e7">${_esc(u.name)}</span>
              <button class="activity-btn" style="padding:2px 8px" onclick="planSkipClick('${_esc(u.id)}','', event).then(() => this.closest('.assist-msg').querySelector('.retro-refresh')?.click())">пропустить</button>
            </div>`;
          }).join('') : '<div style="color:#10b981;font-size:12px">Всё выполнено!</div>';
          const card = document.createElement('div');
          card.className = 'assist-msg assist-assistant';
          card.style.cssText = 'max-width:95%;padding:14px 16px;background:#1e1b4b;border:1px solid #4338ca;border-radius:12px;margin-bottom:12px;';
          card.innerHTML = `
            <div style="font-size:10px;color:#818cf8;font-weight:600;margin-bottom:8px">🌙 РЕТРО ДНЯ</div>
            <div style="font-size:13px;color:#e4e4e7;margin-bottom:10px">${_esc(a.text)}</div>
            <div style="margin-bottom:10px">${unList}</div>
            <div style="display:flex;gap:8px">
              <button class="activity-btn activity-btn-primary" onclick="openCheckin()">Открыть check-in</button>
              <button class="activity-btn retro-refresh" style="display:none" onclick="planRender()">refresh</button>
              <button class="activity-btn" onclick="this.closest('.assist-msg').remove()">Позже</button>
            </div>`;
          container.appendChild(card);
          container.scrollTop = container.scrollHeight;
          return;
        }

        // Low-energy heavy-decision guard: карточка с кнопкой «Перенести»
        if (a.type === 'low_energy_heavy' && a.goal_id) {
          const key = 'low_energy_heavy:' + a.goal_id;
          if (_assistLastAlertTypes.has(key)) return;
          _assistLastAlertTypes.add(key);
          const text = lang === 'ru' ? a.text : (a.text_en || a.text);
          assistAddMsg('assistant', text, { mode_name: lang === 'ru' ? 'Защита' : 'Guard' });
          const container = document.getElementById('assist-messages');
          const card = document.createElement('div');
          card.style.cssText = 'align-self:stretch;margin-bottom:12px;padding:12px;background:#1c1917;border:1px solid #78350f;border-radius:12px;';
          card.innerHTML = `
            <div style="font-size:10px;color:#f59e0b;font-weight:600;margin-bottom:6px;">LOW ENERGY · ${a.energy}/100</div>
            <div style="font-size:13px;color:#e4e4e7;margin-bottom:10px;">${_esc(a.goal_text || '')}</div>
            <div style="display:flex;gap:8px;">
              <button class="activity-btn activity-btn-primary" onclick="lowEnergyPostpone('${a.goal_id}', this)">${lang==='ru'?'Перенести на утро':'Move to morning'}</button>
              <button class="activity-btn" onclick="this.closest('div[style*=\\'border\\']').remove()">${lang==='ru'?'Нет, сейчас':'No, now'}</button>
            </div>`;
          container.appendChild(card);
          container.scrollTop = container.scrollHeight;
          return;
        }

        const key = a.type;
        if (_assistLastAlertTypes.has(key)) return;
        _assistLastAlertTypes.add(key);
        const text = lang === 'ru' ? a.text : (a.text_en || a.text);
        assistAddWarning(text);
        if (typeof _incrChatUnread === 'function') _incrChatUnread();
      });
    } else {
      // Reset seen alerts when none active — allows re-alerting later
      // But keep bridge keys forever so they don't repeat
      const bridgeKeys = [..._assistLastAlertTypes].filter(k => k.startsWith('scout_bridge:') || k.startsWith('dmn_bridge:'));
      _assistLastAlertTypes.clear();
      bridgeKeys.forEach(k => _assistLastAlertTypes.add(k));
    }
    if (d.energy) _assistEnergy = d.energy;
    if (d.hrv !== undefined) _assistHRV = d.hrv;
    assistUpdateHeader();
  } catch(e) {}

  setTimeout(assistPollAlerts, 30000);  // every 30s
}

function assistStartAlertPolling() {
  if (_assistAlertsPolling) return;
  _assistAlertsPolling = true;
  assistPollAlerts();
}

// ── Briefing food suggestion: «Выбери для меня» → /assist для LLM ──
async function briefingAcceptFood() {
  const lang = (document.getElementById('lang-select') || {}).value || 'ru';
  const msg = lang === 'ru' ? 'что покушать на завтрак' : 'what to eat for breakfast';
  try {
    // Простой путь: отправить через главный /assist endpoint
    const r = await fetch('/assist', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ message: msg, lang }),
    });
    const d = await r.json();
    if (d.text) {
      assistAddMsg('user', msg, { mode_name: '' });
      const metaName = d.mode_name ? String(d.mode_name) : (d.mode || '');
      assistAddMsg('assistant', d.text, { mode_name: metaName });
      if (d.cards) {
        const container = document.getElementById('assist-messages');
        d.cards.forEach(c => {
          const el = assistRenderCard(c);
          container.appendChild(el);
          _chatStorePush({ kind: 'card', card: c });
        });
        container.scrollTop = container.scrollHeight;
      }
    }
  } catch (e) { /* silent */ }
}

// ── Daily check-in: ручной subjective-сигнал (когда HRV off) ─────────
async function openCheckin() {
  const modal = document.getElementById('checkin-modal');
  if (!modal) return;
  modal.style.display = 'flex';

  // Предзаполнить последним check-in'ом (если был за 24ч)
  try {
    const r = await fetch('/checkin/latest');
    const d = await r.json();
    const e = d.entry;
    if (e) {
      const setVal = (id, valId, v, def) => {
        const val = (v !== null && v !== undefined) ? v : def;
        const el = document.getElementById(id);
        const vEl = document.getElementById(valId);
        if (el) el.value = val;
        if (vEl) vEl.textContent = val;
      };
      setVal('checkin-energy',  'checkin-energy-val',  e.energy,  60);
      setVal('checkin-focus',   'checkin-focus-val',   e.focus,   60);
      setVal('checkin-stress',  'checkin-stress-val',  e.stress,  40);
      setVal('checkin-expected','checkin-expected-val',e.expected, 0);
      setVal('checkin-reality', 'checkin-reality-val', e.reality,  0);
      const noteEl = document.getElementById('checkin-note');
      if (noteEl) noteEl.value = e.note || '';
    }
  } catch(err) { /* silent */ }

  // История за последние 14 дней
  try {
    const r = await fetch('/checkin/history?days=14');
    const d = await r.json();
    const host = document.getElementById('checkin-history');
    if (host) {
      const items = (d.items || []).slice(0, 10);
      if (!items.length) {
        host.innerHTML = '<div style="color:#52525b;font-size:11px;text-align:center">Нет истории check-in</div>';
      } else {
        const avg = d.averages || {};
        const avgLine = avg.n ? `<div style="margin-bottom:8px;color:#a1a1aa">7-day avg: energy ${avg.energy_mean ?? '—'} · focus ${avg.focus_mean ?? '—'} · stress ${avg.stress_mean ?? '—'} · surprise ${avg.surprise_mean ?? '—'}</div>` : '';
        const lines = items.map(it => {
          const dt = new Date((it.ts || 0) * 1000).toLocaleString('ru-RU', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'});
          const parts = [];
          if (it.energy !== null && it.energy !== undefined) parts.push('E' + it.energy);
          if (it.focus !== null && it.focus !== undefined) parts.push('F' + it.focus);
          if (it.stress !== null && it.stress !== undefined) parts.push('S' + it.stress);
          if (it.expected !== null && it.reality !== null && it.expected !== undefined && it.reality !== undefined) {
            const surprise = it.reality - it.expected;
            parts.push('Δ' + (surprise > 0 ? '+' : '') + surprise);
          }
          return `<div class="checkin-history-item">
            <span>${dt}</span>
            <span>${parts.join(' · ')}</span>
          </div>`;
        }).join('');
        host.innerHTML = avgLine + lines;
      }
    }
  } catch(err) { /* silent */ }
}

function closeCheckin(ev) {
  if (ev && ev.target.closest('.weekly-content') && !ev.target.classList.contains('weekly-close')) return;
  const m = document.getElementById('checkin-modal');
  if (m) m.style.display = 'none';
}

async function saveCheckin() {
  const body = {
    energy:   parseInt(document.getElementById('checkin-energy').value, 10),
    focus:    parseInt(document.getElementById('checkin-focus').value, 10),
    stress:   parseInt(document.getElementById('checkin-stress').value, 10),
    expected: parseInt(document.getElementById('checkin-expected').value, 10),
    reality:  parseInt(document.getElementById('checkin-reality').value, 10),
    note:     (document.getElementById('checkin-note').value || '').trim(),
  };
  try {
    const r = await fetch('/checkin', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (d.ok) {
      closeCheckin();
      // Force refresh neurochem panel — UserState изменился
      try {
        const st = await (await fetch('/assist/state')).json();
        _updateNeurochemPanel(st);
        if (st.user_state?.energy) _assistEnergy = st.user_state.energy;
        assistUpdateHeader();
      } catch(e) {}
    }
  } catch (e) { /* silent */ }
}

// Map action-id → handler-function-name (for onclick inlining in briefing sections)
const _BRIEF_ACTION_MAP = {
  'food_suggest': 'briefingAcceptFood',
  'open_checkin': 'openCheckin',
  'open_plan': 'briefingOpenPlan',
};

function briefingOpenPlan() {
  const det = document.getElementById('plan-panel');
  if (det) { det.open = true; planRender(); det.scrollIntoView({block:'start'}); }
}

// ── Morning briefing: structured sections renderer (mockup-style) ───
function renderMorningBriefingCard(sections, hour) {
  const container = document.getElementById('assist-messages');
  if (!container) return;
  const empty = container.querySelector('.assist-empty');
  if (empty) empty.remove();

  const now = new Date();
  const dateStr = now.toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' });

  const _briefActionHtml = (actions) => {
    if (!Array.isArray(actions) || !actions.length) return '';
    return '<div class="brief-actions">' + actions.map(a => {
      const fn = _BRIEF_ACTION_MAP[a.action] || 'console.warn';
      return `<button class="activity-btn activity-btn-primary" onclick="${fn}(); this.closest('.brief-section').classList.add('acted')">${_esc(a.label || 'OK')}</button>`;
    }).join('') + '</div>';
  };
  const sectionsHtml = sections.map(s => {
    const kind = s.kind || 'neutral';
    return `<div class="brief-section brief-${_esc(kind)}">
      <span class="brief-emoji">${_esc(s.emoji || '•')}</span>
      <div class="brief-body">
        <div class="brief-title">${_esc(s.title || '')}</div>
        ${s.subtitle ? `<div class="brief-subtitle">${_esc(s.subtitle)}</div>` : ''}
        ${_briefActionHtml(s.actions)}
      </div>
    </div>`;
  }).join('');

  const card = document.createElement('div');
  card.className = 'assist-msg assist-assistant brief-card';
  card.innerHTML = `
    <div class="brief-header">
      <span class="brief-greeting">☀️ Доброе утро</span>
      <span class="brief-date">${_esc(dateStr)}</span>
    </div>
    <div class="brief-sections">${sectionsHtml}</div>`;
  container.appendChild(card);
  container.scrollTop = container.scrollHeight;

  // Persist в chat history КАК CARD — чтобы не потерять при reload
  _chatStorePush({ kind: 'card', card: { type: 'morning_briefing', sections: sections, hour: hour } });
}

async function lowEnergyPostpone(goalId, btn) {
  if (!goalId) return;
  try {
    const r = await fetch('/goals/postpone', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: goalId, until: 'tomorrow' })
    });
    const d = await r.json();
    if (btn) {
      const card = btn.closest('div[style*=\"border\"]');
      if (card) card.remove();
    }
    const lang = (document.getElementById('lang-select') || {}).value || 'ru';
    assistAddMsg('assistant',
      lang === 'ru' ? `✓ Перенёс на завтра (${d.postponed_until || ''}).` : `✓ Postponed to tomorrow.`,
      { mode_name: lang === 'ru' ? 'Защита' : 'Guard' });
  } catch (e) { /* silent */ }
}

// ── HRV control ────────────────────────────────────────────────────────

function _hrvToast(text, level) {
  // Эфемерный тост в углу (НЕ в чат-историю) — статус старта/остановки
  // HRV не должен жить в chat history, это просто временный индикатор.
  let host = document.getElementById('hrv-toast');
  if (!host) {
    host = document.createElement('div');
    host.id = 'hrv-toast';
    host.style.cssText = 'position:fixed;bottom:16px;left:50%;transform:translateX(-50%);'
      + 'background:#1f1f23;color:#e4e4e7;padding:8px 14px;border-radius:8px;'
      + 'border:1px solid #3f3f46;font-size:12px;z-index:9999;opacity:0;'
      + 'transition:opacity .25s;pointer-events:none';
    document.body.appendChild(host);
  }
  host.textContent = text;
  host.style.borderColor = level === 'ok' ? '#166534' : level === 'err' ? '#7f1d1d' : '#3f3f46';
  host.style.opacity = '1';
  clearTimeout(host._t);
  host._t = setTimeout(() => { host.style.opacity = '0'; }, 2200);
}

async function assistHRVToggle(mode) {
  // Toggle — источник истины /hrv/status (не локальный `_assistHRV` который
  // синхронизируется с задержкой через polling). Второй клик сразу после
  // первого иначе стартовал бы симулятор повторно вместо стопа.
  mode = mode || 'simulator';
  let running = false;
  try {
    const st = await (await fetch('/hrv/status')).json();
    running = !!st.running;
  } catch(e) { /* silent — assume stopped */ }

  const btn = document.querySelector('.assist-hrv-btn');
  if (running) {
    try {
      await fetch('/hrv/stop', { method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}' });
    } catch(e) {}
    _assistHRV = null;
    _assistHRVHistory = [];
    // Мгновенно переключаем текст кнопки — не ждём следующего poll'а
    if (btn) { btn.textContent = 'Start HRV'; btn.classList.remove('running'); }
    assistUpdateHeader();
    _hrvToast('HRV off', 'info');
    return;
  }
  try {
    const r = await fetch('/hrv/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ mode: mode })
    });
    const d = await r.json();
    if (d.ok) {
      _hrvToast('HRV on (' + mode + ')', 'ok');
      if (btn) { btn.textContent = 'Stop HRV'; btn.classList.add('running'); }
      setTimeout(assistHRVPoll, 500);
    } else {
      _hrvToast('HRV start failed', 'err');
    }
  } catch(e) {
    _hrvToast('HRV start error', 'err');
  }
}

// Обратная совместимость: старый код (HRV simulator panel «Restart») зовёт assistHRVStart
async function assistHRVStart(mode) {
  mode = mode || 'simulator';
  try {
    const r = await fetch('/hrv/start', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ mode: mode }),
    });
    const d = await r.json();
    if (d.ok) {
      _hrvToast('HRV on (' + mode + ')', 'ok');
      setTimeout(assistHRVPoll, 3000);
    }
  } catch(e) {}
}

async function assistHRVPoll() {
  try {
    const r = await fetch('/hrv/metrics');
    const d = await r.json();
    if (d.baddle_state) {
      _assistHRV = d.baddle_state;
      assistUpdateHeader();
    }
  } catch(e) {}
  // Re-poll if HRV is running
  const sr = await fetch('/hrv/status').then(r => r.json()).catch(() => ({}));
  if (sr && sr.running) {
    setTimeout(assistHRVPoll, 5000);
  }
}

async function assistHRVSimSliders() {
  const hrEl = document.getElementById('sim-hr');
  const cohEl = document.getElementById('sim-coherence');
  const actEl = document.getElementById('sim-activity');
  if (!hrEl || !cohEl) return;
  const body = {
    hr: parseFloat(hrEl.value),
    coherence: parseFloat(cohEl.value),
  };
  if (actEl) body.activity = parseFloat(actEl.value);
  try {
    await fetch('/hrv/simulate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
  } catch(e) {}
}

// ── Show/hide advanced graph view ──────────────────────────────────────

function assistToggleAdvanced() {
  const assistView = document.getElementById('assist-view');
  const graphView = document.getElementById('cfg-graph');
  const chatView = document.getElementById('cfg-chat');
  const btn = document.getElementById('assist-advanced-btn');

  if (!assistView || !graphView) return;

  const showingAdvanced = graphView.style.display !== 'none' && !graphView.classList.contains('hidden');

  if (showingAdvanced) {
    // Switch to assistant
    assistView.style.display = '';
    graphView.style.display = 'none';
    graphView.classList.add('hidden');
    if (chatView) { chatView.style.display = 'none'; chatView.classList.add('hidden'); }
    if (btn) btn.textContent = '⚙ Advanced';
  } else {
    // Switch to graph (advanced)
    assistView.style.display = 'none';
    graphView.style.display = '';
    graphView.classList.remove('hidden');
    if (btn) btn.textContent = '💬 Chat';
  }
}

// ── Init on page load ──────────────────────────────────────────────────

function assistInit() {
  // Bind input enter
  const input = document.getElementById('assist-input');
  if (input) {
    input.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        assistSend();
      }
    });
  }

  // Restore chat history from localStorage
  _restoreChatHistory();

  // Status on load
  assistRefreshStatus();

  // Show morning briefing if first open after 6 AM
  const now = new Date();
  const hour = now.getHours();
  const lastBriefing = localStorage.getItem('assist-last-briefing');
  const today = now.toISOString().slice(0, 10);
  if (hour >= 5 && lastBriefing !== today) {
    setTimeout(() => {
      assistMorningBriefing();
      localStorage.setItem('assist-last-briefing', today);
    }, 800);
  }

  // Start alert polling
  assistStartAlertPolling();

  // Start neurochem polling (v5d panel)
  assistStartNeurochemPolling();

  // Populate workspace selector (v4)
  workspaceRefresh();
}

// ── Activity bar (ручной трекер «что я сейчас делаю») ───────────────────
// Ground-truth слой: каждая задача → event в activity.jsonl + нода type=activity
// в текущем workspace-графе. День восстанавливается replay'ем событий.

let _activityTimerInt = null;
let _activityStartedAt = null;  // ms epoch, для локального тикания таймера

function _activityFmt(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = String(Math.floor(sec / 3600)).padStart(2, '0');
  const m = String(Math.floor((sec % 3600) / 60)).padStart(2, '0');
  const s = String(sec % 60).padStart(2, '0');
  return `${h}:${m}:${s}`;
}

function _activityTick() {
  if (_activityStartedAt == null) return;
  const el = document.getElementById('activity-timer');
  if (el) el.textContent = _activityFmt((Date.now() - _activityStartedAt) / 1000);
}

async function activityRefresh() {
  try {
    const r = await fetch('/activity/active');
    const d = await r.json();
    _renderActivityState(d);
  } catch (e) { /* silent */ }
  // Сегодняшний summary — реже (раз в 30с)
  try {
    const r2 = await fetch('/activity/today');
    const d2 = await r2.json();
    _renderActivitySummary(d2);
  } catch (e) { /* silent */ }
}

function _renderActivitySummary(sum) {
  const el = document.getElementById('activity-today-summary');
  if (!el || !sum) return;
  const hours = sum.total_tracked_h || 0;
  const n = sum.activity_count || 0;
  if (n === 0) {
    el.textContent = '';
    return;
  }
  el.textContent = `сегодня ${hours.toFixed(1)}ч · ${n} задач`;
}

function _renderActivityState(data) {
  const active = (data && data.active) || null;
  const templates = (data && data.templates) || [];

  const statusEl = document.getElementById('activity-status');
  const nameEl   = document.getElementById('activity-name');
  const timerEl  = document.getElementById('activity-timer');
  const inputEl  = document.getElementById('activity-input');
  const startBtn = document.getElementById('activity-start-btn');
  const nextBtn  = document.getElementById('activity-next-btn');
  const stopBtn  = document.getElementById('activity-stop-btn');

  if (!statusEl) return;

  // Render templates (один раз при каждом refresh — дёшево)
  const tplEl = document.getElementById('activity-templates');
  if (tplEl) {
    tplEl.innerHTML = '';
    templates.forEach(t => {
      const btn = document.createElement('button');
      btn.className = 'activity-template-btn';
      btn.type = 'button';
      btn.textContent = (t.emoji ? t.emoji + ' ' : '') + t.name;
      btn.title = t.category ? `категория: ${t.category}` : '';
      btn.addEventListener('click', () => activityStartFromTemplate(t));
      tplEl.appendChild(btn);
    });
  }

  // Stop local timer
  if (_activityTimerInt) { clearInterval(_activityTimerInt); _activityTimerInt = null; }

  if (active) {
    statusEl.classList.remove('activity-status-idle');
    statusEl.classList.add('activity-status-active');
    nameEl.textContent = active.name || '(без названия)';
    _activityStartedAt = (active.started_at || 0) * 1000;
    _activityTick();
    _activityTimerInt = setInterval(_activityTick, 1000);
    if (inputEl) inputEl.style.display = 'none';
    if (startBtn) startBtn.style.display = 'none';
    if (nextBtn) nextBtn.style.display = '';
    if (stopBtn) stopBtn.style.display = '';
  } else {
    statusEl.classList.remove('activity-status-active');
    statusEl.classList.add('activity-status-idle');
    nameEl.textContent = 'Нет активной задачи';
    timerEl.textContent = '00:00:00';
    _activityStartedAt = null;
    if (inputEl) inputEl.style.display = 'none';
    if (startBtn) { startBtn.style.display = ''; startBtn.textContent = '＋ Начать'; }
    if (nextBtn) nextBtn.style.display = 'none';
    if (stopBtn) stopBtn.style.display = 'none';
  }
}

function activityStartClick() {
  // Toggle input
  const inputEl = document.getElementById('activity-input');
  const startBtn = document.getElementById('activity-start-btn');
  if (!inputEl) return;
  if (inputEl.style.display === 'none') {
    inputEl.style.display = '';
    inputEl.value = '';
    inputEl.focus();
    if (startBtn) startBtn.textContent = 'OK';
  } else {
    activitySubmitInput();
  }
}

function activityNextClick() {
  const inputEl = document.getElementById('activity-input');
  if (!inputEl) return;
  inputEl.style.display = '';
  inputEl.value = '';
  inputEl.focus();
  inputEl.dataset.mode = 'next';
}

async function activitySubmitInput() {
  const inputEl = document.getElementById('activity-input');
  if (!inputEl) return;
  const name = (inputEl.value || '').trim();
  if (!name) { inputEl.focus(); return; }
  inputEl.value = '';
  inputEl.style.display = 'none';
  delete inputEl.dataset.mode;
  await _activityStartRequest(name, null);
}

async function activityStartFromTemplate(t) {
  if (!t || !t.name) return;
  await _activityStartRequest(t.name, t.category || null);
}

async function _activityStartRequest(name, category) {
  try {
    const r = await fetch('/activity/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name, category: category || null })
    });
    const d = await r.json();
    if (d && d.error) {
      assistAddMsg('system', 'Activity start: ' + d.error);
    }
  } catch (e) { /* silent */ }
  await activityRefresh();
}

async function activityStopClick() {
  try {
    await fetch('/activity/stop', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: 'manual' })
    });
  } catch (e) { /* silent */ }
  await activityRefresh();
}

function activityBindInput() {
  const inputEl = document.getElementById('activity-input');
  if (!inputEl || inputEl._bound) return;
  inputEl._bound = true;
  inputEl.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      activitySubmitInput();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      inputEl.value = '';
      inputEl.style.display = 'none';
      const startBtn = document.getElementById('activity-start-btn');
      if (startBtn) startBtn.textContent = '＋ Начать';
    }
  });
}

// ── Timeline: горизонтальная лента 0-24h за сегодня ──────────────────
const _ACTIVITY_CATEGORY_COLOR = {
  work:     '#6366f1',
  food:     '#f59e0b',
  health:   '#10b981',
  social:   '#ec4899',
  learning: '#8b5cf6',
  uncategorized: '#52525b',
};

async function activityRenderTimeline() {
  const svg = document.getElementById('activity-timeline-svg');
  const badge = document.getElementById('activity-timeline-badge');
  if (!svg) return;
  try {
    const r = await fetch('/activity/history?limit=200');
    const d = await r.json();
    const acts = (d.activities || []).slice();
    // Фильтр на сегодня (локальный день)
    const now = new Date();
    const dayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
    const dayEnd = dayStart + 86400;
    const today = acts.filter(a => {
      const s = a.started_at || 0;
      const e = a.stopped_at || (now.getTime() / 1000);
      return e >= dayStart && s <= dayEnd;
    });

    // viewBox: 1440 minutes × 56 height
    svg.innerHTML = '';
    // Вертикальные линии каждые 6 часов (6/12/18)
    [6, 12, 18].forEach(h => {
      const x = h * 60;
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', x); line.setAttribute('x2', x);
      line.setAttribute('y1', 0); line.setAttribute('y2', 56);
      line.setAttribute('stroke', '#1f1f23');
      line.setAttribute('stroke-width', '1');
      svg.appendChild(line);
    });
    // Сейчас-маркер
    const nowMin = ((now.getTime() / 1000) - dayStart) / 60;
    if (nowMin >= 0 && nowMin <= 1440) {
      const nl = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      nl.setAttribute('x1', nowMin); nl.setAttribute('x2', nowMin);
      nl.setAttribute('y1', 0); nl.setAttribute('y2', 56);
      nl.setAttribute('stroke', '#ef4444');
      nl.setAttribute('stroke-width', '1');
      nl.setAttribute('stroke-dasharray', '3,3');
      svg.appendChild(nl);
    }

    today.forEach(a => {
      const s = Math.max(dayStart, a.started_at || dayStart);
      const e = Math.min(dayEnd, a.stopped_at || (now.getTime() / 1000));
      const x1 = Math.max(0, (s - dayStart) / 60);
      const x2 = Math.min(1440, (e - dayStart) / 60);
      const w = Math.max(1, x2 - x1);
      const color = _ACTIVITY_CATEGORY_COLOR[a.category || 'uncategorized']
                 || _ACTIVITY_CATEGORY_COLOR.uncategorized;
      const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      rect.setAttribute('class', 'tl-block');
      rect.setAttribute('x', x1);
      rect.setAttribute('y', a.status === 'active' ? 6 : 8);
      rect.setAttribute('width', w);
      rect.setAttribute('height', a.status === 'active' ? 44 : 40);
      rect.setAttribute('rx', 3);
      rect.setAttribute('fill', color);
      rect.setAttribute('fill-opacity', a.status === 'active' ? 0.95 : 0.75);
      rect.setAttribute('data-id', a.id);
      const dur = Math.round(((e - s)) / 60);
      const startTime = new Date(s * 1000).toLocaleTimeString('ru-RU', {hour:'2-digit', minute:'2-digit'});
      const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
      title.textContent = `${a.name} · ${a.category || 'uncategorized'} · ${startTime} · ${dur} мин`;
      rect.appendChild(title);
      rect.addEventListener('click', () => activityShowEditPopup(a));
      svg.appendChild(rect);
    });

    if (badge) {
      const totalMin = today.reduce((acc, a) => {
        const s = Math.max(dayStart, a.started_at || dayStart);
        const e = Math.min(dayEnd, a.stopped_at || (now.getTime() / 1000));
        return acc + Math.max(0, (e - s) / 60);
      }, 0);
      badge.textContent = `· ${today.length} задач · ${(totalMin / 60).toFixed(1)}ч`;
    }
  } catch (e) { /* silent */ }
}

// ── Plan: карта будущего (events + recurring habits) ─────────────────
async function planRender() {
  const host = document.getElementById('plan-list');
  const badge = document.getElementById('plan-badge');
  if (!host) return;
  try {
    const r = await fetch('/plan/today');
    const d = await r.json();
    const items = d.schedule || [];
    if (badge) {
      const todo = items.filter(i => !i.done && !i.skipped).length;
      badge.textContent = items.length ? `· ${todo}/${items.length}` : '';
    }
    if (!items.length) {
      host.innerHTML = '<div style="padding:10px;color:#52525b;font-size:11px;text-align:center">Пусто на сегодня. Добавь событие или habit ниже.</div>';
      return;
    }
    const now = Date.now() / 1000;
    host.innerHTML = items.map(it => {
      const t = new Date((it.planned_ts || 0) * 1000).toLocaleTimeString('ru-RU', {hour:'2-digit', minute:'2-digit'});
      const cls = it.done ? 'done' : (it.skipped ? 'skipped' : (it.planned_ts && it.planned_ts < now - 3600 ? 'overdue' : ''));
      const badges = [];
      if (it.kind === 'recurring') {
        const streakStr = it.streak > 0 ? `🔥${it.streak}` : '↻';
        badges.push(`<span class="plan-badge plan-badge-${it.streak > 0 ? 'streak' : 'recurring'}">${streakStr}</span>`);
      }
      if (it.category) badges.push(`<span class="plan-badge">${_esc(it.category)}</span>`);
      if (it.expected_difficulty) badges.push(`<span class="plan-badge plan-badge-diff">диф ${it.expected_difficulty}</span>`);
      const forDate = it.for_date || '';
      const actions = (it.done || it.skipped)
        ? ''
        : `<button class="plan-btn done" title="Выполнено" onclick="planCompleteClick('${_esc(it.id)}','${_esc(forDate)}',event)">✓</button>
           <button class="plan-btn skip" title="Пропустить" onclick="planSkipClick('${_esc(it.id)}','${_esc(forDate)}',event)">✕</button>`;
      return `<div class="plan-item ${cls}">
        <span class="plan-time">${t}</span>
        <span class="plan-name">${_esc(it.name || '—')}</span>
        <span class="plan-badges">${badges.join('')}</span>
        <span class="plan-actions">
          ${actions}
          <button class="plan-btn" title="Удалить" onclick="planDeleteClick('${_esc(it.id)}',event)">🗑</button>
        </span>
      </div>`;
    }).join('');
  } catch (e) { /* silent */ }
}

async function planAddNew() {
  const name = (document.getElementById('plan-name').value || '').trim();
  if (!name) return;
  const category = document.getElementById('plan-category').value || null;
  const time = document.getElementById('plan-time').value || '09:00';
  const recurring = document.getElementById('plan-recurring').checked;
  const diff = document.getElementById('plan-difficulty').value;
  const body = { name, category };
  if (recurring) {
    body.recurring = { days: [0,1,2,3,4,5,6], time };
  } else {
    const today = new Date();
    const [h, m] = time.split(':').map(x => parseInt(x, 10) || 0);
    const ts = new Date(today.getFullYear(), today.getMonth(), today.getDate(), h, m).getTime() / 1000;
    body.ts_start = ts;
  }
  if (diff) body.expected_difficulty = parseInt(diff, 10);
  try {
    await fetch('/plan/add', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    document.getElementById('plan-name').value = '';
    document.getElementById('plan-recurring').checked = false;
    document.getElementById('plan-difficulty').value = '';
    await planRender();
  } catch (e) { /* silent */ }
}

async function planCompleteClick(id, forDate, ev) {
  if (ev) ev.stopPropagation();
  // Optional: quick prompt for actual difficulty (для surprise feed)
  const diffStr = prompt('Фактическая сложность 1-5 (Enter = не указывать):');
  const body = { id, for_date: forDate || undefined };
  const diff = parseInt(diffStr, 10);
  if (!isNaN(diff) && diff >= 1 && diff <= 5) body.actual_difficulty = diff;
  try {
    await fetch('/plan/complete', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    await planRender();
  } catch (e) { /* silent */ }
}

async function planSkipClick(id, forDate, ev) {
  if (ev) ev.stopPropagation();
  const reason = prompt('Причина пропуска (опционально):') || '';
  try {
    await fetch('/plan/skip', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ id, for_date: forDate || undefined, reason }),
    });
    await planRender();
  } catch (e) { /* silent */ }
}

// Plan reminder «Начать сейчас» → создаёт activity + complete plan
async function planReminderStart(planId, name, category, forDate, btn) {
  try {
    // Старт activity с тем же name+category
    await fetch('/activity/start', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name, category: category || null})
    });
    // Отмечаем plan как completed (for_date = сегодня)
    await fetch('/plan/complete', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id: planId, for_date: forDate || new Date().toISOString().slice(0,10)})
    });
    if (btn) btn.closest('.assist-msg').remove();
    activityRefresh();
    planRender();
  } catch (e) { /* silent */ }
}

async function planDeleteClick(id, ev) {
  if (ev) ev.stopPropagation();
  if (!confirm('Удалить?')) return;
  try {
    await fetch('/plan/delete', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ id }),
    });
    await planRender();
  } catch (e) { /* silent */ }
}

// ── History filter by category + days ────────────────────────────────
let _historyFilter = { category: '', days: 30 };
function activityFilterSet(category, days) {
  _historyFilter = { category: category || '', days: days || 30 };
  document.querySelectorAll('.activity-filter-chip').forEach(b => {
    b.classList.toggle('active', (b.dataset.cat || '') === (category || ''));
  });
  activityRenderHistory();
}

// ── History panel: все задачи (не только сегодня) с редактированием ──
async function activityRenderHistory() {
  const host = document.getElementById('activity-history-list');
  const badge = document.getElementById('activity-history-badge');
  if (!host) return;
  try {
    const params = new URLSearchParams();
    params.set('limit', '50');
    if (_historyFilter.category) params.set('category', _historyFilter.category);
    if (_historyFilter.days) params.set('days', String(_historyFilter.days));
    const r = await fetch('/activity/history?' + params.toString());
    const d = await r.json();
    const acts = (d.activities || []);
    if (!acts.length) {
      host.innerHTML = '<div style="padding:10px;color:#52525b;font-size:11px;text-align:center">История пуста</div>';
      if (badge) badge.textContent = '';
      return;
    }
    if (badge) badge.textContent = `· ${acts.length}`;
    host.innerHTML = acts.map(a => {
      const color = _ACTIVITY_CATEGORY_COLOR[a.category || 'uncategorized']
                 || _ACTIVITY_CATEGORY_COLOR.uncategorized;
      const startMs = (a.started_at || 0) * 1000;
      const dateStr = new Date(startMs).toLocaleDateString('ru-RU', {day:'2-digit', month:'2-digit'});
      const timeStr = new Date(startMs).toLocaleTimeString('ru-RU', {hour:'2-digit', minute:'2-digit'});
      const durMin = Math.round(((a.stopped_at || Date.now()/1000) - (a.started_at || 0)) / 60);
      const hours = Math.floor(durMin / 60);
      const mins = durMin % 60;
      const durStr = hours > 0 ? `${hours}ч ${mins}м` : `${mins}м`;
      const isActive = a.status === 'active';
      return `<div class="activity-history-item ${isActive ? 'active' : ''}" data-id="${_esc(a.id)}">
        <span class="hist-dot" style="background:${color}"></span>
        <span class="hist-name">${_esc(a.name || '—')}</span>
        <span class="hist-date">${dateStr} ${timeStr}</span>
        <span class="hist-dur">${durStr}</span>
        <span class="edit-hint">✎</span>
      </div>`;
    }).join('');
    // Wire clicks
    host.querySelectorAll('.activity-history-item').forEach(item => {
      item.addEventListener('click', () => {
        const id = item.dataset.id;
        const a = acts.find(x => x.id === id);
        if (a) activityShowEditPopup(a);
      });
    });
  } catch (e) { /* silent */ }
}

function _toLocalDatetimeInput(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function _fromLocalDatetimeInput(str) {
  if (!str) return null;
  const d = new Date(str);
  if (isNaN(d.getTime())) return null;
  return d.getTime() / 1000;
}

function activityShowEditPopup(a) {
  const popup = document.getElementById('activity-edit-popup');
  if (!popup) return;
  popup.style.display = '';
  const cats = ['work','food','health','social','learning','uncategorized'];
  const opts = cats.map(c =>
    `<option value="${c}" ${a.category === c ? 'selected' : ''}>${c}</option>`).join('');
  popup.innerHTML = `
    <div style="margin-bottom:8px;font-weight:500;">Редактировать задачу</div>
    <label>Название <input id="edit-act-name" type="text" value="${_esc(a.name || '')}"></label>
    <label>Категория <select id="edit-act-cat">${opts}</select></label>
    <label>Начало <input id="edit-act-start" type="datetime-local" value="${_toLocalDatetimeInput(a.started_at)}"></label>
    <label>Конец <input id="edit-act-end" type="datetime-local" value="${_toLocalDatetimeInput(a.stopped_at)}"></label>
    <div class="edit-actions">
      <button class="activity-btn activity-btn-primary" onclick="activityEditSave('${a.id}')">Сохранить</button>
      <button class="activity-btn activity-btn-danger" onclick="activityEditDelete('${a.id}')">Удалить</button>
      <button class="activity-btn" onclick="document.getElementById('activity-edit-popup').style.display='none'">Отмена</button>
    </div>
    <div id="edit-act-err" style="color:#ef4444;font-size:11px;margin-top:6px"></div>`;
}

async function activityEditSave(id) {
  const name = (document.getElementById('edit-act-name').value || '').trim();
  const category = document.getElementById('edit-act-cat').value;
  const startStr = document.getElementById('edit-act-start').value;
  const endStr = document.getElementById('edit-act-end').value;
  const started_at = _fromLocalDatetimeInput(startStr);
  const stopped_at = _fromLocalDatetimeInput(endStr);
  if (stopped_at && started_at && stopped_at <= started_at) {
    document.getElementById('edit-act-err').textContent = 'Конец должен быть > начала';
    return;
  }
  const fields = { name, category };
  if (started_at) fields.started_at = started_at;
  if (stopped_at) fields.stopped_at = stopped_at;
  try {
    const r = await fetch('/activity/update', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({id, fields}),
    });
    const d = await r.json();
    if (d.error) {
      document.getElementById('edit-act-err').textContent = d.error;
      return;
    }
    document.getElementById('activity-edit-popup').style.display = 'none';
    activityRenderTimeline();
    activityRenderHistory();
    activityRefresh();
  } catch (e) {
    document.getElementById('edit-act-err').textContent = String(e);
  }
}

async function activityEditDelete(id) {
  if (!confirm('Удалить задачу?')) return;
  try {
    await fetch('/activity/delete', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({id}),
    });
    document.getElementById('activity-edit-popup').style.display = 'none';
    activityRenderTimeline();
    activityRenderHistory();
    activityRefresh();
  } catch (e) { /* silent */ }
}

function activityInit() {
  activityBindInput();
  activityRefresh();
  // Обновление каждые 30с (активная задача + сегодняшний summary)
  setInterval(activityRefresh, 30000);
}

// ── Manual mode selector (как в graph tab, но inline над input) ─────
let _forcedMode = 'auto';
let _modesCache = null;

async function _loadModes() {
  if (_modesCache) return _modesCache;
  try {
    const r = await fetch('/modes');
    _modesCache = await r.json();
  } catch (e) { _modesCache = []; }
  return _modesCache;
}

function _setForcedMode(mode, silent) {
  _forcedMode = mode || 'auto';
  const btn = document.getElementById('mode-chip-btn');
  const name = document.getElementById('mode-chip-name');
  if (!btn || !name) return;
  if (_forcedMode === 'auto') {
    name.textContent = 'auto';
    btn.classList.remove('forced');
  } else {
    // Найти human-readable имя
    const m = (_modesCache || []).find(x => x.id === _forcedMode);
    name.textContent = m ? (m.name || m.id) : _forcedMode;
    btn.classList.add('forced');
  }
  closeModeMenu();
}

async function toggleModeMenu(ev) {
  if (ev) ev.stopPropagation();
  const menu = document.getElementById('mode-chip-menu');
  if (!menu) return;
  if (menu.style.display !== 'none') { closeModeMenu(); return; }
  const modes = await _loadModes();
  // Рендерим
  const items = [
    {id: 'auto', name: 'Авто (LLM classify)', intro: 'Система сама выбирает режим по содержанию'}
  ].concat(modes);
  menu.innerHTML = items.map(m => `
    <button class="mode-menu-item ${m.id === _forcedMode ? 'current' : ''}" data-mode-id="${_esc(m.id)}">
      <span class="mode-name">${_esc(m.name || m.id)}</span>
      <span class="mode-desc">${_esc(m.intro || '')}</span>
    </button>
  `).join('');
  menu.querySelectorAll('.mode-menu-item').forEach(b => {
    b.addEventListener('click', () => _setForcedMode(b.dataset.modeId));
  });
  menu.style.display = 'flex';
  setTimeout(() => {
    document.addEventListener('click', _modeMenuOutsideClick, true);
    document.addEventListener('keydown', _modeMenuEsc, true);
  }, 10);
}
function closeModeMenu() {
  const menu = document.getElementById('mode-chip-menu');
  if (menu) menu.style.display = 'none';
  document.removeEventListener('click', _modeMenuOutsideClick, true);
  document.removeEventListener('keydown', _modeMenuEsc, true);
}
function _modeMenuOutsideClick(ev) {
  const menu = document.getElementById('mode-chip-menu');
  const btn = document.getElementById('mode-chip-btn');
  if (!menu) return;
  if (menu.contains(ev.target) || btn?.contains(ev.target)) return;
  closeModeMenu();
}
function _modeMenuEsc(ev) { if (ev.key === 'Escape') closeModeMenu(); }

// ── Step-deeper actions (power-user operations прямо из чата) ────────
// Каждое assistant-message получает toolbar с 5 операциями: Elaborate,
// SmartDC (сомнение), Pump (мост), Think more, Open-in-Graph. Таргет —
// последние N нод в активном workspace графе (те что были созданы
// последним /assist call'ом или наибольшие по id).

function assistAttachStepActions(cardDiv) {
  if (!cardDiv || cardDiv.dataset.stepAttached) return;
  cardDiv.dataset.stepAttached = '1';
  const bar = document.createElement('div');
  bar.className = 'msg-step-actions';
  bar.innerHTML = `
    <button class="msg-step-btn" data-act="elaborate" title="Elaborate: LLM углубит последнюю ноду — сгенерирует evidence">🔬 Углубить</button>
    <button class="msg-step-btn" data-act="smartdc"   title="SmartDC: pro vs contra + синтез над последней нодой">⚖ Сомнение</button>
    <button class="msg-step-btn" data-act="pump"      title="Pump: найти скрытую ось между двумя далёкими нодами">🔀 Мост</button>
    <button class="msg-step-btn" data-act="more"      title="Think more: сгенерировать ещё N идей на ту же тему">➕ Ещё</button>
    <button class="msg-step-btn" data-act="graph"     title="Открыть граф workspace'a">🕸 Graph</button>
  `;
  bar.querySelectorAll('.msg-step-btn').forEach(b => {
    b.addEventListener('click', () => stepAction(b.dataset.act, b));
  });
  cardDiv.appendChild(bar);
}

async function stepAction(action, btn) {
  if (action === 'graph') {
    if (typeof setMode === 'function') setMode('graph');
    return;
  }
  if (btn) { btn.disabled = true; btn.classList.add('busy'); }
  try {
    // Получить текущие ноды
    const g = await (await fetch('/graph/recalc', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}'
    })).json();
    const nodes = g.nodes || [];
    if (!nodes.length) {
      assistAddMsg('system', '⚠ Нет нод в workspace — сначала задай вопрос Baddle.');
      return;
    }
    // Последняя нода — обычно самая свежая
    const lastIdx = nodes[nodes.length - 1].id;
    const lastText = (nodes[nodes.length - 1].text || '').slice(0, 50);
    const lang = (document.getElementById('lang-select') || {}).value || 'ru';

    let endpoint, body, label;
    if (action === 'elaborate') {
      endpoint = '/graph/elaborate';
      body = { index: lastIdx, n: 3, lang };
      label = 'Углубить';
    } else if (action === 'smartdc') {
      endpoint = '/graph/smartdc';
      body = { index: lastIdx, lang };
      label = 'Сомнение (SmartDC)';
    } else if (action === 'pump') {
      endpoint = '/graph/pump';
      body = { max_iterations: 2, save: true, lang };
      label = 'Мост';
    } else if (action === 'more') {
      endpoint = '/graph/think';
      const topic = (g.meta && g.meta.topic) || lastText || 'thought';
      body = { topic, n: 3, lang };
      label = 'Ещё идеи';
    } else {
      return;
    }

    // Добавим заглушку-сообщение пока ждём
    const pendingMsg = assistAddMsg('assistant', `⋯ ${label} …`, { mode_name: label });
    const r = await fetch(endpoint, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    const d = await r.json();
    // Replace pending
    if (pendingMsg) pendingMsg.remove();

    // Извлечь результат
    let summary = '';
    let cards = [];
    if (d.error) {
      summary = 'Ошибка: ' + d.error;
    } else if (action === 'elaborate' && d.nodes) {
      const addedCount = (d.nodes || []).filter(n => (n.topic || '') && n.id > lastIdx).length;
      summary = `➕ Добавлено ${addedCount} углублений к «${lastText}»`;
    } else if (action === 'smartdc' && d.result) {
      const res = d.result;
      cards.push({
        type: 'dialectic',
        thesis: res.thesis || res.for || '—',
        antithesis: res.antithesis || res.against || '—',
        synthesis: res.synthesis || '',
        confidence_thesis: res.confidence_thesis,
        confidence_anti: res.confidence_anti,
      });
      summary = '⚖ SmartDC: pro / contra / синтез';
    } else if (action === 'pump' && d.all_bridges && d.all_bridges.length) {
      const b = d.all_bridges[0];
      summary = `🔀 Мост найден: «${(b.text || '').slice(0, 120)}» (quality ${Math.round((b.quality || 0) * 100)}%)`;
    } else if (action === 'more' && d.nodes) {
      const n = Math.max(0, (d.nodes.length || 0) - nodes.length);
      summary = `➕ Сгенерировано ${n} новых идей. Открой 🕸 Graph чтобы посмотреть.`;
    } else {
      summary = 'Готово. Открой 🕸 Graph чтобы увидеть изменения.';
    }
    const m = assistAddMsg('assistant', summary, { mode_name: label });
    if (cards.length) {
      const container = document.getElementById('assist-messages');
      cards.forEach(c => {
        const el = assistRenderCard(c);
        container.appendChild(el);
        _chatStorePush({ kind: 'card', card: c });
      });
      container.scrollTop = container.scrollHeight;
    }
  } catch (e) {
    assistAddMsg('system', 'Ошибка step: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.classList.remove('busy'); }
  }
}

// ── Chat commands dropdown (slash button) ────────────────────────────
// Список берётся из src/chat_commands.py — синхронизация через обычай.
// autoSubmit=true → отправить сразу (no-arg команды). false → вставить
// в инпут чтобы юзер заполнил аргумент.
const _CHAT_COMMANDS = [
  {icon: '💬', name: 'как я?',        template: 'как я?',        desc: 'Текущее состояние: резерв, нейрохим, задача, план', autoSubmit: true},
  {icon: '📋', name: 'план',          template: 'план',           desc: 'Что у меня на сегодня', autoSubmit: true},
  {icon: '▶', name: 'запусти ...',   template: 'запусти ',       desc: 'Стартовать задачу в трекере', autoSubmit: false},
  {icon: '⏹', name: 'стоп',          template: 'стоп',           desc: 'Остановить текущую задачу', autoSubmit: true},
  {icon: '↻', name: 'следующая ...', template: 'следующая ',     desc: 'Переключить на другую задачу', autoSubmit: false},
  {icon: '🍽', name: 'что я ел',      template: 'что я ел за неделю', desc: 'История food-активностей', autoSubmit: true},
  {icon: '📝', name: 'check-in',     template: 'check-in',       desc: 'Subjective energy/focus/stress', autoSubmit: true},
  {icon: '?',  name: 'help',          template: 'help',           desc: 'Список всех команд', autoSubmit: true},
];

function _renderCmdMenu() {
  const host = document.getElementById('chat-cmd-menu');
  if (!host || host.dataset.rendered) return;
  host.dataset.rendered = '1';
  host.innerHTML = _CHAT_COMMANDS.map((c, i) => `
    <button class="chat-cmd-item" data-cmd-idx="${i}">
      <span class="cmd-icon">${_esc(c.icon)}</span>
      <div class="cmd-body">
        <div class="cmd-name">${_esc(c.name)}</div>
        <div class="cmd-desc">${_esc(c.desc)}</div>
      </div>
    </button>`).join('');
  host.querySelectorAll('.chat-cmd-item').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.cmdIdx, 10);
      const c = _CHAT_COMMANDS[idx];
      _applyChatCommand(c);
    });
  });
}

function _applyChatCommand(c) {
  if (!c) return;
  const inp = document.getElementById('assist-input');
  if (!inp) return;
  inp.value = c.template;
  closeCmdMenu();
  if (c.autoSubmit) {
    // Небольшая задержка чтобы DOM-update + focus прошли
    setTimeout(() => { try { assistSend(); } catch(e){} }, 50);
  } else {
    inp.focus();
    // Курсор в конец (после «запусти »)
    const len = inp.value.length;
    inp.setSelectionRange(len, len);
  }
}

function toggleCmdMenu(ev) {
  if (ev) ev.stopPropagation();
  const menu = document.getElementById('chat-cmd-menu');
  const btn = document.getElementById('chat-cmd-btn');
  if (!menu) return;
  _renderCmdMenu();
  const open = menu.style.display !== 'none';
  if (open) { closeCmdMenu(); return; }
  menu.style.display = 'flex';
  btn?.classList.add('active');
  setTimeout(() => {
    document.addEventListener('click', _cmdMenuOutsideClick, true);
    document.addEventListener('keydown', _cmdMenuEscHandler, true);
  }, 10);
}
function closeCmdMenu() {
  const menu = document.getElementById('chat-cmd-menu');
  const btn = document.getElementById('chat-cmd-btn');
  if (menu) menu.style.display = 'none';
  btn?.classList.remove('active');
  document.removeEventListener('click', _cmdMenuOutsideClick, true);
  document.removeEventListener('keydown', _cmdMenuEscHandler, true);
}
function _cmdMenuOutsideClick(ev) {
  const menu = document.getElementById('chat-cmd-menu');
  const btn = document.getElementById('chat-cmd-btn');
  if (!menu) return;
  if (menu.contains(ev.target) || btn?.contains(ev.target)) return;
  closeCmdMenu();
}
function _cmdMenuEscHandler(ev) { if (ev.key === 'Escape') closeCmdMenu(); }

// ── Sub-tabs навигация внутри baddle ─────────────────────────────────
let _baddleSub = 'chat';
let _chatUnread = 0;

function setBaddleSub(sub) {
  _baddleSub = sub || 'chat';
  document.querySelectorAll('.baddle-subtab').forEach(b => {
    b.classList.toggle('active', b.dataset.sub === _baddleSub);
  });
  document.querySelectorAll('.baddle-sub-page').forEach(p => {
    p.style.display = p.dataset.subPage === _baddleSub ? '' : 'none';
  });
  // Clear unread badge когда открыли чат
  if (_baddleSub === 'chat') {
    _chatUnread = 0;
    const b = document.getElementById('sub-badge-chat');
    if (b) { b.style.display = 'none'; b.textContent = ''; }
    // Scroll to bottom of messages after tab switch
    setTimeout(() => {
      const m = document.getElementById('assist-messages');
      if (m) m.scrollTop = m.scrollHeight;
    }, 50);
  }
  // Autorefresh контента при переключении
  if (sub === 'tasks') {
    try { activityRefresh(); } catch(e) {}
    try { planRender(); } catch(e) {}
  }
  try { localStorage.setItem('baddle-subtab', _baddleSub); } catch(e) {}
}

function _incrChatUnread() {
  if (_baddleSub === 'chat') return;
  _chatUnread++;
  const b = document.getElementById('sub-badge-chat');
  if (b) { b.style.display = ''; b.textContent = String(_chatUnread); }
}

function _initModes() {
  // Preload modes для mode-chip-menu (всё равно один /modes на init'е)
  _loadModes();
  _setForcedMode('auto', /*silent=*/true);
}

function _initSubtabs() {
  let saved = 'chat';
  try { saved = localStorage.getItem('baddle-subtab') || 'chat'; } catch(e) {}
  setBaddleSub(saved);
  // Автопереход в graph-таб если была кнопка «Открыть» в workspaces modal
  try {
    if (localStorage.getItem('open-graph-after-load') === '1') {
      localStorage.removeItem('open-graph-after-load');
      setTimeout(() => {
        if (typeof setMode === 'function') setMode('graph');
      }, 400);
    }
  } catch(e) {}
}

// Auto-init when DOM ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', function () {
    assistInit(); activityInit(); _initSubtabs(); _initModes();
  });
} else {
  assistInit(); activityInit(); _initSubtabs(); _initModes();
}
