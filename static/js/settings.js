// ── Settings ─────────────────────────────────────────────────────────────
function openSettings() {
  Promise.all([
    fetch('/settings').then(r => r.json()),
    fetch('/settings/local-models').then(r => r.json())
  ]).then(async ([s, lm]) => {
    // Try to fetch API models if URL is set
    let apiModels = [];
    if (s.api_url) {
      try {
        const mr = await fetch('/settings/models', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({api_url: s.api_url, api_key: s.api_key || ''})
        });
        const md = await mr.json();
        apiModels = md.models || [];
      } catch(e) {}
    }
    document.getElementById('settings-mode').value = s.mode || 'local';
    document.getElementById('settings-api-url').value = s.api_url || '';
    document.getElementById('settings-api-key').value = s.api_key || '';
    // Populate API model select dropdown
    const apiModelSel = document.getElementById('settings-api-model-select');
    if (apiModels.length) {
      apiModelSel.innerHTML = '<option value="">-- select --</option>';
      apiModels.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m; opt.textContent = m;
        apiModelSel.appendChild(opt);
      });
      apiModelSel.value = s.api_model || '';
    }
    document.getElementById('settings-api-model').value = s.api_model || '';
    document.getElementById('settings-hybrid-graph').value = s.hybrid_graph || 'api';
    document.getElementById('settings-hybrid-embeddings').value = s.hybrid_embeddings || 'local';
    document.getElementById('settings-hybrid-chat').value = s.hybrid_chat || 'api';
    document.getElementById('settings-gpu-layers').value = s.local_gpu_layers ?? -1;
    document.getElementById('settings-ctx').value = s.local_ctx || 4096;
    // Populate embedding model dropdown from API models
    const embSel = document.getElementById('settings-embedding-model');
    embSel.innerHTML = '<option value="">(same as main model)</option>';
    (apiModels || []).forEach(m => {
      const opt = document.createElement('option');
      opt.value = m; opt.textContent = m;
      embSel.appendChild(opt);
    });
    // Also add local GGUF models
    (lm.models || []).forEach(m => {
      if (!apiModels || !apiModels.includes(m)) {
        const opt = document.createElement('option');
        opt.value = m; opt.textContent = '(local) ' + m;
        embSel.appendChild(opt);
      }
    });
    embSel.value = s.embedding_model || '';
    // Populate local models dropdown
    const sel = document.getElementById('settings-local-model');
    sel.innerHTML = '<option value="">-- select --</option>';
    (lm.models || []).forEach(m => {
      const opt = document.createElement('option');
      opt.value = m; opt.textContent = m;
      sel.appendChild(opt);
    });
    sel.value = s.local_model || s.current_model || '';
    document.getElementById('settings-current-model').textContent = `Current: ${s.current_model || 'none'}`;
    onSettingsModeChange();
    document.getElementById('settings-modal').style.display = 'flex';
  }).catch(e => console.error('openSettings error:', e));
}
function closeSettings() {
  document.getElementById('settings-modal').style.display = 'none';
}
function onSettingsModeChange() {
  const m = document.getElementById('settings-mode').value;
  const showApi = m === 'api' || m === 'hybrid';
  document.getElementById('settings-api-fields').style.display = showApi ? 'block' : 'none';
  document.getElementById('settings-hybrid-fields').style.display = m === 'hybrid' ? 'block' : 'none';
}
function reloadModel() {
  const model = document.getElementById('settings-local-model').value;
  const gpu = parseInt(document.getElementById('settings-gpu-layers').value) || -1;
  const ctx = parseInt(document.getElementById('settings-ctx').value) || 4096;
  if (!model) { alert('Select a model first'); return; }
  const btn = document.querySelector('[onclick*="reloadModel"]');
  btn.textContent = 'Loading...';
  btn.disabled = true;
  fetch('/settings/reload-model', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({model, gpu_layers: gpu, ctx})
  }).then(r => r.json()).then(d => {
    btn.textContent = '↻ Reload model';
    btn.disabled = false;
    if (d.error) { alert('Error: ' + d.error); return; }
    document.getElementById('settings-current-model').textContent = `Current: ${d.model}`;
    document.getElementById('sb-model').textContent = d.model;
    // Update settings
    fetch('/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({local_model: model, local_gpu_layers: gpu, local_ctx: ctx})
    }).catch(e => console.error('Failed to save settings:', e));
  }).catch(e => {
    btn.textContent = '↻ Reload model';
    btn.disabled = false;
    alert('Error: ' + e.message);
  });
}

function saveSettings() {
  const body = {
    mode: document.getElementById('settings-mode').value,
    api_url: document.getElementById('settings-api-url').value,
    api_key: document.getElementById('settings-api-key').value,
    api_model: document.getElementById('settings-api-model').value,
    hybrid_graph: document.getElementById('settings-hybrid-graph').value,
    hybrid_embeddings: document.getElementById('settings-hybrid-embeddings').value,
    hybrid_chat: document.getElementById('settings-hybrid-chat').value,
    local_model: document.getElementById('settings-local-model').value,
    local_gpu_layers: parseInt(document.getElementById('settings-gpu-layers').value) || -1,
    local_ctx: parseInt(document.getElementById('settings-ctx').value) || 4096,
    embedding_model: document.getElementById('settings-embedding-model').value,
  };
  fetch('/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})
    .then(r => r.json()).then(s => {
      closeSettings();
      // Update status bar to show mode
      const apiEl = document.getElementById('sb-api-mode');
      if (s.mode === 'api') apiEl.textContent = `API: ${s.api_model}`;
      else if (s.mode === 'hybrid') apiEl.textContent = `hybrid: ${s.api_model}`;
      else apiEl.textContent = 'local';
    }).catch(e => console.error('saveSettings error:', e));
}
function fetchApiModels() {
  const url = document.getElementById('settings-api-url').value.trim();
  const key = document.getElementById('settings-api-key').value.trim();
  if (!url) { alert('Enter API URL first'); return; }
  const sel = document.getElementById('settings-api-model-select');
  sel.innerHTML = '<option value="">loading...</option>';
  fetch('/settings/models', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({api_url: url, api_key: key})
  }).then(r => r.json()).then(d => {
    sel.innerHTML = '<option value="">-- select --</option>';
    if (d.error) { sel.innerHTML = `<option value="">${d.error}</option>`; return; }
    (d.models || []).forEach(m => {
      const opt = document.createElement('option');
      opt.value = m; opt.textContent = m;
      sel.appendChild(opt);
    });
    // Pre-select current model
    const cur = document.getElementById('settings-api-model').value;
    if (cur) sel.value = cur;
  }).catch(e => {
    sel.innerHTML = `<option value="">Error: ${e.message}</option>`;
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

fetch('/model/info').then(r => r.json()).then(d => { modelCtx = d.n_ctx; }).catch(e => console.error('model/info error:', e));
fetch('/settings').then(r => r.json()).then(s => {
  const apiEl = document.getElementById('sb-api-mode');
  if (s.mode === 'api') apiEl.textContent = `API: ${s.api_model}`;
  else if (s.mode === 'hybrid') apiEl.textContent = `hybrid: ${s.api_model}`;
  else apiEl.textContent = 'local';
  // Auto-open settings if no model configured
  if (s.current_model && (s.current_model.startsWith('(no ') || s.current_model === '')) {
    setTimeout(() => openSettings(), 500);
  }
}).catch(e => console.error('settings init error:', e));

function updateTokenCounter(used) {
  const el = document.getElementById('token-counter');
  if (!modelCtx || !used) { el.textContent = ''; return; }
  const pct = Math.round(used / modelCtx * 100);
  el.textContent = `${used} / ${modelCtx} tokens (${pct}%)`;
  el.style.color = pct > 90 ? '#ef4444' : pct > 70 ? '#f59e0b' : '';
}
