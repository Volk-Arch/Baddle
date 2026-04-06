// ── Parallel / Compare ─────────────────────────────────────────────────────
let dualIsServer = false;

function hideToStepBtns() {
  document.getElementById('btn-to-step-a').style.display = 'none';
  document.getElementById('btn-to-step-b').style.display = 'none';
}

function stopDual() {
  if (dualEs) { dualEs.close(); dualEs = null; }
  document.getElementById('btn-gen').style.display  = '';
  document.getElementById('btn-stop').style.display = 'none';
}

function generate() {
  if (dualEs) { dualEs.close(); dualEs = null; }
  dualIsServer = false;
  hideToStepBtns();
  ['output-a','output-b'].forEach(id => { document.getElementById(id).textContent = ''; document.getElementById(id).innerHTML = ''; });
  ['step-a','step-b'].forEach(id => document.getElementById(id).textContent = '');
  document.getElementById('diverge-badge').classList.add('hidden');
  document.getElementById('batch-tag').textContent = '';
  document.getElementById('status').textContent = 'Generating…';
  document.getElementById('btn-gen').style.display  = 'none';
  document.getElementById('btn-stop').style.display = '';

  const isCompare = document.getElementById('parallel-compare-mode').checked;
  const params = new URLSearchParams({ mode: isCompare ? 'compare' : 'parallel' });
  const pfx = getRole();
  let promptA, promptB;

  const ta = document.getElementById('temp-pa').value;
  const tb = document.getElementById('temp-pb').value;
  const ka = document.getElementById('topk-a').value;
  const kb = document.getElementById('topk-b').value;

  if (isCompare) {
    promptA = promptB = pfx + document.getElementById('pa').value;
    document.getElementById('title-a').textContent = `temp=${ta}  top_k=${ka}`;
    document.getElementById('title-b').textContent = `temp=${tb}  top_k=${kb}`;
  } else {
    promptA = pfx + document.getElementById('pa').value;
    promptB = pfx + document.getElementById('pb').value;
    document.getElementById('title-a').textContent = 'Stream A';
    document.getElementById('title-b').textContent = 'Stream B';
  }
  params.set('pa', promptA); params.set('pb', isCompare ? promptA : promptB);
  params.set('n', document.getElementById('n-p').value);
  params.set('temp_a', ta); params.set('temp_b', tb);
  params.set('top_k_a', ka); params.set('top_k_b', kb);
  params.set('seed', document.getElementById('seed-p').value);

  let diverged = false;
  dualEs = new EventSource('/stream?' + params.toString());

  dualEs.onmessage = function(e) {
    const d = JSON.parse(e.data);
    if (d.error) {
      document.getElementById('status').textContent = 'Error: ' + d.error;
      stopDual(); return;
    }
    if (d.mode_tag) {
      document.getElementById('batch-tag').textContent = d.mode_tag;
      dualIsServer = d.mode_tag.includes('llama-server');
      return;
    }
    if (d.done) {
      document.getElementById('status').textContent = 'Done.';
      stopDual();
      if (!dualIsServer) {
        document.getElementById('btn-to-step-a').style.display = '';
        document.getElementById('btn-to-step-b').style.display = '';
      }
      return;
    }
    // Use the known prompt strings (from input fields) instead of extracting
    // from detokenized text — tokenize/detokenize roundtrip may alter characters
    const userPromptA = promptA.slice(getRole().length);
    const userPromptB = promptB.slice(getRole().length);
    if (d.toks_a && d.ents_a) {
      renderHeatmap('output-a', d.toks_a, d.ents_a, userPromptA);
      renderHeatmap('output-b', d.toks_b, d.ents_b, userPromptB);
    } else {
      // Server mode: no individual tokens, extract from full detokenized text
      const idxA = d.a.indexOf(userPromptA);
      const idxB = d.b.indexOf(userPromptB);
      document.getElementById('output-a').textContent = idxA >= 0 ? d.a.slice(idxA) : d.a;
      document.getElementById('output-b').textContent = idxB >= 0 ? d.b.slice(idxB) : d.b;
    }
    document.getElementById('step-a').textContent = d.done_a ? 'EOS' : 'step ' + d.step;
    document.getElementById('step-b').textContent = d.done_b ? 'EOS' : 'step ' + d.step;
    if (d.total_tokens) updateTokenCounter(d.total_tokens);

    if (isCompare && !diverged) {
      const ga = d.toks_a ? d.toks_a.join('') : d.a.slice(promptA.length);
      const gb = d.toks_b ? d.toks_b.join('') : d.b.slice(promptB.length);
      if (ga !== gb && (ga || gb)) {
        diverged = true;
        const badge = document.getElementById('diverge-badge');
        badge.textContent = 'diverged @ step ' + d.step;
        badge.classList.remove('hidden');
      }
    }
  };
  dualEs.onerror = function() {
    document.getElementById('status').textContent = 'Stream ended.';
    stopDual();
  };
}

async function dualToStep(stream) {
  const temp = parseFloat(document.getElementById('step-temp').value) || 0.0;
  const top_k = parseInt(document.getElementById('step-topk').value) || 40;
  document.getElementById('status').textContent = 'Switching to step mode…';
  const r = await fetch('/dual/to-step', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({stream, temp, top_k})
  });
  const d = await r.json();
  if (d.error) {
    document.getElementById('status').textContent = 'Error: ' + d.error;
    return;
  }
  hideToStepBtns();
  setMode('step');
  document.getElementById('step-output').textContent = d.text;
  stepRoleLen = 0;
  stepPromptText = d.text;
  if (d.top) renderTop(d.top);
  if (d.total_tokens) updateTokenCounter(d.total_tokens);
  document.getElementById('status').textContent = 'Step mode (from stream ' + stream.toUpperCase() + ')';
  ['step-btn-next','step-btn-auto','step-btn-reset','step-btn-edit'].forEach(id => {
    document.getElementById(id).disabled = false;
  });
}
