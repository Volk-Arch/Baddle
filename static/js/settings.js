// ── Settings ─────────────────────────────────────────────────────────────

function openSettings() {
  fetch('/settings').then(r => r.json()).then(async s => {
    document.getElementById('settings-api-url').value = s.api_url || 'http://localhost:1234';
    document.getElementById('settings-api-key').value = s.api_key || '';
    document.getElementById('settings-api-model').value = s.api_model || '';
    document.getElementById('settings-embedding-model').value = s.embedding_model || '';
    document.getElementById('settings-ctx').value = s.local_ctx || 32768;
    // Neural defaults
    const setVal = (id, v) => { const el = document.getElementById(id); if (el && v !== undefined) el.value = v; };
    setVal('settings-neural-temp',      s.neural_temp !== undefined ? s.neural_temp : 0.7);
    setVal('settings-neural-topk',      s.neural_top_k !== undefined ? s.neural_top_k : 40);
    setVal('settings-neural-threshold', s.neural_threshold !== undefined ? s.neural_threshold : 0.91);
    setVal('settings-neural-novelty',   s.neural_novelty !== undefined ? s.neural_novelty : 0.85);
    setVal('settings-neural-maxtok',    s.neural_max_tokens !== undefined ? s.neural_max_tokens : 3000);
    setVal('settings-neural-seed',      s.neural_seed !== undefined ? s.neural_seed : -1);
    const liveBayes = document.getElementById('settings-live-bayes');
    if (liveBayes) liveBayes.checked = !!s.live_bayes;
    document.getElementById('settings-current-model').textContent = `Current: ${s.current_model || '(not configured)'}`;

    // Try to fetch available models if URL is set
    if (s.api_url) {
      fetchApiModels();
    }
    document.getElementById('settings-modal').style.display = 'flex';
  }).catch(e => console.error('openSettings error:', e));
}

function closeSettings() {
  document.getElementById('settings-modal').style.display = 'none';
}

function saveSettings() {
  const body = {
    api_url: document.getElementById('settings-api-url').value.trim(),
    api_key: document.getElementById('settings-api-key').value.trim(),
    api_model: document.getElementById('settings-api-model').value.trim(),
    embedding_model: document.getElementById('settings-embedding-model').value.trim(),
    local_ctx: parseInt(document.getElementById('settings-ctx').value) || 32768,
  };
  // Neural defaults
  const numFrom = (id, def) => {
    const el = document.getElementById(id);
    if (!el) return def;
    const v = parseFloat(el.value);
    return isFinite(v) ? v : def;
  };
  body.neural_temp       = numFrom('settings-neural-temp', 0.7);
  body.neural_top_k      = parseInt(numFrom('settings-neural-topk', 40)) || 40;
  body.neural_threshold  = numFrom('settings-neural-threshold', 0.91);
  body.neural_novelty    = numFrom('settings-neural-novelty', 0.85);
  body.neural_max_tokens = parseInt(numFrom('settings-neural-maxtok', 3000)) || 3000;
  body.neural_seed       = parseInt(numFrom('settings-neural-seed', -1));
  const liveBayes = document.getElementById('settings-live-bayes');
  if (liveBayes) body.live_bayes = liveBayes.checked;

  fetch('/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  }).then(r => r.json()).then(s => {
    closeSettings();
    const apiEl = document.getElementById('sb-api-mode');
    if (apiEl) apiEl.textContent = s.api_model ? `API: ${s.api_model}` : '(not configured)';
    const sbModel = document.getElementById('sb-model');
    if (sbModel) sbModel.textContent = s.api_model || '(not configured)';
  }).catch(e => console.error('saveSettings error:', e));
}

function fetchApiModels() {
  const url = document.getElementById('settings-api-url').value.trim();
  const key = document.getElementById('settings-api-key').value.trim();
  if (!url) { alert('Enter API URL first'); return; }

  const modelSel = document.getElementById('settings-api-model-select');
  const embSel = document.getElementById('settings-embedding-model-select');

  if (modelSel) modelSel.innerHTML = '<option value="">loading...</option>';
  if (embSel) embSel.innerHTML = '<option value="">loading...</option>';

  fetch('/settings/models', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({api_url: url, api_key: key})
  }).then(r => r.json()).then(d => {
    const models = d.models || [];
    const current = document.getElementById('settings-api-model').value;
    const currentEmb = document.getElementById('settings-embedding-model').value;

    if (modelSel) {
      modelSel.innerHTML = '<option value="">-- select --</option>';
      if (d.error) {
        modelSel.innerHTML = `<option value="">${d.error}</option>`;
      } else {
        models.forEach(m => {
          const opt = document.createElement('option');
          opt.value = m; opt.textContent = m;
          modelSel.appendChild(opt);
        });
        if (current) modelSel.value = current;
      }
    }

    if (embSel) {
      embSel.innerHTML = '<option value="">(none)</option>';
      models.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m; opt.textContent = m;
        embSel.appendChild(opt);
      });
      if (currentEmb) embSel.value = currentEmb;
    }
  }).catch(e => {
    if (modelSel) modelSel.innerHTML = `<option value="">Error: ${e.message}</option>`;
    if (embSel) embSel.innerHTML = `<option value="">Error: ${e.message}</option>`;
  });
}

// Sync select dropdowns to text inputs
function onApiModelSelectChange() {
  const sel = document.getElementById('settings-api-model-select');
  if (sel) document.getElementById('settings-api-model').value = sel.value;
}
function onEmbeddingSelectChange() {
  const sel = document.getElementById('settings-embedding-model-select');
  if (sel) document.getElementById('settings-embedding-model').value = sel.value;
}

// ── Reset user data ──────────────────────────────────────────────────────

function resetAllData() {
  const status = document.getElementById('settings-reset-status');
  const typed = prompt(
    'Полная очистка данных: удалит графы, state_graph, user_state, goals, profile, archive.\n\n' +
    'Settings, roles, templates останутся.\n\n' +
    'Введи "RESET" (заглавными) чтобы подтвердить:'
  );
  if (typed !== 'RESET') {
    if (typed !== null) alert('Отмена — подтверждение не совпало.');
    return;
  }
  if (status) status.textContent = 'удаляю...';
  fetch('/data/reset', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({confirm: 'RESET'})
  }).then(r => r.json()).then(d => {
    if (d.ok) {
      if (status) status.textContent = `✓ удалено: ${d.removed_count} файлов/папок`;
      setTimeout(() => { window.location.reload(); }, 1200);
    } else {
      if (status) status.textContent = `ошибка: ${d.error || '?'}`;
    }
  }).catch(e => {
    if (status) status.textContent = `ошибка: ${e.message}`;
  });
}

// Close modal on Escape
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && document.getElementById('settings-modal').style.display === 'flex') closeSettings();
});

// ── Auto-grow textareas ────────────────────────────────────────────────────
function autoGrow(el) {
  el.style.height = 'auto';
  el.style.height = el.scrollHeight + 'px';
}
document.querySelectorAll('textarea.auto-grow').forEach(el => {
  el.addEventListener('input', () => autoGrow(el));
});

// ── Initial status bar / auto-open settings ────────────────────────────────
// modelCtx and updateTokenCounter are declared in modes.js
fetch('/model/info').then(r => r.json()).then(d => { modelCtx = d.n_ctx || 32768; }).catch(() => {});

fetch('/settings').then(r => r.json()).then(s => {
  const apiEl = document.getElementById('sb-api-mode');
  if (apiEl) apiEl.textContent = s.api_model ? `API: ${s.api_model}` : '(not configured)';
  // Auto-open settings if no API configured
  if (!s.api_url || !s.api_model) {
    setTimeout(() => openSettings(), 500);
  }
}).catch(e => console.error('settings init error:', e));
