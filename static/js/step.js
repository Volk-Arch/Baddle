// ── Step mode ──────────────────────────────────────────────────────────────
let stepAutoEs = null;

function renderTop(tokens) {
  const el = document.getElementById('step-top');
  if (!tokens || !tokens.length) { el.innerHTML = '<span class="text-neutral-600">—</span>'; return; }
  el.innerHTML = tokens.map((t, i) => {
    const bar  = Math.round(t.prob * 28);
    const pct  = (t.prob * 100).toFixed(1).padStart(5);
    const txt  = JSON.stringify(t.text);
    return `<div class="flex items-center gap-1 mb-1">
      <span class="text-neutral-600 w-4 text-right shrink-0">${i+1}</span>
      <span class="text-blue-300 w-28 truncate shrink-0">${txt}</span>
      <span class="text-neutral-500 w-12 text-right shrink-0">${pct}%</span>
      <div class="prob-bar ml-1" style="width:${bar*4}px"></div>
    </div>`;
  }).join('');
}

let stepEditing = false;
let stepRoleLen = 0;  // length of role text to hide from display
let stepPromptText = '';  // user's prompt text (without role prefix)

function setStepButtons(enabled) {
  ['step-btn-next','step-btn-auto','step-btn-reset','step-btn-edit'].forEach(id => {
    document.getElementById(id).disabled = !enabled;
  });
}

function stepToggleEdit() {
  const el = document.getElementById('step-output');
  stepEditing = !stepEditing;
  el.contentEditable = stepEditing ? 'true' : 'false';
  document.getElementById('step-btn-sync').style.display = stepEditing ? '' : 'none';
  document.getElementById('step-btn-edit').textContent = stepEditing ? 'Cancel Edit' : 'Edit';
  // Disable other buttons while editing
  ['step-btn-next','step-btn-auto','step-btn-reset'].forEach(id => {
    document.getElementById(id).disabled = stepEditing;
  });
  if (stepEditing) {
    el.focus();
    // Place cursor at end
    const range = document.createRange();
    range.selectNodeContents(el);
    range.collapse(false);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }
}

async function stepSync() {
  const el = document.getElementById('step-output');
  const visibleText = el.textContent;
  const fullText = getRole() + visibleText;
  document.getElementById('step-status').textContent = 'Syncing…';
  const r = await fetch('/step/edit', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text: fullText})
  });
  const d = await r.json();
  if (d.error) { document.getElementById('step-status').textContent = 'Error: ' + d.error; return; }
  const syncIdx = d.full_text.indexOf(stepPromptText);
  el.textContent = syncIdx >= 0 ? d.full_text.slice(syncIdx) : d.full_text.slice(stepRoleLen);
  stepPromptText = el.textContent;  // update prompt text after edit
  renderTop(d.top_tokens);
  // Exit edit mode
  stepEditing = false;
  el.contentEditable = 'false';
  document.getElementById('step-btn-sync').style.display = 'none';
  document.getElementById('step-btn-edit').textContent = 'Edit';
  setStepButtons(true);
  const info = d.action === 'cut' ? `Cut to ${d.token_count} tokens`
             : d.action === 'inject' ? `Injected ${d.injected_tokens} tokens`
             : d.action === 're-eval' ? `Re-eval ${d.token_count} tokens`
             : 'No change';
  document.getElementById('step-status').textContent = info;
}

async function stepInit() {
  const raw    = document.getElementById('step-prompt').value.trim();
  const temp   = parseFloat(document.getElementById('step-temp').value) || 0;
  if (!raw) return;
  const pfx    = getRole();
  const prompt = pfx + raw;
  stepRoleLen = pfx.length;
  stepPromptText = raw;
  setStepButtons(false);
  document.getElementById('step-status').textContent = 'Initializing…';
  const r = await fetch('/step/init', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({prompt, temp, top_k: parseInt(document.getElementById('step-topk').value) || 40})
  });
  const d = await r.json();
  if (d.error) { document.getElementById('step-status').textContent = 'Error: ' + d.error; return; }
  document.getElementById('step-output').textContent = d.text.slice(stepRoleLen);
  renderTop(d.top_tokens);
  document.getElementById('step-status').textContent = 'Ready  (' + d.token_count + ' prompt tokens)';
  updateTokenCounter(d.token_count);
  setStepButtons(true);
}

async function stepNext() {
  const r = await fetch('/step/next', {method: 'POST'});
  const d = await r.json();
  if (d.error) { document.getElementById('step-status').textContent = 'Error: ' + d.error; return; }
  if (d.tok_texts && d.ents && d.tok_texts.length > 0) {
    renderHeatmap('step-output', d.tok_texts, d.ents, stepPromptText);
  } else {
    const idx = d.full_text.indexOf(stepPromptText);
    document.getElementById('step-output').textContent = idx >= 0 ? d.full_text.slice(idx) : d.full_text.slice(stepRoleLen);
  }
  renderTop(d.top_tokens);
  document.getElementById('step-status').textContent = d.is_eos ? 'EOS' : 'step ' + d.step;
  updateTokenCounter(d.total_tokens);
  if (d.is_eos) setStepButtons(false);
}

function stepAuto() {
  if (stepAutoEs) return;
  const n = parseInt(document.getElementById('step-auto-n').value) || 20;
  setStepButtons(false);
  document.getElementById('step-btn-stop').style.display = '';
  stepAutoEs = new EventSource('/step/auto?n=' + n);
  stepAutoEs.onmessage = function(e) {
    const d = JSON.parse(e.data);
    if (d.error) {
      document.getElementById('step-status').textContent = 'Error: ' + d.error;
      stepStopAuto(false); return;
    }
    if (d.done) { stepStopAuto(true); return; }
    if (d.tok_texts && d.ents && d.tok_texts.length > 0) {
      renderHeatmap('step-output', d.tok_texts, d.ents, stepPromptText);
    } else {
      const idx = d.full_text.indexOf(stepPromptText);
      document.getElementById('step-output').textContent = idx >= 0 ? d.full_text.slice(idx) : d.full_text.slice(stepRoleLen);
    }
    document.getElementById('step-status').textContent = 'step ' + d.step;
    updateTokenCounter(d.total_tokens);
    if (d.top_tokens) renderTop(d.top_tokens);
    if (d.eos) { stepStopAuto(false); document.getElementById('step-status').textContent = 'EOS'; }
  };
  stepAutoEs.onerror = function() { stepStopAuto(true); };
}

function stepUpdateParams() {
  if (!document.getElementById('step-output').textContent) return;
  fetch('/step/temp', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      temp: parseFloat(document.getElementById('step-temp').value) || 0,
      top_k: parseInt(document.getElementById('step-topk').value) || 40,
    })
  });
}

function stepStopAuto(restoreButtons) {
  if (stepAutoEs) { stepAutoEs.close(); stepAutoEs = null; }
  document.getElementById('step-btn-stop').style.display = 'none';
  if (restoreButtons !== false) setStepButtons(true);
}

async function stepReset() {
  const r = await fetch('/step/reset', {method: 'POST'});
  const d = await r.json();
  if (d.error) { document.getElementById('step-status').textContent = 'Error: ' + d.error; return; }
  const resetIdx = d.full_text.indexOf(stepPromptText);
  document.getElementById('step-output').textContent = resetIdx >= 0 ? d.full_text.slice(resetIdx) : d.full_text.slice(stepRoleLen);
  renderTop(d.top_tokens);
  document.getElementById('step-status').textContent = 'Reset';
  setStepButtons(true);
}

// Ctrl+Enter → Sync if editing, Next Token otherwise
document.addEventListener('keydown', e => {
  if (e.ctrlKey && e.key === 'Enter') {
    e.preventDefault();
    if (mode === 'step' && stepEditing) stepSync();
    else if (mode === 'step') stepNext();
    else generate();
  }
});
