// ── Global variables ─────────────────────────────────────────────────────
let mode = 'graph';
let roles = [];
let modelCtx = 0;

// ── Roles ───────────────────────────────────────────────────────────────────
let allRoles = [];
fetch('/roles').then(r => r.json()).then(data => {
  allRoles = data;
  populateRoles();
}).catch(e => console.error('roles fetch error:', e));

function populateRoles() {
  const lang = document.getElementById('lang-select').value;
  roles = allRoles.filter(r => !r.lang || r.lang === lang);
  const sel = document.getElementById('role-select');
  sel.innerHTML = '';
  roles.forEach((p, i) => {
    const opt = document.createElement('option');
    opt.value = i;
    opt.textContent = p.name;
    sel.appendChild(opt);
  });
  const custom = document.createElement('option');
  custom.value = 'custom';
  custom.textContent = '(custom)';
  sel.appendChild(custom);
  document.getElementById('role-text').value = roles.length ? roles[0].text : '';
}

function onLangChange() {
  populateRoles();
  _loadTemplateSelector();
  if (typeof onGraphModeChange === 'function') onGraphModeChange();
}

function onRoleSelect() {
  const sel = document.getElementById('role-select');
  const inp = document.getElementById('role-text');
  if (sel.value === 'custom') {
    inp.value = '';
    inp.focus();
  } else {
    inp.value = roles[parseInt(sel.value)].text;
  }
  autoGrow(inp);
}

const _langHints = { ru: 'Отвечай на русском языке.' };

function getRole() {
  const tplSel = document.getElementById('tpl-select');
  if (tplSel && tplSel.style.display !== 'none' && tplSel.value !== '' && tplSel.value !== 'none') {
    const tpl = templates[parseInt(tplSel.value)];
    if (tpl) {
      const lang = (document.getElementById('lang-select') || {}).value || 'ru';
      let text = (lang === 'en' && tpl.text_en) ? tpl.text_en : tpl.text;
      const varsDiv = document.getElementById('tpl-vars');
      varsDiv.querySelectorAll('input[data-var]').forEach(inp => {
        const re = new RegExp('[{][{]' + inp.dataset.var + '[}][}]', 'g');
        text = text.replace(re, inp.value);
      });
      return text;
    }
  }
  let text = document.getElementById('role-text').value;
  if (!text) {
    const lang = document.getElementById('lang-select').value;
    if (_langHints[lang]) text = _langHints[lang];
  }
  return text;
}

// ── Templates ──────────────────────────────────────────────────────────────
let templates = [];
function _loadTemplateSelector() {
  const sel = document.getElementById('tpl-select');
  if (!sel || !templates.length) return;
  const lang = (document.getElementById('lang-select') || {}).value || 'ru';
  const cur = sel.value;
  sel.innerHTML = '';
  sel.style.display = '';
  const none = document.createElement('option');
  none.value = 'none';
  none.textContent = lang === 'ru' ? 'Шаблон…' : 'Template…';
  sel.appendChild(none);
  templates.forEach((t, i) => {
    const opt = document.createElement('option');
    opt.value = i;
    opt.textContent = (lang === 'ru' ? t.name : t.name_en) || t.name;
    sel.appendChild(opt);
  });
  sel.value = cur || 'none';
}
fetch('/templates').then(r => r.json()).then(data => {
  templates = data;
  _loadTemplateSelector();
}).catch(e => console.error('templates fetch error:', e));

function onTplSelect() {
  const sel = document.getElementById('tpl-select');
  const varsDiv = document.getElementById('tpl-vars');
  if (sel.value === 'none') {
    varsDiv.style.display = 'none';
    varsDiv.innerHTML = '';
    return;
  }
  const tpl = templates[parseInt(sel.value)];
  if (!tpl) return;
  varsDiv.innerHTML = '';
  varsDiv.style.display = '';
  const vars = [...new Set((tpl.text.match(/[{][{]([a-zA-Z0-9_]+)[}][}]/g) || []).map(m => m.slice(2, -2)))];
  vars.forEach(v => {
    const label = document.createElement('span');
    label.className = 'text-neutral-500 text-sm';
    label.textContent = v;
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.dataset.var = v;
    inp.value = (tpl.defaults && tpl.defaults[v]) || '';
    inp.style.cssText = 'width:160px;font-size:0.875rem;';
    inp.placeholder = v;
    varsDiv.appendChild(label);
    varsDiv.appendChild(inp);
  });
}

// ── Text rendering (heatmap removed) ─────────────────────────────────────
function renderHeatmap(elOrId, toks, ents, promptText) {
  const el = typeof elOrId === 'string' ? document.getElementById(elOrId) : elOrId;
  el.textContent = (promptText || '') + toks.join('');
}
function heatmapRescale() {}

// ── Tab switching ──────────────────────────────────────────────────────────
function setMode(m) {
  mode = m;
  ['chat', 'graph'].forEach(t => {
    const el = document.getElementById('cfg-' + t);
    if (el) el.classList.toggle('hidden', t !== m);
    const tab = document.getElementById('tab-' + t);
    if (tab) tab.className = (t === m ? 'tab-active' : 'tab-inactive') + ' px-4 py-2 text-sm';
  });
  const sbMode = document.getElementById('sb-mode');
  if (sbMode) sbMode.textContent = m;
  if (typeof updateTokenCounter === 'function') updateTokenCounter(0);
}

function updateTokenCounter(used) {
  const el = document.getElementById('token-counter');
  if (!el) return;
  if (!modelCtx || !used) { el.textContent = ''; return; }
  const pct = Math.round(used / modelCtx * 100);
  el.textContent = `${used} / ${modelCtx} tokens (${pct}%)`;
  el.style.color = pct > 90 ? '#ef4444' : pct > 70 ? '#f59e0b' : '';
}
