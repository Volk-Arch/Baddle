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

function assistClearChat() {
  if (!confirm('Очистить историю чата?')) return;
  localStorage.removeItem(CHAT_STORE_KEY);
  const container = document.getElementById('assist-messages');
  if (container) container.innerHTML = '<div class="assist-empty">Baddle готов. Напиши что угодно — цель, вопрос, гипотезу.</div>';
}

function _restoreChatHistory() {
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

  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return div;
}

// ── Render structured cards ─────────────────────────────────────────────

function assistRenderCard(card) {
  const wrapper = document.createElement('div');
  wrapper.className = 'assist-card';
  wrapper.style.cssText = 'align-self:stretch;max-width:100%;margin-bottom:12px;';

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
    const r = await fetch('/assist', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ message: text, lang: lang })
    });
    const d = await r.json();

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

  // Energy
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

  // HRV button state
  const hrvBtn = document.querySelector('.assist-hrv-btn');
  if (hrvBtn) {
    if (_assistHRV) {
      hrvBtn.textContent = 'HRV on';
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

async function assistPollNeurochem() {
  if (!_neurochemPolling) return;
  try {
    const r = await fetch('/assist/state');
    const d = await r.json();
    _updateNeurochemPanel(d);
  } catch(e) { /* silent */ }
  // Also refresh timeline when open (cheaper than fetching /graph/self every time)
  if (_timelineOpen) _refreshTimeline();
  setTimeout(assistPollNeurochem, 3000);
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

  // Dopamine phasic arrow (legacy — new dopamine is single scalar, so always hidden)
  const phasicEl = document.getElementById('neuro-da-phasic');
  if (phasicEl) phasicEl.style.display = 'none';

  // Mode badge + freeze animation
  const modeEl = document.getElementById('neuro-mode');
  if (modeEl) {
    const state = metrics.state || 'exploration';
    modeEl.textContent = _MODE_LABELS[state] || state;
    modeEl.title = 'Режим: ' + state;  // techy hint in tooltip
    modeEl.classList.toggle('freeze', state === 'protective_freeze');
  }

  // State origin badge
  const originEl = document.getElementById('neuro-origin');
  if (originEl) {
    const origin = neuro.state_origin || '1_rest';
    originEl.textContent = _ORIGIN_LABELS[origin] || origin;
    originEl.title = 'state_origin: ' + origin;
  }

  // Camera mode badge + button
  const camBadge = document.getElementById('neuro-camera');
  const camBtn = document.getElementById('neuro-camera-btn');
  const camOn = !!metrics.llm_disabled;
  if (camBadge) camBadge.style.display = camOn ? 'inline-block' : 'none';
  if (camBtn) camBtn.classList.toggle('active', camOn);
  _lastCameraState = camOn;
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
      return `<div class="neuro-timeline-item" title="${_esc(e.action)} · ${_esc(originRaw)}">
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

        const key = a.type;
        if (_assistLastAlertTypes.has(key)) return;
        _assistLastAlertTypes.add(key);
        const text = lang === 'ru' ? a.text : (a.text_en || a.text);
        assistAddWarning(text);
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

// ── HRV control ────────────────────────────────────────────────────────

async function assistHRVStart(mode) {
  mode = mode || 'simulator';
  try {
    const r = await fetch('/hrv/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ mode: mode })
    });
    const d = await r.json();
    if (d.ok) {
      assistAddMsg('system', 'HRV started (' + mode + ')');
      // Start polling metrics
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
  if (!hrEl || !cohEl) return;
  const hr = parseFloat(hrEl.value);
  const coherence = parseFloat(cohEl.value);
  try {
    await fetch('/hrv/simulate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ hr: hr, coherence: coherence })
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

// Auto-init when DOM ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', assistInit);
} else {
  assistInit();
}
