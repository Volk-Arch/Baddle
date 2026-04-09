// ── Chat mode ────────────────────────────────────────────────────────────────

function chatAddMsg(role, content) {
  const container = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = 'chat-msg text-sm ' + (role === 'user' ? 'chat-user' : role === 'system' ? 'chat-system' : 'chat-assistant');
  div.style.whiteSpace = 'pre-wrap';
  div.style.wordBreak = 'break-word';
  div.textContent = content;
  if (role !== 'system') {
    const header = document.createElement('div');
    header.className = 'text-xs mb-1 flex justify-between items-center';
    const label = document.createElement('span');
    label.className = role === 'user' ? 'text-blue-400' : 'text-emerald-400';
    label.textContent = role;
    header.appendChild(label);
    if (role === 'assistant') {
      const btnWrap = document.createElement('span');
      btnWrap.style.cssText = 'display:flex;gap:6px;';
      const ctxBtn = document.createElement('button');
      ctxBtn.textContent = '→ ctx';
      ctxBtn.style.cssText = 'color:#b4b2ad;font-size:10px;cursor:pointer;';
      ctxBtn.onmouseover = function() { this.style.color = '#60a5fa'; };
      ctxBtn.onmouseout = function() { this.style.color = '#b4b2ad'; };
      ctxBtn.onclick = function() { chatContextAddFromChat(div.textContent.replace(/^assistant/, '').trim()); };
      btnWrap.appendChild(ctxBtn);
      const graphBtn = document.createElement('button');
      graphBtn.textContent = '→ graph';
      graphBtn.style.cssText = 'color:#b4b2ad;font-size:10px;cursor:pointer;';
      graphBtn.onmouseover = function() { this.style.color = '#10b981'; };
      graphBtn.onmouseout = function() { this.style.color = '#b4b2ad'; };
      graphBtn.onclick = function() { chatSendToGraph(div.textContent.replace(/^assistant/, '').trim()); };
      btnWrap.appendChild(graphBtn);
      header.appendChild(btnWrap);
    }
    div.prepend(header);
  }
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return div;
}

async function chatSend() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  autoGrow(input);

  chatAddMsg('user', text);
  const temp = parseFloat(document.getElementById('chat-temp').value) || 0.7;
  const top_k = parseInt(document.getElementById('chat-topk').value) || 40;
  let system = getRole();

  // Prepend context sidebar items if available
  const ctxText = chatContextGetText();
  if (ctxText) {
    system = system ? system + '\n\n' + ctxText : ctxText;
  }

  const sendBtn = document.getElementById('chat-btn-send');
  sendBtn.disabled = true;
  sendBtn.textContent = 'Thinking...';

  const pending = chatAddMsg('assistant', '…');
  try {
    const r = await fetch('/chat/send', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text, system, temp, top_k})
    });
    const d = await r.json();
    if (d.error) {
      pending.textContent = 'Error: ' + d.error;
    } else {
      // Rebuild msg div with response text, keep header/buttons
      const label = pending.querySelector('.text-xs');
      pending.textContent = d.text;
      if (label) pending.prepend(label);
    }
  } catch(e) {
    pending.textContent = 'Error: ' + e.message;
  } finally {
    sendBtn.disabled = false;
    sendBtn.textContent = 'Send';
    document.getElementById('chat-messages').scrollTop = document.getElementById('chat-messages').scrollHeight;
  }
}

async function chatReset() {
  await fetch('/chat/reset', {method: 'POST'});
  document.getElementById('chat-messages').innerHTML = '';
}

// ── Chat context sidebar ──────────────────────────────────────────────
let chatContextItems = []; // [{text, source, enabled}]

function chatContextRender() {
  const el = document.getElementById('chat-context-items');
  if (!chatContextItems.length) {
    el.innerHTML = '<span style="color:#b4b2ad;font-size:11px;">Add context: graph nodes, text, or chat responses</span>';
    return;
  }
  el.innerHTML = chatContextItems.map((item, i) => {
    const srcColor = item.source === 'graph' ? '#60a5fa' : item.source === 'chat' ? '#34d399' : '#a78bfa';
    const srcLabel = item.source === 'graph' ? 'graph' : item.source === 'chat' ? 'chat' : 'manual';
    const opacity = item.enabled ? '1' : '0.4';
    return '<div style="padding:3px 4px;border-bottom:1px solid #e0ddd8;opacity:' + opacity + ';display:flex;align-items:start;gap:4px;" data-ctx-idx="' + i + '">'
      + '<input type="checkbox" ' + (item.enabled ? 'checked' : '') + ' onchange="chatContextToggle(' + i + ')" style="margin-top:2px;flex-shrink:0">'
      + '<span style="color:' + srcColor + ';font-size:9px;text-transform:uppercase;flex-shrink:0;margin-top:1px;">' + srcLabel + '</span>'
      + '<span style="color:#37352f;flex:1;word-break:break-word;">' + item.text.replace(/</g, '&lt;').substring(0, 150)
      + (item.text.length > 150 ? '…' : '') + '</span>'
      + '<button onclick="chatContextRemove(' + i + ')" style="color:#b4b2ad;flex-shrink:0;font-size:10px;" class="hover:text-white">✕</button>'
      + '</div>';
  }).join('');
}

function chatContextAdd(text, source) {
  chatContextItems.push({ text: text.trim(), source: source || 'manual', enabled: true });
  chatContextRender();
}

function chatContextAddManual() {
  const input = document.getElementById('chat-context-input');
  const text = input.value.trim();
  if (!text) return;
  chatContextAdd(text, 'manual');
  input.value = '';
}

function chatContextRemove(i) {
  chatContextItems.splice(i, 1);
  chatContextRender();
}

function chatContextToggle(i) {
  chatContextItems[i].enabled = !chatContextItems[i].enabled;
  chatContextRender();
}

function chatContextClear() {
  chatContextItems = [];
  window._graphChatContext = null;
  window._graphChatIndices = null;
  window._graphChatStructure = null;
  chatContextRender();
}

function chatContextGetText() {
  const enabled = chatContextItems.filter(i => i.enabled);
  if (!enabled.length) return '';
  const useStructure = document.getElementById('chat-graph-structure') && document.getElementById('chat-graph-structure').checked;
  if (useStructure && window._graphChatStructure) {
    return _buildGraphStructureContext();
  }
  return 'Context:\n' + enabled.map((item, i) => (i + 1) + '. ' + item.text).join('\n');
}

// Add chat response to context sidebar
function chatContextAddFromChat(text) {
  chatContextAdd(text.substring(0, 500), 'chat');
}

async function chatSendToGraph(text) {
  if (!text) return;
  const r = await fetch('/graph/add', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ text: text.substring(0, 500), ...graphGetParams() })
  });
  const d = await r.json();
  if (d.error) { alert(d.error); return; }
  graphSaveUndo();
  graphUpdateView(d);
}

function chatToGraphFromInput() {
  const input = document.getElementById('chat-to-graph-input');
  const text = input.value.trim();
  if (!text) return;
  chatSendToGraph(text);
  input.value = '';
}

function chatClearGraphContext() {
  chatContextClear();
}

function _buildGraphStructureContext() {
  const s = window._graphChatStructure;
  const indices = window._graphChatIndices || [];
  const selectedSet = new Set(indices);
  if (!s) return '';

  const lines = ['=== Graph Context ==='];

  // Group selected nodes by cluster
  const clusterMap = new Map(); // clusterIdx → [nodeIdx]
  const nodeCluster = new Map(); // nodeIdx → clusterIdx
  (s.clusters || []).forEach((cl, ci) => {
    cl.forEach(ni => { nodeCluster.set(ni, ci); });
  });
  indices.forEach(ni => {
    const ci = nodeCluster.has(ni) ? nodeCluster.get(ni) : -1;
    if (!clusterMap.has(ci)) clusterMap.set(ci, []);
    clusterMap.get(ci).push(ni);
  });

  // Group by topic
  const topicGroups = new Map(); // topic → [clusterIdx]
  clusterMap.forEach((nodes, ci) => {
    const topic = nodes.length > 0 && nodes[0] < s.nodes.length ? s.nodes[nodes[0]].topic : '?';
    if (!topicGroups.has(topic)) topicGroups.set(topic, []);
    topicGroups.get(topic).push(ci);
  });

  topicGroups.forEach((clusterIdxList, topic) => {
    if (topic) lines.push('\nTopic: ' + topic);
    clusterIdxList.forEach(ci => {
      const nodes = clusterMap.get(ci);
      if (ci >= 0) lines.push('\nCluster ' + (ci + 1) + ':');
      nodes.forEach(ni => {
        const nodeData = ni < s.nodes.length ? s.nodes[ni] : null;
        const depth = nodeData ? (nodeData.depth || 0) : 0;
        const depthLabel = depth === -1 ? ' [root]' : depth > 0 ? ' [d' + depth + ']' : '';
        lines.push('  [' + ni + '] ' + (nodeData ? nodeData.text : '?') + depthLabel);
      });
      // Edges within this cluster between selected nodes
      const clEdges = (s.edges || []).filter(e =>
        selectedSet.has(e[0]) && selectedSet.has(e[1]) &&
        nodes.includes(e[0]) && nodes.includes(e[1])
      );
      if (clEdges.length > 0) {
        lines.push('  Edges: ' + clEdges.map(e => e[0] + '\u2194' + e[1] + ' (' + (e[2] || 0).toFixed(2) + ')').join(', '));
      }
    });
  });

  // Cross-cluster edges
  const crossEdges = (s.edges || []).filter(e => {
    if (!selectedSet.has(e[0]) || !selectedSet.has(e[1])) return false;
    return nodeCluster.get(e[0]) !== nodeCluster.get(e[1]);
  });
  if (crossEdges.length > 0) {
    lines.push('\nCross-cluster edges: ' + crossEdges.map(e => e[0] + '\u2194' + e[1] + ' (' + (e[2] || 0).toFixed(2) + ')').join(', '));
  }

  // Directed edges between selected
  const dirEdges = (s.directed || []).filter(e => selectedSet.has(e[0]) && selectedSet.has(e[1]));
  if (dirEdges.length > 0) {
    lines.push('\nDirected edges: ' + dirEdges.map(e => e[0] + '\u2192' + e[1]).join(', '));
  }

  lines.push('\n===');
  return lines.join('\n');
}
