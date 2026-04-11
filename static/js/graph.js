// ── Graph thinking ────────────────────────────────────────────────────────
const graphClusterColors = ['#10b981', '#3b82f6', '#8b5cf6', '#ef4444', '#06b6d4', '#ec4899', '#84cc16', '#f97316'];
let graphData = { nodes: [], edges: [], clusters: [] };
let graphSelectedNode = -1;
let graphLinkMode = false;  // toggle for manual linking
let graphManualCollapseMode = false;
let graphManualCollapseSet = new Set();
let graphFlowMode = false;  // directed flow layout
let graphZoom = 1;
let graphPanX = 0, graphPanY = 0;
let graphNodePositions = [];  // {x, y, vx, vy, fixed} per node
let graphDragIdx = -1;
let graphDragOffset = { x: 0, y: 0 };
let graphSimTimer = null;
let graphUndoStack = [];  // [{data, positions, collapsed}]
const GRAPH_MAX_UNDO = 20;
let graphCollapsedNodes = new Set();  // indices of nodes created by collapse
let graphHubNodes = new Set();       // indices of nodes used as elaborate source
let graphDirectedEdges = [];         // [[from, to], ...] — elaborate edges

let graphLastW = 0, graphLastH = 0;

function graphSaveUndo() {
  graphUndoStack.push({
    data: JSON.parse(JSON.stringify(graphData)),
    positions: JSON.parse(JSON.stringify(graphNodePositions)),
    collapsed: new Set(graphCollapsedNodes),
    hubs: new Set(graphHubNodes),
    directed: JSON.parse(JSON.stringify(graphDirectedEdges))
  });
  if (graphUndoStack.length > GRAPH_MAX_UNDO) graphUndoStack.shift();
  document.getElementById('graph-btn-undo').style.display = '';
}

async function graphUndo() {
  if (!graphUndoStack.length) return;
  const state = graphUndoStack.pop();
  graphNodePositions = state.positions;
  graphCollapsedNodes = state.collapsed || new Set();
  graphHubNodes = state.hubs || new Set();
  graphDirectedEdges = state.directed || [];
  graphSelectedNode = -1;
  // Sync backend state with full undo data
  const syncData = {
    nodes: state.data.nodes,
    edges: state.data.edges || [],
    clusters: state.data.clusters || [],
    manual_links: state.data.manual_links || [],
    manual_unlinks: state.data.manual_unlinks || [],
    directed_edges: state.directed || [],
    hub_nodes: [...(state.hubs || [])],
    ...graphGetParams()
  };
  const r = await fetch('/graph/sync', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(syncData)
  });
  const d = await r.json();
  if (d.error) {
    graphData = state.data;
  } else {
    graphData = d;
    if (d.hub_nodes) { graphHubNodes = new Set(); d.hub_nodes.forEach(i => graphHubNodes.add(i)); }
    if (d.directed_edges) graphDirectedEdges = d.directed_edges;
  }
  graphRenderSvg();
  graphUpdateThoughtsList();
  graphUpdateCollapseButtons();
  graphShowDetail(-1);
  if (!graphUndoStack.length) document.getElementById('graph-btn-undo').style.display = 'none';
}

function graphRecalcLayout() {
  graphNodePositions = [];  // force fresh positions
  graphDrawSvg();
}

// Update graph data + render without moving nodes (just redraw edges/clusters)
function graphUpdateView(d) {
  graphData = d;
  // Track hub nodes and traps from backend
  if (d.hub_nodes) d.hub_nodes.forEach(i => graphHubNodes.add(i));
  if (d.directed_edges) graphDirectedEdges = d.directed_edges;
  _graphTraps = d.traps || [];
  graphData._alpha_beta = d.alpha_beta || {};
  graphInitPositions();  // add positions for new nodes only
  graphRenderSvg();
  graphUpdateThoughtsList();
  graphUpdateCollapseButtons();
  document.getElementById('graph-actions-bar').style.display = graphData.nodes.length ? '' : 'none';
  // Check for weak hypotheses (confidence < 0.4) — suggest verification
  const weakNodes = (d.nodes || []).filter(n => n.depth >= 0 && n.confidence < 0.4);
  if (weakNodes.length > 0) {
    const names = weakNodes.map(n => '#' + n.id + ' "' + n.text.slice(0, 30) + '..."').join(', ');
    console.log('[smartdc trigger] Weak nodes detected:', names);
    // Show subtle notification
    const bar = document.getElementById('graph-actions-bar');
    let hint = document.getElementById('graph-weak-hint');
    if (!hint) {
      hint = document.createElement('div');
      hint.id = 'graph-weak-hint';
      hint.style.cssText = 'color:#facc15;font-size:11px;padding:4px 8px;cursor:pointer;';
      hint.onclick = function() {
        // Select first weak node
        const idx = weakNodes[0].id;
        graphSelectedNode = idx;
        graphRenderSvg();
        graphShowDetail(idx);
        this.style.display = 'none';
      };
      bar.appendChild(hint);
    }
    hint.textContent = '\u26A0 ' + weakNodes.length + ' weak hypothesis' + (weakNodes.length > 1 ? 'es' : '') + ' \u2014 click to verify';
    hint.style.display = '';
  } else {
    const hint = document.getElementById('graph-weak-hint');
    if (hint) hint.style.display = 'none';
  }
}

function graphGetDimensions() {
  const svg = document.getElementById('graph-svg');
  return { W: svg.clientWidth || 500, H: parseInt(svg.getAttribute('height')) || 520 };
}

function graphInitPositions() {
  const { W, H } = graphGetDimensions();
  const n = graphData.nodes.length;
  const old = graphNodePositions;
  const positions = [];

  for (let i = 0; i < n; i++) {
    if (i < old.length && old[i]) {
      positions.push({ x: old[i].x, y: old[i].y, vx: 0, vy: 0, fixed: old[i].fixed });
    } else {
      let nx, ny;
      if (graphFlowMode) {
        const d = (graphData.nodes[i] && graphData.nodes[i].depth) || 0;
        nx = _flowColX(d);
        ny = H / 2 + (Math.random() - 0.5) * H * 0.5;
      } else {
        // Check if this node has a parent (directed edge)
        let parentIdx = -1;
        graphDirectedEdges.forEach(([from, to]) => { if (to === i) parentIdx = from; });
        if (parentIdx >= 0 && parentIdx < positions.length) {
          const px = positions[parentIdx].x, py = positions[parentIdx].y;
          const dx = px - W/2, dy = py - H/2;
          const dist = Math.sqrt(dx*dx + dy*dy) || 1;
          const spread = 50 + Math.random() * 30;
          nx = px + (dx/dist) * spread + (Math.random()-0.5) * 30;
          ny = py + (dy/dist) * spread + (Math.random()-0.5) * 30;
        } else {
          const angle = (2 * Math.PI * i / Math.max(n, 1)) - Math.PI / 2;
          const R = Math.min(W, H) * 0.35;
          nx = W/2 + R * Math.cos(angle) + (Math.random()-0.5) * 20;
          ny = H/2 + R * Math.sin(angle) + (Math.random()-0.5) * 20;
        }
      }
      positions.push({ x: nx, y: ny, vx: 0, vy: 0, fixed: false });
    }
  }
  graphNodePositions = positions.slice(0, n);
  graphLastW = W; graphLastH = H;
}

// Flow layout constants
const FLOW_COL_WIDTH = 280;
const FLOW_COL_START = 120;
function _flowColX(depth) {
  if (depth === -1) return 40;  // topic root far left
  return FLOW_COL_START + depth * FLOW_COL_WIDTH;
}

// ── Flow layout: deterministic column placement ──
function graphRunFlowLayout() {
  const { W, H } = graphGetDimensions();
  const nodes = graphNodePositions;
  const n = nodes.length;
  if (n === 0) return;
  const pad = 25;

  // 1. Set X to column, keep existing Y for fixed nodes
  for (let i = 0; i < n; i++) {
    if (!nodes[i].fixed) {
      const nodeDepth = (graphData.nodes[i] && graphData.nodes[i].depth) || 0;
      nodes[i].x = _flowColX(nodeDepth);
    }
  }

  // 2. Build parent map from directed edges
  const parentOf = new Array(n).fill(-1);
  graphDirectedEdges.forEach(([from, to]) => { if (to < n) parentOf[to] = from; });

  // 3. Group by depth column
  const byDepth = {};
  for (let i = 0; i < n; i++) {
    const d = (graphData.nodes[i] && graphData.nodes[i].depth) || 0;
    if (!byDepth[d]) byDepth[d] = [];
    byDepth[d].push(i);
  }

  // 4. Place nodes in each column — children near their parent's Y
  Object.keys(byDepth).sort((a,b) => a - b).forEach(dStr => {
    const col = byDepth[dStr];
    // Sort by parent Y so children cluster near parent
    col.sort((a, b) => {
      const pa = parentOf[a] >= 0 && nodes[parentOf[a]] ? nodes[parentOf[a]].y : H/2;
      const pb = parentOf[b] >= 0 && nodes[parentOf[b]] ? nodes[parentOf[b]].y : H/2;
      return pa - pb;
    });
    const spacing = Math.max(75, (H - 2*pad) / (col.length + 1));
    const startY = pad + (H - 2*pad - spacing * (col.length - 1)) / 2;
    col.forEach((idx, j) => {
      if (!nodes[idx].fixed) {
        const parentY = parentOf[idx] >= 0 && nodes[parentOf[idx]] ? nodes[parentOf[idx]].y : null;
        // Blend between even distribution and parent proximity
        const evenY = startY + j * spacing;
        nodes[idx].y = parentY !== null ? parentY * 0.4 + evenY * 0.6 : evenY;
      }
    });
  });

  // 5. Vertical collision resolution within same column
  const minGap = 85;
  for (let pass = 0; pass < 5; pass++) {
    Object.values(byDepth).forEach(col => {
      col.sort((a, b) => nodes[a].y - nodes[b].y);
      for (let j = 1; j < col.length; j++) {
        const prev = col[j-1], curr = col[j];
        const gap = nodes[curr].y - nodes[prev].y;
        if (gap < minGap) {
          const push = (minGap - gap) / 2;
          if (!nodes[prev].fixed) nodes[prev].y -= push;
          if (!nodes[curr].fixed) nodes[curr].y += push;
        }
      }
    });
  }

  // 6. Clamp to bounds
  for (let i = 0; i < n; i++) {
    nodes[i].y = Math.max(pad, Math.min(H - pad, nodes[i].y));
  }
}

// ── Free layout: fan/radial placement ──
function graphRunFreeLayout(iterations) {
  const { W, H } = graphGetDimensions();
  const nodes = graphNodePositions;
  const n = nodes.length;
  if (n === 0) return;
  const cx = W / 2, cy = H / 2;
  const pad = 40;

  // Build clusters and unclustered list
  const nodeCluster = new Array(n).fill(-1);
  const clusters = graphData.clusters || [];
  clusters.forEach((cl, ci) => cl.forEach(idx => { if (idx < n) nodeCluster[idx] = ci; }));
  const unclustered = [];
  for (let i = 0; i < n; i++) if (nodeCluster[i] < 0) unclustered.push(i);

  // Each cluster + unclustered group gets a sector
  const groups = clusters.map(cl => [...cl]);
  if (unclustered.length) groups.push(unclustered);
  const totalGroups = groups.length;
  if (totalGroups === 0) return;

  const maxR = Math.min(W/2 - pad, H/2 - pad);

  groups.forEach((group, gi) => {
    // Sector angle range for this group
    const sectorStart = (2 * Math.PI * gi / totalGroups) - Math.PI / 2;
    const sectorEnd = (2 * Math.PI * (gi + 1) / totalGroups) - Math.PI / 2;
    const sectorMid = (sectorStart + sectorEnd) / 2;
    const sectorSpan = sectorEnd - sectorStart;

    if (group.length === 1) {
      const idx = group[0];
      if (!nodes[idx].fixed) {
        const r = maxR * 0.6;
        nodes[idx].x = cx + r * Math.cos(sectorMid);
        nodes[idx].y = cy + r * Math.sin(sectorMid);
      }
    } else {
      // Spread nodes within sector at varying radii
      group.forEach((idx, j) => {
        if (nodes[idx].fixed) return;
        const t = (j + 0.5) / group.length;  // 0..1 within group
        const angle = sectorStart + sectorSpan * 0.15 + t * sectorSpan * 0.7;
        // Alternate between inner and outer radius to avoid overlap
        const ringOffset = (j % 2 === 0) ? 0.5 : 0.8;
        const r = maxR * (0.3 + ringOffset * 0.5);
        nodes[idx].x = cx + r * Math.cos(angle);
        nodes[idx].y = cy + r * Math.sin(angle);
      });
    }
  });

  // Clamp to bounds
  for (let i = 0; i < n; i++) {
    nodes[i].x = Math.max(pad, Math.min(W-pad, nodes[i].x));
    nodes[i].y = Math.max(pad, Math.min(H-pad, nodes[i].y));
  }
}

function graphConvexHull(points) {
  // Graham scan for convex hull
  if (points.length < 3) return points;
  points = points.slice().sort((a, b) => a.x - b.x || a.y - b.y);
  const cross = (O, A, B) => (A.x - O.x) * (B.y - O.y) - (A.y - O.y) * (B.x - O.x);
  const lower = [];
  for (const p of points) { while (lower.length >= 2 && cross(lower[lower.length-2], lower[lower.length-1], p) <= 0) lower.pop(); lower.push(p); }
  const upper = [];
  for (const p of points.reverse()) { while (upper.length >= 2 && cross(upper[upper.length-2], upper[upper.length-1], p) <= 0) upper.pop(); upper.push(p); }
  return lower.slice(0, -1).concat(upper.slice(0, -1));
}

function graphExpandHull(hull, pad) {
  // Expand hull outward by pad pixels
  const cx = hull.reduce((s, p) => s + p.x, 0) / hull.length;
  const cy = hull.reduce((s, p) => s + p.y, 0) / hull.length;
  return hull.map(p => {
    const dx = p.x - cx, dy = p.y - cy;
    const dist = Math.sqrt(dx*dx + dy*dy) || 1;
    return { x: p.x + (dx/dist) * pad, y: p.y + (dy/dist) * pad };
  });
}

function graphApplyViewBox() {
  const svg = document.getElementById('graph-svg');
  const W = svg.clientWidth || 500;
  const H = parseInt(svg.getAttribute('height')) || 520;
  const vw = W / graphZoom, vh = H / graphZoom;
  svg.setAttribute('viewBox', `${graphPanX} ${graphPanY} ${vw} ${vh}`);
}

function graphRenderSvg() {
  const svg = document.getElementById('graph-svg');
  svg.innerHTML = '';
  graphApplyViewBox();
  const nodes = graphData.nodes;
  const positions = graphNodePositions;
  if (!nodes.length || !positions.length) return;

  const nodeCluster = new Array(nodes.length).fill(-1);
  (graphData.clusters || []).forEach((cl, ci) => cl.forEach(idx => { nodeCluster[idx] = ci; }));
  const clusterColors = graphClusterColors;
  const depths = nodes.map(n => n.depth || 0);
  const maxDepth = Math.max(0, ...depths);

  // Draw cluster hulls
  (graphData.clusters || []).forEach((cl, ci) => {
    const pts = cl.map(idx => positions[idx]).filter(Boolean);
    if (pts.length < 2) return;
    const color = clusterColors[ci % clusterColors.length];
    if (pts.length === 2) {
      // Just a line between two points
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', pts[0].x); line.setAttribute('y1', pts[0].y);
      line.setAttribute('x2', pts[1].x); line.setAttribute('y2', pts[1].y);
      line.setAttribute('stroke', color); line.setAttribute('stroke-width', 20);
      line.setAttribute('stroke-opacity', 0.08); line.setAttribute('stroke-linecap', 'round');
      svg.appendChild(line);
    } else {
      const hull = graphExpandHull(graphConvexHull(pts), 25);
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', 'M' + hull.map(p => p.x+','+p.y).join('L') + 'Z');
      path.setAttribute('fill', color); path.setAttribute('fill-opacity', '0.07');
      path.setAttribute('stroke', color); path.setAttribute('stroke-opacity', '0.15');
      path.setAttribute('stroke-width', '1');
      path.style.pointerEvents = 'none';
      svg.appendChild(path);
    }
  });

  // Arrowhead marker for directed edges
  const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
  const marker = document.createElementNS('http://www.w3.org/2000/svg', 'marker');
  marker.setAttribute('id', 'arrowhead'); marker.setAttribute('markerWidth', '8');
  marker.setAttribute('markerHeight', '6'); marker.setAttribute('refX', '8');
  marker.setAttribute('refY', '3'); marker.setAttribute('orient', 'auto');
  const arrow = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
  arrow.setAttribute('points', '0 0, 8 3, 0 6'); arrow.setAttribute('fill', '#c4b5fd');
  marker.appendChild(arrow); defs.appendChild(marker); svg.appendChild(defs);

  // Build directed edge lookup: "from,to" → true
  const directedSet = new Set();
  graphDirectedEdges.forEach(([a, b]) => directedSet.add(a + ',' + b));

  // Walk path edge set for highlighting
  const walkEdgeSet = new Set();
  if (_graphWalkPath && _graphWalkPath.length > 1) {
    for (let wi = 0; wi < _graphWalkPath.length - 1; wi++) {
      walkEdgeSet.add(_graphWalkPath[wi] + ',' + _graphWalkPath[wi+1]);
      walkEdgeSet.add(_graphWalkPath[wi+1] + ',' + _graphWalkPath[wi]);
    }
  }

  // Draw edges — color by similarity strength
  graphData.edges.forEach(e => {
    const a = positions[e.from], b = positions[e.to];
    if (!a || !b) return;
    // Check if this edge is directed (elaborate)
    const isDirected = directedSet.has(e.from + ',' + e.to) || directedSet.has(e.to + ',' + e.from);
    const dirFrom = directedSet.has(e.from + ',' + e.to) ? e.from : (directedSet.has(e.to + ',' + e.from) ? e.to : -1);

    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    // For directed edges, shorten line to not overlap node
    let x1 = a.x, y1 = a.y, x2 = b.x, y2 = b.y;
    if (isDirected) {
      const target = dirFrom === e.from ? b : a;
      const source = dirFrom === e.from ? a : b;
      const dx = target.x - source.x, dy = target.y - source.y;
      const dist = Math.sqrt(dx*dx + dy*dy) || 1;
      x1 = source.x; y1 = source.y;
      x2 = target.x - (dx/dist) * 12; y2 = target.y - (dy/dist) * 12;
    }
    line.setAttribute('x1', x1); line.setAttribute('y1', y1);
    line.setAttribute('x2', x2); line.setAttribute('y2', y2);

    if (e.relation === 'supports') {
      line.setAttribute('stroke', '#10b981');
      line.setAttribute('stroke-width', 2);
      line.setAttribute('stroke-opacity', 0.7);
      line.setAttribute('marker-end', 'url(#arrowhead)');
    } else if (e.relation === 'contradicts') {
      line.setAttribute('stroke', '#ef4444');
      line.setAttribute('stroke-width', 2);
      line.setAttribute('stroke-opacity', 0.7);
      line.setAttribute('stroke-dasharray', '6,3');
      line.setAttribute('marker-end', 'url(#arrowhead)');
    } else if (isDirected) {
      line.setAttribute('stroke', '#c4b5fd');
      line.setAttribute('stroke-width', 2);
      line.setAttribute('stroke-opacity', 0.7);
      line.setAttribute('marker-end', 'url(#arrowhead)');
    } else if (e.manual) {
      line.setAttribute('stroke', '#a78bfa');
      line.setAttribute('stroke-width', 2);
      line.setAttribute('stroke-opacity', 0.8);
      line.setAttribute('stroke-dasharray', '6,3');
    } else {
      const tp = Math.max(0, Math.min(1, e.tp || e.weight || 0));
      line.setAttribute('stroke', '#94a3b8');
      line.setAttribute('stroke-width', 1 + tp * 5);
      line.setAttribute('stroke-opacity', 0.3 + tp * 0.6);
    }
    // Walk path override: bright green
    const isWalkEdge = walkEdgeSet.has(e.from + ',' + e.to);
    if (isWalkEdge) {
      line.setAttribute('stroke', '#10b981');
      line.setAttribute('stroke-width', 4);
      line.setAttribute('stroke-opacity', 0.9);
    }
    // Edge title for native browser tooltip on hover
    const tpVal = (e.tp || 0).toFixed(2);
    const tpRev = (e.tp_rev || 0).toFixed(2);
    const wVal = (e.weight || 0).toFixed(2);
    const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
    const rel = e.relation || 'similarity';
    title.textContent = '#' + e.from + '\u2192#' + e.to + ': P=' + tpVal + '  |  #' + e.to + '\u2192#' + e.from + ': P=' + tpRev + '  |  sim=' + wVal + '  |  ' + rel;
    line.appendChild(title);
    line.style.pointerEvents = 'stroke';
    svg.appendChild(line);
  });

  // Draw directed edges that are NOT covered by similarity edges
  const edgeSet = new Set();
  graphData.edges.forEach(e => { edgeSet.add(e.from+','+e.to); edgeSet.add(e.to+','+e.from); });
  graphDirectedEdges.forEach(([from, to]) => {
    if (edgeSet.has(from+','+to)) return;  // already drawn above
    const a = positions[from], b = positions[to];
    if (!a || !b) return;
    const dx = b.x - a.x, dy = b.y - a.y;
    const dist = Math.sqrt(dx*dx + dy*dy) || 1;
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', a.x); line.setAttribute('y1', a.y);
    line.setAttribute('x2', b.x - (dx/dist) * 12); line.setAttribute('y2', b.y - (dy/dist) * 12);
    line.setAttribute('stroke', '#fbbf24');
    line.setAttribute('stroke-width', 1.5);
    line.setAttribute('stroke-opacity', 0.5);
    line.setAttribute('stroke-dasharray', '4,4');
    line.setAttribute('marker-end', 'url(#arrowhead)');
    svg.appendChild(line);
  });

  // In flow mode, detect dead-end nodes (no outgoing directed edges)
  const hasChildren = new Set();
  if (graphFlowMode) {
    graphDirectedEdges.forEach(([from, to]) => hasChildren.add(from));
  }

  // Draw nodes
  positions.forEach((p, i) => {
    if (!p) return;
    const ci = nodeCluster[i];
    const color = ci >= 0 ? clusterColors[ci % clusterColors.length] : '#64748b';
    const isSelected = i === graphSelectedNode;
    const isCollapsed = graphCollapsedNodes.has(i);
    const isHub = graphHubNodes.has(i);
    const nodeDepth = (graphData.nodes[i] && graphData.nodes[i].depth);
    const isTopicRoot = nodeDepth === -1;
    const isDeadEnd = graphFlowMode && !hasChildren.has(i) && nodeDepth > 0;
    const isCollapseSelected = graphManualCollapseMode && graphManualCollapseSet.has(i);

    // Hub halo (drawn behind the node)
    if (isHub && !isCollapsed) {
      const halo = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      halo.setAttribute('cx', p.x); halo.setAttribute('cy', p.y);
      halo.setAttribute('r', 18);
      halo.setAttribute('fill', 'none');
      halo.setAttribute('stroke', '#c4b5fd');
      halo.setAttribute('stroke-width', 2);
      halo.setAttribute('stroke-opacity', 0.4);
      halo.setAttribute('stroke-dasharray', '4,3');
      halo.style.pointerEvents = 'none';
      svg.appendChild(halo);
    }

    // Trap indicator (red dashed ring)
    const isTrap = _graphTraps && _graphTraps.includes(i);
    if (isTrap && !isCollapsed && !isTopicRoot) {
      const trap = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      trap.setAttribute('cx', p.x); trap.setAttribute('cy', p.y);
      trap.setAttribute('r', 16);
      trap.setAttribute('fill', 'none');
      trap.setAttribute('stroke', '#ef4444');
      trap.setAttribute('stroke-width', 2);
      trap.setAttribute('stroke-opacity', 0.7);
      trap.setAttribute('stroke-dasharray', '3,3');
      trap.style.pointerEvents = 'none';
      svg.appendChild(trap);
    }

    // Walk path highlight
    const isOnWalkPath = _graphWalkPath && _graphWalkPath.includes(i);

    // Confidence border (grey=low → blue=medium → green=high)
    const nodeConf = (graphData.nodes[i] && graphData.nodes[i].confidence != null) ? graphData.nodes[i].confidence : 0.5;
    let confColor;
    if (isCollapseSelected) {
      confColor = '#f43f5e';
    } else if (isTopicRoot) {
      confColor = '#e2e8f0';
    } else if (nodeConf < 0.33) {
      // Low: grey → blue
      const t = nodeConf / 0.33;
      confColor = `rgb(${Math.round(148 - t * 88)},${Math.round(163 - t * 33)},${Math.round(184 + t * 71)})`;
    } else if (nodeConf < 0.66) {
      // Medium: blue → teal
      const t = (nodeConf - 0.33) / 0.33;
      confColor = `rgb(${Math.round(60 - t * 44)},${Math.round(130 + t * 53)},${Math.round(255 - t * 124)})`;
    } else {
      // High: teal → green
      const t = (nodeConf - 0.66) / 0.34;
      confColor = `rgb(${Math.round(16 + t * 0)},${Math.round(183 + t * 2)},${Math.round(131 - t * 2)})`;
    }

    if (isTopicRoot) {
      // Topic root: diamond shape
      const size = isSelected ? 18 : 16;
      const diamond = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
      diamond.setAttribute('points',
        `${p.x},${p.y-size} ${p.x+size},${p.y} ${p.x},${p.y+size} ${p.x-size},${p.y}`);
      diamond.setAttribute('fill', '#f59e0b');
      diamond.setAttribute('stroke', isSelected ? '#facc15' : '#fbbf24');
      diamond.setAttribute('stroke-width', isSelected ? 3 : 2);
      diamond.style.cursor = 'pointer';
      diamond.dataset.nodeIdx = i;
      svg.appendChild(diamond);
    } else if (isCollapsed) {
      // Collapsed nodes: rounded rectangle (diamond-like)
      const size = isSelected ? 16 : 13;
      const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      rect.setAttribute('x', p.x - size); rect.setAttribute('y', p.y - size);
      rect.setAttribute('width', size * 2); rect.setAttribute('height', size * 2);
      rect.setAttribute('rx', 4); rect.setAttribute('ry', 4);
      rect.setAttribute('fill', color);
      rect.setAttribute('stroke', isSelected ? '#facc15' : confColor);
      rect.setAttribute('stroke-width', isSelected ? 3 : (isCollapseSelected ? 3 : 1.5));
      rect.style.cursor = 'pointer';
      if (isDeadEnd) rect.setAttribute('opacity', 0.45);
      rect.dataset.nodeIdx = i;
      svg.appendChild(rect);
    } else {
      // Normal nodes: circle (with type-specific styling)
      const nodeType = (graphData.nodes[i] && graphData.nodes[i].type) || 'thought';
      const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      circle.setAttribute('cx', p.x); circle.setAttribute('cy', p.y);
      circle.setAttribute('r', isSelected ? 14 : (ci >= 0 ? 11 : 8));
      circle.setAttribute('fill', color);
      let strokeColor = isSelected ? '#facc15' : (isOnWalkPath ? '#10b981' : confColor);
      let strokeWidth = isSelected ? 3 : (isOnWalkPath ? 3 : (isCollapseSelected ? 3 : 1.5));
      // Type-specific visuals
      if (nodeType === 'hypothesis') {
        circle.setAttribute('stroke-dasharray', '4,2');
        if (!isSelected) strokeColor = '#a78bfa'; // purple dashed for hypothesis
      } else if (nodeType === 'evidence') {
        if (!isSelected) strokeColor = '#06b6d4'; // cyan for evidence
        strokeWidth = isSelected ? 3 : 2.5;
      } else if (nodeType === 'fact') {
        if (!isSelected) strokeColor = '#10b981'; // green solid for fact
        strokeWidth = isSelected ? 3 : 2.5;
      } else if (nodeType === 'question') {
        circle.setAttribute('stroke-dasharray', '6,3');
        if (!isSelected) strokeColor = '#f59e0b'; // amber dashed for question
      } else if (nodeType === 'goal') {
        if (!isSelected) strokeColor = '#f43f5e'; // rose for goal (point B)
        strokeWidth = isSelected ? 3 : 3;
        circle.setAttribute('stroke-dasharray', '8,2');
      } else if (nodeType === 'action') {
        if (!isSelected) strokeColor = '#8b5cf6'; // violet for action
        strokeWidth = isSelected ? 3 : 2.5;
      }
      circle.setAttribute('stroke', strokeColor);
      circle.setAttribute('stroke-width', strokeWidth);
      circle.style.cursor = 'pointer';
      if (isDeadEnd) circle.setAttribute('opacity', 0.45);
      circle.dataset.nodeIdx = i;
      svg.appendChild(circle);
    }

    // Label
    const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    const fontSize = (isCollapsed || isTopicRoot) ? 12 : 11;
    label.setAttribute('fill', isTopicRoot ? '#fbbf24' : '#94a3b8');
    label.setAttribute('font-size', fontSize);
    if (isCollapsed || isTopicRoot) label.setAttribute('font-weight', 'bold');
    label.style.pointerEvents = 'none';
    if (isDeadEnd) label.setAttribute('opacity', 0.45);

    if (graphFlowMode) {
      // Flow mode: text to the right of node, up to 2 lines
      const nodeR = isCollapsed ? 16 : (isSelected ? 14 : 11);
      const lx = p.x + nodeR + 6;
      label.setAttribute('text-anchor', 'start');
      const maxChars = 35;
      const text = nodes[i].text;
      if (text.length <= maxChars) {
        label.setAttribute('x', lx);
        label.setAttribute('y', p.y + 4);
        label.textContent = text;
      } else {
        // Word-wrap into 2 lines
        const words = text.split(/\s+/);
        let line1 = '', line2 = '';
        for (const w of words) {
          if (!line1 || (line1 + ' ' + w).length <= maxChars) {
            line1 = line1 ? line1 + ' ' + w : w;
          } else {
            line2 = line2 ? line2 + ' ' + w : w;
          }
        }
        if (line2.length > maxChars) line2 = line2.slice(0, maxChars - 3) + '...';
        const t1 = document.createElementNS('http://www.w3.org/2000/svg', 'tspan');
        t1.setAttribute('x', lx); t1.setAttribute('dy', '0');
        t1.textContent = line1;
        const t2 = document.createElementNS('http://www.w3.org/2000/svg', 'tspan');
        t2.setAttribute('x', lx); t2.setAttribute('dy', String(fontSize + 2));
        t2.textContent = line2;
        label.appendChild(t1); label.appendChild(t2);
        label.setAttribute('y', p.y - 2);
      }
    } else {
      // Free mode: text above node, single line
      label.setAttribute('x', p.x);
      label.setAttribute('y', p.y - (isCollapsed ? 20 : 16));
      label.setAttribute('text-anchor', 'middle');
      const maxLen = 25;
      const short = nodes[i].text.length > maxLen ? nodes[i].text.slice(0, maxLen - 1) + '...' : nodes[i].text;
      label.textContent = short;
    }
    svg.appendChild(label);
  });

  // (Topic diamonds are now real nodes — no pseudo-rendering needed)

  // Tooltip element (reused)
  let tooltip = document.getElementById('graph-tooltip');
  if (!tooltip) {
    tooltip = document.createElement('div');
    tooltip.id = 'graph-tooltip';
    tooltip.style.cssText = 'position:absolute;background:#f7f6f3;border:1px solid #e0ddd8;color:#37352f;padding:6px 10px;border-radius:2px;font-size:12px;max-width:300px;pointer-events:none;display:none;z-index:50;white-space:pre-wrap;word-wrap:break-word;box-shadow:0 2px 8px rgba(0,0,0,0.12);';
    document.getElementById('graph-svg-wrap').appendChild(tooltip);
  }
}

function graphDrawSvg(gentle) {
  graphInitPositions();
  if (graphFlowMode) {
    graphRunFlowLayout();
    graphRenderSvg();
    if (graphSimTimer) cancelAnimationFrame(graphSimTimer);
    graphSimTimer = null;
    return;
  }
  // Free mode: deterministic radial layout
  graphRunFreeLayout();
  graphRenderSvg();
  if (graphSimTimer) cancelAnimationFrame(graphSimTimer);
  graphSimTimer = null;
}

// ── Drag, Tooltip, Context Menu, Keyboard ─────────────────────────────────
(function initGraphInteractions() {
  const svg = document.getElementById('graph-svg');
  if (!svg) return;
  let dragMoved = false;
  let graphPanning = false;
  let graphPanStart = { mx: 0, my: 0, px: 0, py: 0 };

  function getNodeTarget(e) {
    if (!e.target.closest) return null;
    return e.target.closest('[data-node-idx]');
  }

  function getSvgPoint(e) {
    const rect = svg.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const clientY = e.touches ? e.touches[0].clientY : e.clientY;
    // Convert screen coords to SVG coords accounting for viewBox zoom/pan
    const px = clientX - rect.left;
    const py = clientY - rect.top;
    const W = svg.clientWidth || 500;
    const H = parseInt(svg.getAttribute('height')) || 520;
    return { x: graphPanX + px / graphZoom, y: graphPanY + py / graphZoom };
  }

  function onDown(e) {
    const target = getNodeTarget(e);
    if (!target) {
      // Background drag → pan
      if (e.target === svg || e.target.closest('svg') === svg) {
        e.preventDefault();
        graphPanning = true;
        const clientX = e.touches ? e.touches[0].clientX : e.clientX;
        const clientY = e.touches ? e.touches[0].clientY : e.clientY;
        graphPanStart = { mx: clientX, my: clientY, px: graphPanX, py: graphPanY };
      }
      return;
    }
    const idx = parseInt(target.dataset.nodeIdx);
    if (isNaN(idx) || idx < 0 || idx >= graphNodePositions.length) return;
    e.preventDefault();
    graphDragIdx = idx;
    dragMoved = false;
    const pt = getSvgPoint(e);
    graphDragOffset.x = graphNodePositions[idx].x - pt.x;
    graphDragOffset.y = graphNodePositions[idx].y - pt.y;
    graphNodePositions[idx].fixed = true;
  }

  function onMove(e) {
    if (graphPanning) {
      e.preventDefault();
      const clientX = e.touches ? e.touches[0].clientX : e.clientX;
      const clientY = e.touches ? e.touches[0].clientY : e.clientY;
      graphPanX = graphPanStart.px - (clientX - graphPanStart.mx) / graphZoom;
      graphPanY = graphPanStart.py - (clientY - graphPanStart.my) / graphZoom;
      graphApplyViewBox();
      return;
    }
    if (graphDragIdx < 0) return;
    e.preventDefault();
    dragMoved = true;
    const pt = getSvgPoint(e);
    const node = graphNodePositions[graphDragIdx];
    node.x = pt.x + graphDragOffset.x;
    node.y = pt.y + graphDragOffset.y;
    graphRenderSvg();
  }

  function onUp(e) {
    if (graphPanning) { graphPanning = false; return; }
    if (graphDragIdx < 0) return;
    const idx = graphDragIdx;
    graphDragIdx = -1;
    graphNodePositions[idx].fixed = false;
    if (!dragMoved) {
      graphNodeClick(idx);
    }
  }

  svg.addEventListener('mousedown', onDown);
  svg.addEventListener('touchstart', onDown, { passive: false });
  window.addEventListener('mousemove', onMove);
  window.addEventListener('touchmove', onMove, { passive: false });
  window.addEventListener('mouseup', onUp);
  window.addEventListener('touchend', onUp);

  // ── Zoom on scroll wheel ──
  svg.addEventListener('wheel', function(e) {
    e.preventDefault();
    const rect = svg.getBoundingClientRect();
    const W = svg.clientWidth || 500;
    const H = parseInt(svg.getAttribute('height')) || 520;
    // Mouse position in SVG coords before zoom
    const mx = graphPanX + (e.clientX - rect.left) / graphZoom;
    const my = graphPanY + (e.clientY - rect.top) / graphZoom;
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    const newZoom = Math.max(0.2, Math.min(10, graphZoom * factor));
    // Adjust pan so point under cursor stays fixed
    graphPanX = mx - (e.clientX - rect.left) / newZoom;
    graphPanY = my - (e.clientY - rect.top) / newZoom;
    graphZoom = newZoom;
    graphApplyViewBox();
  }, { passive: false });

  // ── Tooltip on hover ──
  svg.addEventListener('mousemove', function(e) {
    const target = getNodeTarget(e);
    const tooltip = document.getElementById('graph-tooltip');
    if (!tooltip) return;
    if (target && graphDragIdx < 0 && !graphPanning) {
      const idx = parseInt(target.dataset.nodeIdx);
      if (idx >= 0 && idx < graphData.nodes.length) {
        const node = graphData.nodes[idx];
        const entData = (node && node.entropy) || {};
        const avg = (entData.avg || 0).toFixed(2);
        const unc = ((entData.unc || 0) * 100).toFixed(0);
        tooltip.textContent = node.text + (entData.avg ? '\n[ent: ' + avg + '  unc: ' + unc + '%]' : '');
        const rect = svg.getBoundingClientRect();
        tooltip.style.left = (e.clientX - rect.left + 15) + 'px';
        tooltip.style.top = (e.clientY - rect.top - 10) + 'px';
        tooltip.style.display = '';
        return;
      }
    }
    tooltip.style.display = 'none';
  });
  svg.addEventListener('mouseleave', function() {
    const tooltip = document.getElementById('graph-tooltip');
    if (tooltip) tooltip.style.display = 'none';
  });

  // ── Context menu ──
  svg.addEventListener('contextmenu', function(e) {
    const target = getNodeTarget(e);
    if (!target) return;
    e.preventDefault();
    const idx = parseInt(target.dataset.nodeIdx);
    if (isNaN(idx) || idx < 0 || idx >= graphData.nodes.length) return;
    graphSelectedNode = idx;
    graphRenderSvg();
    graphShowDetail(idx);
    graphShowContextMenu(e, idx);
  });
})();

// ── Context Menu ──
function graphShowContextMenu(e, idx) {
  let menu = document.getElementById('graph-context-menu');
  if (!menu) {
    menu = document.createElement('div');
    menu.id = 'graph-context-menu';
    menu.style.cssText = 'position:fixed;background:#f7f6f3;border:1px solid #e0ddd8;border-radius:2px;padding:4px 0;z-index:100;min-width:140px;box-shadow:0 2px 8px rgba(0,0,0,0.12);';
    document.body.appendChild(menu);
  }
  const node = graphData.nodes[idx] || {};
  const nodeType = node.type || 'thought';
  const items = [
    { label: '\uD83D\uDCA1 Brainstorm', action: 'graphBrainstorm(' + idx + ')' },
    { label: '\u261E Expand', action: 'openStudio("expand_preview")' },
    { label: '\u270E Elaborate', action: 'openStudio("elaborate_preview")' },
    { label: '\u2710 Rephrase', action: 'openStudio("rephrase")' },
    { label: '\u2753 Ask', action: 'graphAsk(' + idx + ')' },
    { label: '\u26A1 Verify (Smart DC)', action: 'graphSmartDC()' },
    { label: '\uD83D\uDEB6 Walk', action: 'graphWalk()' },
    { label: '\uD83D\uDD17 Pump to...', action: 'graphPumpStart(' + idx + ')' },
    null, // separator
    { label: '+ Evidence', action: 'graphShowAddEvidence()', show: nodeType === 'hypothesis' || nodeType === 'thought' },
    { label: '\u2192 Chat', action: 'graphDetailToChat()' },
    { label: '\u270F Edit', action: 'graphDetailEdit()' },
    null, // separator
    { label: 'Type: hypothesis', action: 'graphSetNodeType("hypothesis")', show: nodeType !== 'hypothesis' },
    { label: 'Type: fact', action: 'graphSetNodeType("fact")', show: nodeType !== 'fact' },
    { label: 'Type: question', action: 'graphSetNodeType("question")', show: nodeType !== 'question' },
    null,
    { label: '\u2715 Delete', action: 'graphRemoveThought(' + idx + ')' },
  ];
  menu.innerHTML = items.map(item => {
    if (item === null) return '<div style="border-top:1px solid #e0ddd8;margin:2px 0"></div>';
    if (item.show === false) return '';
    return '<div onclick="' + item.action + ';graphHideContextMenu()" style="padding:4px 16px;cursor:pointer;color:#37352f;font-size:13px;" onmouseover="this.style.background=\'#e8f4fd\'" onmouseout="this.style.background=\'\'">' + item.label + '</div>';
  }).join('');
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';
  menu.style.display = '';
  // Close on click outside
  setTimeout(() => document.addEventListener('click', graphHideContextMenu, { once: true }), 0);
}

function graphHideContextMenu() {
  const menu = document.getElementById('graph-context-menu');
  if (menu) menu.style.display = 'none';
}

async function graphAsk(idx) {
  const node = graphData.nodes[idx];
  if (!node) return;
  const params = graphGetParams();
  const res = await fetch('/graph/studio/generate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      mode: 'freeform', index: idx, text: node.text,
      instruction: 'Idea: ' + node.text + '\n\nGenerate ONE probing question that challenges this idea or reveals a hidden assumption. Just the question, nothing else.',
      temp: 0.9, top_k: 40, max_tokens: 1000,
      lang: document.getElementById('lang-select').value,
    })
  });
  const d = await res.json();
  if (d.variants && d.variants.length > 0) {
    graphSaveUndo();
    const r2 = await fetch('/graph/studio/apply-child', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ index: idx, text: d.variants[0].text, type: 'elaborate', ...params })
    });
    const d2 = await r2.json();
    if (!d2.error) {
      graphUpdateView(d2);
      const qIdx = graphData.nodes.length - 1;
      fetch('/graph/set-type', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({index: qIdx, type: 'question'})
      }).catch(() => {});
      if (graphData.nodes[qIdx]) graphData.nodes[qIdx].type = 'question';
      graphDrawSvg();
      graphShowDetail(qIdx);
    }
  }
}

// ── Keyboard shortcuts ──
document.addEventListener('keydown', function(e) {
  if (mode !== 'graph') return;
  // Don't intercept when typing in inputs
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

  if ((e.ctrlKey || e.metaKey) && e.key === 'z') {
    e.preventDefault();
    graphUndo();
  } else if (e.key === 'Delete' || e.key === 'Backspace') {
    if (graphSelectedNode >= 0) {
      e.preventDefault();
      graphRemoveThought(graphSelectedNode);
      graphSelectedNode = -1;
      graphShowDetail(-1);
    }
  } else if (e.key === 'Escape') {
    graphDetailClose();
    graphHideContextMenu();
  }
});

function graphUpdateThoughtsList() {
  const div = document.getElementById('graph-thoughts');
  const nodes = graphData.nodes;
  if (!nodes.length) {
    div.innerHTML = '<span style="color:#b4b2ad;font-size:12px;">Thoughts will appear here...</span>';
    return;
  }
  const nodeCluster = new Array(nodes.length).fill(-1);
  const clusterColors = graphClusterColors;
  (graphData.clusters || []).forEach((cl, ci) => cl.forEach(idx => { nodeCluster[idx] = ci; }));

  // Sort indices by cluster (clustered first, then unclustered)
  const sorted = nodes.map((_, i) => i);
  sorted.sort((a, b) => {
    const ca = nodeCluster[a], cb = nodeCluster[b];
    if (ca === cb) return a - b;
    if (ca < 0) return 1;
    if (cb < 0) return -1;
    return ca - cb;
  });

  div.innerHTML = sorted.map(i => {
    const t = nodes[i].text;
    const nodeType = nodes[i].type || 'thought';
    const ci = nodeCluster[i];
    const dot = ci >= 0
      ? '<span style="color:' + clusterColors[ci % clusterColors.length] + '">&#9679;</span> '
      : '<span style="color:#64748b">&#9675;</span> ';
    const entData = (nodes[i] && nodes[i].entropy) || {};
    let entBadge = '';
    if (entData.avg) {
      const unc = entData.unc || 0;
      // Color: green (0%) → yellow (5%) → red (15%+)
      const t = Math.min(unc / 0.15, 1);
      const r = Math.round(t < 0.5 ? t * 2 * 255 : 255);
      const g = Math.round(t < 0.5 ? 255 : (1 - (t - 0.5) * 2) * 255);
      const entColor = 'rgb(' + r + ',' + g + ',60)';
      entBadge = '<span style="color:' + entColor + ';font-size:11px;white-space:nowrap;margin-left:8px;font-family:Consolas,monospace">'
        + (entData.avg || 0).toFixed(2) + ' / ' + (unc * 100).toFixed(0) + '%</span>';
    }
    const del = '<span onclick="event.stopPropagation();graphRemoveThought(' + i + ')" style="cursor:pointer;color:#64748b;margin-left:6px;font-size:12px" title="Remove">&times;</span>';
    return '<div data-thought-idx="' + i + '" onclick="graphSelectFromList(' + i + ')" '
      + 'class="mb-2 text-sm" style="display:flex;align-items:baseline;cursor:pointer;padding:2px 4px;border-radius:4px;color:#37352f" '
      + 'onmouseover="this.style.background=\'#e8f4fd\'" onmouseout="graphHighlightListItem(this,' + i + ')">'
      + '<span style="flex:1">' + dot + '<span style="color:#64748b;font-size:10px;margin-right:4px">#' + i + '</span>'
      + (nodeType !== 'thought' ? '<span style="color:' + ({hypothesis:'#a78bfa',evidence:'#06b6d4',fact:'#10b981',question:'#f59e0b',goal:'#f43f5e',action:'#8b5cf6'}[nodeType]||'#64748b') + ';font-size:9px;margin-right:3px">[' + nodeType[0].toUpperCase() + ']</span>' : '')
      + t + '</span>' + entBadge + del + '</div>';
  }).join('');
  // AutoSave after every graph mutation
  graphAutoSave();
}

function graphSelectFromList(idx) {
  graphSelectedNode = idx;
  graphRenderSvg();
  graphShowDetail(idx);
  graphHighlightClusterInList(idx);
  // Scroll node into view if needed
  if (graphNodePositions[idx]) {
    const p = graphNodePositions[idx];
    // TODO: could scroll SVG viewBox to node
  }
}

function graphHighlightClusterInList(idx) {
  const cl = (graphData.clusters || []).find(c => c.includes(idx));
  const div = document.getElementById('graph-thoughts');
  div.querySelectorAll('[data-thought-idx]').forEach(el => {
    const i = parseInt(el.dataset.thoughtIdx);
    if (i === idx) {
      el.style.background = '#e8f4fd';
    } else if (cl && cl.includes(i)) {
      el.style.background = '';
    } else {
      el.style.background = '';
    }
  });
}

function graphHighlightListItem(el, idx) {
  // On mouseout, restore cluster highlight if node is selected
  if (graphSelectedNode >= 0) {
    graphHighlightClusterInList(graphSelectedNode);
  } else {
    el.style.background = '';
  }
}

function graphGetParams() {
  const sim_mode = document.getElementById('graph-sim-mode').value;
  const threshold = parseFloat(document.getElementById('graph-threshold').value) || 0.91;
  const lang = document.getElementById('lang-select').value;
  const temp = parseFloat(document.getElementById('graph-temp').value);
  const top_k = parseInt(document.getElementById('graph-topk').value) || 40;
  const seed = parseInt(document.getElementById('graph-seed').value);
  const maxtok_think = parseInt(document.getElementById('graph-maxtok-think').value) || 60;
  const maxtok_expand = parseInt(document.getElementById('graph-maxtok-expand').value) || 120;
  const maxtok_elaborate = parseInt(document.getElementById('graph-maxtok-elaborate').value) || 120;
  const novelty_threshold = parseFloat(document.getElementById('graph-novelty-threshold').value) || 0.92;
  return { sim_mode, threshold, lang, temp, top_k, seed, maxtok_think, maxtok_expand, maxtok_elaborate, novelty_threshold };
}

function graphToggleCollapsePanel() {
  const panel = document.getElementById('graph-collapse-panel');
  const isOpen = panel.style.display !== 'none';
  if (isOpen) { panel.style.display = 'none'; return; }

  const list = document.getElementById('graph-collapse-list');
  const clusters = graphData.clusters || [];
  let html = '';

  // Section 1: Manual selection (if any)
  if (graphManualCollapseMode && graphManualCollapseSet.size > 0) {
    const selCount = graphManualCollapseSet.size;
    // Capture indices now so button works even if set changes later
    const capturedIndices = JSON.stringify([...graphManualCollapseSet]);
    html += '<div style="margin-bottom:8px">'
      + '<div style="color:#facc15;font-size:10px;font-weight:bold;margin-bottom:4px">Selected (' + selCount + ')</div>'
      + '<button onclick="graphDoManualCollapseFrom(' + capturedIndices + ');document.getElementById(\'graph-collapse-panel\').style.display=\'none\'" '
      + 'class="px-3 py-1 bg-emerald-700 hover:bg-emerald-600 text-white text-xs rounded w-full">Collapse ' + selCount + ' selected \u2192 Studio</button>'
      + '<button onclick="graphSendToChat();document.getElementById(\'graph-collapse-panel\').style.display=\'none\'" '
      + 'class="px-3 py-1 bg-blue-700 hover:bg-blue-600 text-white text-xs rounded w-full mt-1">\u2192 Chat</button>'
      + '</div>';
  }

  // Section 2: Auto-clusters
  if (clusters.length > 0) {
    html += '<div style="color:#10b981;font-size:10px;font-weight:bold;margin-bottom:4px">Clusters</div>';
    clusters.forEach((cl, ci) => {
      const color = graphClusterColors[ci % graphClusterColors.length];
      const texts = cl.slice(0, 3).map(i => graphData.nodes[i] ? '#' + i + ' ' + graphData.nodes[i].text.slice(0, 30) : '').join(', ');
      html += '<button onclick="graphCollapse(' + ci + ');document.getElementById(\'graph-collapse-panel\').style.display=\'none\'" '
        + 'style="display:block;width:100%;text-align:left;padding:4px 8px;margin-bottom:2px;background:' + color + '15;border:1px solid ' + color + '40;border-radius:4px;color:#37352f;font-size:11px;cursor:pointer" '
        + 'onmouseover="this.style.background=\'' + color + '30\'" onmouseout="this.style.background=\'' + color + '15\'">'
        + '<span style="color:' + color + ';font-weight:bold">Cluster ' + (ci+1) + '</span> (' + cl.length + ' nodes) '
        + '<span style="color:#64748b">' + texts + '...</span>'
        + '</button>';
    });
  }

  // No clusters and no selection
  if (!html) {
    html = '<div style="color:#64748b;font-size:11px">No clusters or selection. Use Select All or generate more thoughts.</div>';
  }

  list.innerHTML = html;
  panel.style.display = '';

  // Close on outside click
  setTimeout(() => document.addEventListener('click', function handler(e) {
    if (!panel.contains(e.target) && !e.target.closest('#graph-btn-collapse-main')) {
      panel.style.display = 'none';
      document.removeEventListener('click', handler);
    }
  }), 0);
}

function graphToggleManualSelect() {
  graphManualCollapseMode = !graphManualCollapseMode;
  graphManualCollapseSet.clear();
  const btn = document.getElementById('graph-btn-manual-select');
  const actions = document.getElementById('graph-selection-actions');
  if (graphManualCollapseMode) {
    btn.style.background = '#b45309';
    btn.textContent = 'Cancel';
  } else {
    btn.style.background = '';
    btn.textContent = 'Select';
    actions.style.display = 'none';
  }
  graphRenderSvg();
}

function graphSelectAll() {
  graphManualCollapseMode = true;
  graphManualCollapseSet.clear();
  (graphData.nodes || []).forEach((node, i) => {
    if (node.depth === -1) return; // skip topic roots
    graphManualCollapseSet.add(i);
  });
  const btn = document.getElementById('graph-btn-manual-select');
  btn.style.background = '#b45309';
  btn.textContent = '\u2702 Cancel';
  document.getElementById('graph-selection-actions').style.display = '';
  document.getElementById('graph-collapse-count').textContent = graphManualCollapseSet.size;
  graphRenderSvg();
}

function graphSendToChat() {
  if (graphManualCollapseSet.size === 0) { alert('Select at least 1 node'); return; }
  const nodes = graphData.nodes || [];
  const edges = graphData.edges || [];
  const clusters = graphData.clusters || [];
  const selected = [...graphManualCollapseSet].sort((a, b) => a - b);
  const texts = selected.map(i => nodes[i] && nodes[i].text).filter(Boolean);

  // Store structure for structured context mode
  window._graphChatContext = texts;
  window._graphChatIndices = selected;
  window._graphChatStructure = { nodes, edges, clusters, directed: graphDirectedEdges };

  // Add each node to context sidebar
  texts.forEach(t => chatContextAdd(t, 'graph'));

  // Switch to chat mode
  setMode('chat');

  // Exit selection mode
  graphManualCollapseMode = false;
  graphManualCollapseSet.clear();
  document.getElementById('graph-btn-manual-select').style.background = '';
  document.getElementById('graph-btn-manual-select').textContent = '\u2702 Select';
  document.getElementById('graph-selection-actions').style.display = 'none';
}

async function graphDoManualCollapse() {
  if (graphManualCollapseSet.size < 2) { alert('Select at least 2 nodes'); return; }
  graphDoManualCollapseFrom([...graphManualCollapseSet]);
}

function graphDoManualCollapseFrom(cluster) {
  if (!cluster || cluster.length < 2) { alert('Select at least 2 nodes'); return; }
  const ideas = cluster.map(i => graphData.nodes[i]?.text).filter(Boolean);
  graphManualCollapseMode = false;
  graphManualCollapseSet.clear();
  document.getElementById('graph-btn-manual-select').style.background = '';
  document.getElementById('graph-btn-manual-select').textContent = '\u2702 Select';
  document.getElementById('graph-selection-actions').style.display = 'none';

  openStudio('collapse_preview', { ideas, cluster });
}

function graphToggleFlow() {
  graphFlowMode = !graphFlowMode;
  const btn = document.getElementById('graph-btn-flow');
  if (graphFlowMode) {
    btn.style.background = '#0369a1';
    btn.onmouseover = function() { this.style.background='#0284c7'; };
    btn.onmouseout = function() { this.style.background='#0369a1'; };
  } else {
    btn.style.background = '';
    btn.onmouseover = null;
    btn.onmouseout = null;
  }
  // Reset positions so layout recalculates from scratch
  graphNodePositions = [];
  graphDrawSvg();
}


function graphToggleLinkMode() {
  graphLinkMode = !graphLinkMode;
  const btn = document.getElementById('graph-btn-link');
  if (graphLinkMode) {
    btn.style.background = '#7c3aed';
    btn.onmouseover = function() { this.style.background='#6d28d9'; };
    btn.onmouseout = function() { this.style.background='#7c3aed'; };
  } else {
    btn.style.background = '';
    btn.onmouseover = null;
    btn.onmouseout = null;
    graphSelectedNode = -1;
    graphRenderSvg();
  }
}

function graphSimModeChanged() {
  const mode = document.getElementById('graph-sim-mode').value;
  const thEl = document.getElementById('graph-threshold');
  if (mode === 'jaccard') {
    thEl.value = '0.15';
    thEl.step = '0.05';
  } else {
    thEl.value = '0.91';
    thEl.step = '0.01';
  }
  graphRecalcEdges();
}

let _recalcTimer = null;
async function graphRecalcEdges() {
  if (!graphData.nodes.length) return;
  clearTimeout(_recalcTimer);
  _recalcTimer = setTimeout(async () => {
    const r = await fetch('/graph/recalc', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ ...graphGetParams() })
    });
    const d = await r.json();
    if (d.error) return;
    graphUpdateView(d);
    graphUpdateCollapseButtons();
  }, 300);
}

async function graphNodeClick(i) {
  // Manual collapse mode — toggle selection
  if (graphManualCollapseMode) {
    const nodeDepth = (graphData.nodes[i] && graphData.nodes[i].depth);
    if (nodeDepth === -1) return;  // can't collapse topic root
    if (graphManualCollapseSet.has(i)) {
      graphManualCollapseSet.delete(i);
    } else {
      graphManualCollapseSet.add(i);
    }
    document.getElementById('graph-collapse-count').textContent = graphManualCollapseSet.size;
    graphRenderSvg();
    return;
  }
  // Pump mode: second click → find bridge
  if (_pumpSourceIdx >= 0 && _pumpSourceIdx !== i) {
    graphPumpTo(i);
    return;
  }
  if (!graphLinkMode || graphSelectedNode === -1 || graphSelectedNode === i) {
    // Select / deselect node
    graphSelectedNode = graphSelectedNode === i ? -1 : i;
    graphRenderSvg();
    graphShowDetail(graphSelectedNode);
    graphHighlightClusterInList(graphSelectedNode);
    return;
  }
  // Link mode: second click — toggle edge between selected and this node
  const a = graphSelectedNode;
  const b = i;
  graphSelectedNode = -1;
  graphShowDetail(-1);
  const r = await fetch('/graph/link', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ a, b, ...graphGetParams() })
  });
  const d = await r.json();
  if (d.error) { alert(d.error); return; }
  graphSaveUndo();
  graphUpdateView(d);
}

let _graphPrevSelected = -1;
function graphShowDetail(idx) {
  const panel = document.getElementById('graph-detail');
  if (idx < 0 || idx >= graphData.nodes.length) {
    panel.style.display = 'none';
    _graphPrevSelected = -1;
    return;
  }
  // Hebb: strengthen edge between previous and current selection
  if (_graphPrevSelected >= 0 && _graphPrevSelected !== idx) {
    fetch('/graph/navigate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({from: _graphPrevSelected, to: idx})
    }).catch(() => {});
  }
  _graphPrevSelected = idx;
  panel.style.display = '';
  const node = graphData.nodes[idx];
  const text = node.text;
  const entData = (node && node.entropy) || {};
  const detailView = document.getElementById('graph-detail-view');
  const heatmapOn = document.getElementById('heatmap-toggle').checked;
  if (heatmapOn && entData.tokens && entData.tokens.length) {
    // Filter out <think>, </think>, control tokens
    const visibleTokens = entData.tokens.filter(t =>
      !t.token.match(/^<\/?think>$|^<\|.*\|>$/) && t.token.trim() !== ''
    );
    // Skip leading whitespace/newline tokens
    let start = 0;
    while (start < visibleTokens.length && visibleTokens[start].token.match(/^\s+$/)) start++;
    const tokens = visibleTokens.slice(start);
    // Render heatmap — color each token by entropy, using global scale
    const scale = parseFloat(document.getElementById('heatmap-scale').value) || 3;
    detailView.innerHTML = tokens.map(t => {
      const norm = Math.min(1, t.ent / scale);
      const r = Math.round(norm < 0.5 ? norm * 2 * 255 : 255);
      const g = Math.round(norm < 0.5 ? 255 : (1 - (norm - 0.5) * 2) * 200);
      const escaped = t.token.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      return '<span title="ent: ' + t.ent.toFixed(2) + '" style="color:rgb(' + r + ',' + g + ',80)">' + escaped + '</span>';
    }).join('');
  } else {
    detailView.textContent = text;
  }
  document.getElementById('graph-detail-edit-wrap').style.display = 'none';
  document.getElementById('graph-detail-view').style.display = '';
  document.getElementById('graph-detail-edit-btn').textContent = 'Edit';
  // Confidence slider
  const conf = (node && node.confidence != null) ? node.confidence : 0.5;
  const slider = document.getElementById('graph-detail-conf-slider');
  slider.value = Math.round(conf * 100);
  document.getElementById('graph-detail-conf-value').textContent = Math.round(conf * 100) + '%';
  slider.dataset.nodeIdx = idx;
  // Node type selector
  const typeSelect = document.getElementById('graph-detail-type');
  typeSelect.value = node.type || 'thought';
  // Show "+ Evidence" for hypothesis and thought types (auto-converts to hypothesis)
  const canAddEvidence = (node.type === 'hypothesis' || node.type === 'thought');
  document.getElementById('graph-add-evidence-btn').style.display = canAddEvidence ? '' : 'none';
  // Show α/β for hypothesis nodes
  let abDiv = document.getElementById('graph-detail-ab');
  if (!abDiv) {
    abDiv = document.createElement('div');
    abDiv.id = 'graph-detail-ab';
    abDiv.style.cssText = 'font-size:11px;margin-bottom:4px;';
    document.getElementById('graph-detail-confidence').after(abDiv);
  }
  const ab = (graphData._alpha_beta || {})[String(idx)];
  if (node.type === 'hypothesis' && ab) {
    const total = ab.alpha + ab.beta;
    const aPct = total > 0 ? Math.round(ab.alpha / total * 100) : 50;
    const bPct = 100 - aPct;
    abDiv.innerHTML = '<span style="color:#10b981">\u03B1=' + ab.alpha.toFixed(1) + ' (' + aPct + '%)</span>'
      + ' <span style="color:#64748b">|</span> '
      + '<span style="color:#ef4444">\u03B2=' + ab.beta.toFixed(1) + ' (' + bPct + '%)</span>'
      + ' <span style="color:#64748b">| evidence: ' + ab.evidence.length + '</span>'
      + '<div style="height:4px;background:#f0efeb;border-radius:2px;margin-top:2px;display:flex">'
      + '<div style="height:4px;background:#10b981;width:' + aPct + '%;border-radius:2px 0 0 2px"></div>'
      + '<div style="height:4px;background:#ef4444;width:' + bPct + '%;border-radius:0 2px 2px 0"></div>'
      + '</div>';
    // "What changes the mind?" — list evidence sorted by strength
    if (ab.evidence.length > 0) {
      const sorted = [...ab.evidence].sort((a, b) => b.strength - a.strength);
      abDiv.innerHTML += '<div style="margin-top:4px;color:#64748b;font-size:10px">Evidence (strongest first):</div>';
      sorted.forEach(ev => {
        const evNode = graphData.nodes[ev.idx];
        const evText = evNode ? evNode.text.slice(0, 60) : '?';
        const evColor = ev.relation === 'supports' ? '#10b981' : '#ef4444';
        const evSign = ev.relation === 'supports' ? '\u2713' : '\u2717';
        abDiv.innerHTML += '<div onclick="graphSelectFromList(' + ev.idx + ')" style="cursor:pointer;font-size:10px;color:#9ca3af;padding:1px 0" '
          + 'onmouseover="this.style.color=\'#37352f\'" onmouseout="this.style.color=\'#9ca3af\'">'
          + '<span style="color:' + evColor + '">' + evSign + ' ' + ev.strength.toFixed(1) + '</span> '
          + '#' + ev.idx + ' ' + evText + '...</div>';
      });
    }
    abDiv.style.display = '';
  } else {
    abDiv.style.display = 'none';
  }
  // Color dot by cluster
  const nodeClusterArr = new Array(graphData.nodes.length).fill(-1);
  (graphData.clusters || []).forEach((cl, ci) => cl.forEach(j => { nodeClusterArr[j] = ci; }));
  const ci = nodeClusterArr[idx];
  const color = ci >= 0 ? graphClusterColors[ci % graphClusterColors.length] : '#64748b';
  document.getElementById('graph-detail-dot').style.color = color;
  // Show source (parent) if this node was created via expand/elaborate
  const sourceDiv = document.getElementById('graph-detail-source');
  const sourceText = document.getElementById('graph-detail-source-text');
  let parentIdx = -1;
  graphDirectedEdges.forEach(([from, to]) => { if (to === idx) parentIdx = from; });
  if (parentIdx >= 0 && parentIdx < graphData.nodes.length) {
    const pt = graphData.nodes[parentIdx].text;
    sourceText.textContent = pt;
    sourceDiv.style.display = '';
  } else {
    sourceDiv.style.display = 'none';
  }
  // Show connected edges with tp values
  let edgesHtml = '';
  const edges = graphData.edges || [];
  const connEdges = edges.filter(e => e.from === idx || e.to === idx);
  if (connEdges.length) {
    edgesHtml = '<details style="margin-top:8px;border-top:1px solid #e0ddd8;padding-top:6px">'
      + '<summary style="color:#64748b;font-size:11px;cursor:pointer;user-select:none">Edges (' + connEdges.length + ')</summary>';
    connEdges.forEach(e => {
      const other = e.from === idx ? e.to : e.from;
      const tp = e.from === idx ? (e.tp || 0) : (e.tp_rev || 0);
      const otherText = (graphData.nodes[other] && graphData.nodes[other].text || '').slice(0, 50);
      const barWidth = Math.round(tp * 100);
      edgesHtml += '<div onclick="graphSelectFromList(' + other + ')" style="font-size:11px;color:#9ca3af;padding:2px 0;cursor:pointer" '
        + 'onmouseover="this.style.color=\'#37352f\'" onmouseout="this.style.color=\'#9ca3af\'">'
        + '<span style="color:#10b981;font-weight:bold;width:35px;display:inline-block">P=' + tp.toFixed(2) + '</span> '
        + '<span style="color:#64748b">#' + other + '</span> ' + otherText + '...'
        + '<div style="height:2px;background:#f0efeb;margin-top:1px;border-radius:1px">'
        + '<div style="height:2px;background:#10b981;width:' + barWidth + '%;border-radius:1px"></div></div>'
        + '</div>';
    });
    edgesHtml += '</details>';
  }
  // Timestamp info
  let timeHtml = '';
  const created = node.created_at;
  const accessed = node.last_accessed;
  if (created) {
    const fmt = (iso) => {
      try { const d = new Date(iso); return d.toLocaleString(); } catch(e) { return iso; }
    };
    timeHtml = '<div style="margin-top:6px;font-size:10px;color:#64748b">'
      + 'Created: ' + fmt(created);
    if (accessed && accessed !== created) {
      timeHtml += ' | Last: ' + fmt(accessed);
    }
    timeHtml += '</div>';
  }
  // Append timestamp + edges to detail view
  document.getElementById('graph-detail-view').insertAdjacentHTML('beforeend', timeHtml + edgesHtml);
}

function graphDetailToChat() {
  const idx = graphSelectedNode;
  if (idx < 0 || idx >= graphData.nodes.length) return;
  const text = graphData.nodes[idx].text;
  // Store structure for structured mode
  window._graphChatStructure = window._graphChatStructure || {
    nodes: graphData.nodes, edges: graphData.edges,
    clusters: graphData.clusters, directed: graphDirectedEdges
  };
  window._graphChatIndices = [idx];
  window._graphChatContext = [text];
  chatContextAdd(text, 'graph');
  setMode('chat');
}

function graphConfidencePreview(val) {
  document.getElementById('graph-detail-conf-value').textContent = val + '%';
}
// Save confidence on slider change (fires when slider is released with new value)
document.addEventListener('DOMContentLoaded', () => {
  // Sync topic-top → hidden topic field
  const topicTop = document.getElementById('graph-topic-top');
  const topicHidden = document.getElementById('graph-topic');
  if (topicTop && topicHidden) {
    topicTop.addEventListener('input', () => { topicHidden.value = topicTop.value; });
    // Also sync add-type
    const typeTop = document.getElementById('graph-add-type-top');
    const typeHidden = document.getElementById('graph-add-type');
    if (typeTop && typeHidden) {
      typeTop.addEventListener('change', () => { typeHidden.value = typeTop.value; });
    }
  }

  // Load thinking modes into selector
  window._graphModes = {};
  fetch('/modes').then(r => r.json()).then(modes => {
    const sel = document.getElementById('graph-mode-select');
    if (!sel) return;
    modes.forEach(m => {
      window._graphModes[m.id] = m;
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = m.name;
      sel.appendChild(opt);
    });
    sel.value = 'free';
    onGraphModeChange();
  }).catch(() => {});

  // Check model availability on startup
  fetch('/settings').then(r => r.json()).then(async s => {
    if (!s.api_url || !s.api_model) return; // handled by auto-open settings
    try {
      const mr = await fetch('/settings/models', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({api_url: s.api_url, api_key: s.api_key || ''})
      });
      const md = await mr.json();
      const available = md.models || [];
      const warnings = [];
      if (s.api_model && !available.includes(s.api_model)) {
        warnings.push('Main model "' + s.api_model + '" not available');
      }
      if (s.embedding_model && !available.includes(s.embedding_model)) {
        warnings.push('Embedding model "' + s.embedding_model + '" not available');
      }
      if (warnings.length) {
        const bar = document.getElementById('graph-actions-bar') || document.body;
        const warn = document.createElement('div');
        warn.style.cssText = 'color:#facc15;font-size:12px;padding:6px 12px;background:#ffffff;border:1px solid #facc15;border-radius:2px;margin:4px;cursor:pointer;';
        warn.textContent = '\u26A0 ' + warnings.join('. ') + ' \u2014 click to open Settings';
        warn.onclick = function() { openSettings(); this.remove(); };
        bar.prepend(warn);
      }
    } catch(e) {}
  }).catch(e => console.error('startup settings check error:', e));

  const slider = document.getElementById('graph-detail-conf-slider');
  if (slider) {
    slider.addEventListener('change', async () => {
      const idx = parseInt(slider.dataset.nodeIdx);
      const value = parseInt(slider.value) / 100;
      if (isNaN(idx) || idx < 0) return;
      // Update local data immediately
      if (graphData.nodes[idx]) graphData.nodes[idx].confidence = value;
      // Send to backend
      await fetch('/graph/confidence', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({index: idx, value: value})
      });
      // Re-render to update node color
      graphRenderSvg();
    });
  }
});

// ── Smart DC ──
async function _getHorizonParams() {
  try {
    const r = await fetch('/graph/horizon-params');
    return await r.json();
  } catch(e) { return {}; }
}

async function graphSmartDC() {
  const idx = graphSelectedNode;
  if (idx < 0) return;
  const btn = document.getElementById('graph-dc-btn');
  btn.disabled = true; btn.textContent = '\u23F3 Verifying...';
  const hp = await _getHorizonParams();
  try {
    const res = await fetch('/graph/smartdc', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        index: idx,
        lang: document.getElementById('lang-select').value,
        temp: hp.temperature || parseFloat(document.getElementById('graph-temp').value) || 0.9,
        top_k: hp.top_k || parseInt(document.getElementById('graph-topk').value) || 40,
        seed: parseInt(document.getElementById('graph-seed').value) || -1,
        threshold: parseFloat(document.getElementById('graph-threshold').value) || 0.91,
        sim_mode: document.getElementById('graph-sim-mode').value
      })
    });
    const d = await res.json();
    if (d.error) { alert(d.error); return; }

    const detailView = document.getElementById('graph-detail-view');
    let html = '<div style="border-top:1px solid #e0ddd8;margin-top:8px;padding-top:8px">';
    html += '<div style="color:#facc15;font-weight:bold;margin-bottom:6px">\u26A1 Smart DC \u2014 Dialectical Verification</div>';

    // Three poles
    const poleColors = {thesis: '#10b981', antithesis: '#ef4444', neutral: '#64748b'};
    const poleLabels = {thesis: '\u2713 Thesis (FOR)', antithesis: '\u2717 Antithesis (AGAINST)', neutral: '\u25CB Neutral'};
    d.poles.forEach(p => {
      html += '<div style="margin-bottom:6px;padding:6px;background:#f0efeb;border-radius:4px;border-left:3px solid ' + poleColors[p.role] + '">'
        + '<div style="font-size:10px;color:' + poleColors[p.role] + ';font-weight:bold;margin-bottom:2px">' + poleLabels[p.role] + '</div>'
        + '<div style="font-size:12px;color:#37352f">' + p.text + '</div>'
        + '</div>';
    });

    // Synthesis
    html += '<div style="margin-top:8px;padding:8px;background:#e8f4fd;border-radius:4px;border:1px solid #facc15">'
      + '<div style="font-size:10px;color:#facc15;font-weight:bold;margin-bottom:2px">\u2295 SYNTHESIS (confidence: ' + (d.confidence * 100).toFixed(0) + '%' + (d.centroid_distance >= 0 ? ', centroid: ' + d.centroid_distance : '') + ')</div>'
      + '<div style="font-size:12px;color:#37352f">' + d.synthesis + '</div>'
      + '</div>';

    // Action buttons
    html += '<div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">'
      + '<button onclick="graphSmartDCApply(' + d.original_idx + ',\'' + d.synthesis.replace(/'/g, "\\'").replace(/\n/g, "\\n") + '\',' + d.confidence + ')" '
      + 'class="px-3 py-1 bg-emerald-700 hover:bg-emerald-600 text-white text-xs rounded">\u2713 Accept (replace node)</button>'
      + '<button onclick="graphSmartDCAdd(\'' + d.synthesis.replace(/'/g, "\\'").replace(/\n/g, "\\n") + '\',' + d.confidence + ',' + d.original_idx + ')" '
      + 'class="px-3 py-1 bg-blue-700 hover:bg-blue-600 text-white text-xs rounded">+ Add as new node</button>';
    // Recursion: if confidence < 0.8, offer to deepen
    if (d.confidence < 0.8) {
      html += '<button onclick="graphSmartDCDeepen(' + d.original_idx + ',\'' + d.synthesis.replace(/'/g, "\\'").replace(/\n/g, "\\n") + '\')" '
        + 'class="px-3 py-1 bg-yellow-700 hover:bg-yellow-600 text-white text-xs rounded" '
        + 'title="Confidence ' + (d.confidence * 100).toFixed(0) + '% < 80% \u2014 run another Smart DC cycle on the synthesis">'
        + '\u21BB Deepen (' + (d.confidence * 100).toFixed(0) + '% < 80%)</button>';
    }
    html += '</div>';

    html += '</div>';
    detailView.insertAdjacentHTML('beforeend', html);
  } catch(e) {
    console.error('SmartDC error:', e);
  }
  btn.disabled = false; btn.textContent = '\u26A1 Verify';
}

// ── Auto-think (tick) ──
let _autoRunning = false;
let _autoRunLog = [];
let _autoRunAbort = null;

async function graphAutoRun() {
  if (_autoRunning) {
    _autoRunning = false;
    if (_autoRunAbort) _autoRunAbort.abort();
    const _rb = document.getElementById('graph-btn-autorun');
    _rb.textContent = 'Run';
    _rb.style.background = '#6d28d9';
    _rb.style.borderColor = '#6d28d9';
    return;
  }
  // Check if goal exists
  const hasGoal = graphData.nodes && graphData.nodes.some(n => n.type === 'goal');
  if (!hasGoal) {
    const goalText = prompt('Set a goal (point B) before running:\nWhat do you want to achieve?');
    if (!goalText || !goalText.trim()) return;
    // Add goal node
    const r = await fetch('/graph/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ text: goalText.trim(), node_type: 'goal', mode: document.getElementById('graph-mode-select').value || 'horizon', ...graphGetParams() })
    });
    const d = await r.json();
    if (!d.error) {
      graphSaveUndo();
      graphUpdateView(d);
    }
  }

  _autoRunning = true;
  _autoRunAbort = new AbortController();
  _autoRunLog = [];
  _autoRunExhausted = 0;
  _autoRunSparkline = [];
  document.getElementById('graph-run-overlay').style.display = '';
  const btn = document.getElementById('graph-btn-autorun');
  btn.textContent = 'Stop';
  btn.style.background = '#991b1b';
  btn.style.borderColor = '#991b1b';

  const infinite = document.getElementById('graph-autorun-infinite').checked;
  const collapseAt = parseInt(document.getElementById('graph-autorun-steps').value) || 50;
  const hardStop = infinite ? Infinity : collapseAt * 2;
  for (let step = 0; step < hardStop && _autoRunning; step++) {
    const forceCollapse = !infinite && step >= collapseAt;
    // 1. Get tick suggestion
    let res, d;
    try {
      res = await fetch('/graph/tick', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          threshold: parseFloat(document.getElementById('graph-threshold').value) || 0.91,
          sim_mode: document.getElementById('graph-sim-mode').value,
          stable_threshold: parseFloat(document.getElementById('graph-stable-threshold').value) || 0.8,
          min_hyp: parseInt(document.getElementById('graph-min-hyp').value) || 5,
          max_meta: parseInt(document.getElementById('graph-meta-rounds').value) || 2,
          force_collapse: forceCollapse,
        }),
        signal: _autoRunAbort.signal,
      });
      d = await res.json();
    } catch(e) {
      if (e.name === 'AbortError') { _autoRunLog.push({step: step + 1, action: 'STOPPED', reason: 'User stopped'}); break; }
      throw e;
    }
    _autoRunLog.push({step: step + 1, action: d.action, reason: d.reason});

    // Live metrics overlay
    _updateRunOverlay(step + 1, d.phase || d.action, d.horizon_metrics);

    if (d.action === 'none' || d.action === 'stable') {
      _autoRunLog.push({step: step + 1, action: 'STABLE', reason: d.reason});
      break;
    }

    // 2. Execute action automatically
    const targetIdx = Array.isArray(d.target) ? d.target[0] : d.target;
    if (targetIdx !== undefined) {
      graphSelectedNode = targetIdx;
    }

    const hp = d.horizon_params || {};  // dynamic temp/top_k from Horizon
    try {
      if (d.action === 'smartdc') {
        await _autoRunSmartDC(targetIdx, hp);
      } else if (d.action === 'elaborate') {
        await _autoRunElaborate(targetIdx, hp);
      } else if (d.action === 'think_toward') {
        await _autoRunThink(hp);
      } else if (d.action === 'collapse') {
        await _autoRunCollapse(d.target);
      }
    } catch(e) {
      if (e.name === 'AbortError') { _autoRunLog.push({step: step + 1, action: 'STOPPED', reason: 'User stopped'}); break; }
      console.error('AutoRun action error:', e);
      _autoRunLog.push({step: step + 1, action: 'ERROR', reason: e.message});
      break;
    }

    // 3. Update UI + auto-layout
    graphDrawSvg();  // full layout recalc, not just render
    graphUpdateThoughtsList();

    // 3.5 Check model exhaustion — natural convergence
    if (_autoRunExhausted >= 3) {
      _autoRunLog.push({step: step + 1, action: 'EXHAUSTED', reason: 'Model ran out of novel ideas. Converging.'});
      console.log('[autorun] model exhausted — stopping explore, final synthesis');
      break;
    }

    // 4. Small delay for visibility
    await new Promise(r => setTimeout(r, 500));
  }

  _autoRunning = false;
  btn.textContent = 'Run';
  btn.style.background = '#6d28d9';
  btn.style.borderColor = '#6d28d9';
  // Update overlay to show final state
  const _ov = document.getElementById('graph-run-overlay');
  if (_ov) {
    document.getElementById('graph-run-status').textContent = '✓ Complete';
    document.getElementById('graph-run-status').style.color = '#10b981';
    // Keep visible for 10s then hide
    setTimeout(() => { _ov.style.display = 'none'; }, 10000);
  }

  // Final summary — pyramidal batch synthesis
  try {
    const outputFormat = document.getElementById('graph-output-format').value;
    if (outputFormat !== 'none') {
      // Final summary selection criteria:
      // 1. Only hypothesis/thought nodes (not evidence, not goal)
      // 2. Prefer collapse-synthesized nodes (they already contain child ideas)
      // 3. Exclude source nodes that were collapsed into a synthesis
      // 4. Sort by confidence descending — best material first
      const stableThreshold = parseFloat(document.getElementById('graph-stable-threshold').value) || 0.8;
      const directed = graphData.directed_edges || [];
      // Find nodes that are collapse-sources (have directed edge TO a higher-depth node)
      const collapsedInto = new Set();
      for (const [from, to] of directed) {
        const fromN = graphData.nodes[from];
        const toN = graphData.nodes[to];
        if (fromN && toN && toN.depth > fromN.depth) {
          collapsedInto.add(from); // 'from' was collapsed into 'to'
        }
      }
      const allNodes = graphData.nodes
        .filter(n => n.depth >= 0
          && n.type !== 'goal'
          && n.type !== 'evidence'
          && !collapsedInto.has(n.id))
        .sort((a, b) => (b.confidence || 0.5) - (a.confidence || 0.5));
      const indices = allNodes.map(n => n.id);
      if (indices.length >= 2) {
        const goalNode = graphData.nodes.find(n => n.type === 'goal');
        const goalText = goalNode ? goalNode.text : '';
        const params = graphGetParams();

        const BATCH_SIZE = 5;
        let sectionIndices = [];
        const essayTokens = parseInt(document.getElementById('graph-essay-tokens').value) || 6000;

        const batchedEssay = document.getElementById('graph-autorun-batched').checked;
        if (outputFormat === 'essay' && batchedEssay && indices.length > BATCH_SIZE) {
          // Pyramidal: split into batches → collapse each into a section → final essay from sections
          const batches = [];
          for (let i = 0; i < indices.length; i += BATCH_SIZE) {
            batches.push(indices.slice(i, i + BATCH_SIZE));
          }
          _autoRunLog.push({step: _autoRunLog.length + 1, action: 'FINAL_SECTIONS', reason: `${batches.length} batches of ~${BATCH_SIZE} from ${indices.length} nodes`});

          // Phase 1: collapse each batch into a section
          for (let b = 0; b < batches.length; b++) {
            const batch = batches[b];
            console.log(`[autorun] final section ${b+1}/${batches.length}: nodes ${batch.join(',')}`);
            const secRes = await _autoFetch('/graph/collapse', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                cluster: batch,
                mode: 'long',
                no_merge: true,
                custom_max_tokens: 1500,
                collapse_prompt: (goalText ? 'Goal: ' + goalText + '\n' : '') + 'Write a detailed section covering these ideas. Include reasoning and examples.',
                ...params
              })
            });
            const secD = await secRes.json();
            if (!secD.error) {
              graphUpdateView(secD);
              const secIdx = graphData.nodes.length - 1;
              sectionIndices.push(secIdx);
              graphDrawSvg();
              graphUpdateThoughtsList();
            }
          }

          // Phase 2: final essay from sections
          if (sectionIndices.length >= 2) {
            _autoRunLog.push({step: _autoRunLog.length + 1, action: 'FINAL_ESSAY', reason: `essay from ${sectionIndices.length} sections`});
            const essayRes = await _autoFetch('/graph/collapse', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                cluster: sectionIndices,
                mode: 'long',
                no_merge: true,
                custom_max_tokens: essayTokens,
                collapse_prompt: (goalText ? 'Goal: ' + goalText + '\n\n' : '')
                  + 'Combine these sections into a comprehensive essay. Add an introduction and conclusion. Maintain all arguments and details from each section.',
                ...params
              })
            });
            const essayD = await essayRes.json();
            if (!essayD.error) {
              graphUpdateView(essayD);
              const essayIdx = graphData.nodes.length - 1;
              if (goalNode) {
                await _autoFetch('/graph/link', {
                  method: 'POST',
                  headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({ a: essayIdx, b: goalNode.id, ...params })
                }).then(r => r.json()).then(d3 => { if (!d3.error) graphUpdateView(d3); }).catch(() => {});
              }
            }
          }
        } else {
          // Non-essay or small graph: single collapse (as before)
          _autoRunLog.push({step: _autoRunLog.length + 1, action: 'FINAL_SUMMARY', reason: outputFormat + ' from ' + indices.length + ' nodes'});
          const formatPrompts = {
            essay: 'Write a comprehensive essay. Include ALL ideas as sections with reasoning. Add a conclusion. Do NOT repeat the introduction or restate the title.',
            brief: 'Write a brief summary in 3-5 sentences capturing the key findings.',
            list: 'Write a structured list of key points with brief explanations for each.',
          };
          const colRes = await _autoFetch('/graph/collapse', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              cluster: indices,
              mode: outputFormat === 'essay' ? 'long' : 'short',
              no_merge: true,
              custom_max_tokens: outputFormat === 'essay' ? essayTokens : 1500,
              collapse_prompt: (goalText ? 'Goal: ' + goalText + '\n\n' : '') + formatPrompts[outputFormat],
              ...params
            })
          });
          const d2 = await colRes.json();
          if (!d2.error) {
            graphUpdateView(d2);
            const summaryIdx = graphData.nodes.length - 1;
            if (goalNode) {
              await _autoFetch('/graph/link', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ a: summaryIdx, b: goalNode.id, ...params })
              }).then(r => r.json()).then(d3 => { if (!d3.error) graphUpdateView(d3); }).catch(() => {});
            }
          }
        }

        graphDrawSvg();
        graphUpdateThoughtsList();
      }
    }
  } catch(e) {
    if (e.name !== 'AbortError') console.error('Final summary error:', e);
  }
  _autoRunAbort = null;

  // Show log
  const panel = document.getElementById('graph-detail');
  panel.style.display = '';
  document.getElementById('graph-detail-view').innerHTML =
    '<div style="color:#a78bfa;font-weight:bold;margin-bottom:6px">\uD83D\uDD04 Auto-run complete (' + _autoRunLog.length + ' steps)</div>'
    + _autoRunLog.map(l =>
      '<div style="font-size:11px;color:#9ca3af">'
      + '<span style="color:#64748b">#' + l.step + '</span> '
      + '<span style="color:' + ({smartdc:'#facc15',elaborate:'#a78bfa',expand:'#3b82f6',collapse:'#10b981',think_toward:'#3b82f6',elaborate_toward:'#a78bfa',STABLE:'#10b981',ERROR:'#ef4444'}[l.action]||'#64748b') + '">' + l.action + '</span> '
      + l.reason
      + '</div>'
    ).join('');
}

// ── Run overlay: live convergence metrics ──
let _autoRunSparkline = [];

function _updateRunOverlay(step, phase, horizonMetrics) {
  const stableThreshold = parseFloat(document.getElementById('graph-stable-threshold').value) || 0.8;
  const nodes = graphData.nodes || [];
  const hyps = nodes.filter(n => (n.type === 'hypothesis' || n.type === 'thought') && n.depth >= 0);
  const verified = hyps.filter(n => n.confidence >= stableThreshold);
  const allActive = nodes.filter(n => n.depth >= 0 && n.type !== 'evidence' && n.type !== 'goal');
  const avgConf = allActive.length > 0 ? allActive.reduce((s, n) => s + (n.confidence || 0.5), 0) / allActive.length : 0;
  const ratio = hyps.length > 0 ? verified.length / hyps.length : 0;

  document.getElementById('graph-run-step').textContent = step;
  document.getElementById('graph-run-phase').textContent = phase;
  document.getElementById('graph-run-hyps').textContent = hyps.length;
  document.getElementById('graph-run-verified').textContent = verified.length;

  // Horizon metrics
  const hm = horizonMetrics || {};
  const precisionStr = hm.precision !== undefined ? ` Π=${hm.precision}` : '';
  const stateStr = hm.state ? ` ${hm.state.toUpperCase()}` : '';
  document.getElementById('graph-run-avg').textContent = Math.round(avgConf * 100) + '%' + precisionStr;
  document.getElementById('graph-run-status').textContent = phase === 'synthesize' ? '✓ Converged' : '⟳' + stateStr;
  document.getElementById('graph-run-status').style.color = phase === 'synthesize' ? '#10b981' : '#6d28d9';

  // Sparkline: track verified ratio over time
  _autoRunSparkline.push(ratio);
  const canvas = document.getElementById('graph-run-sparkline');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const data = _autoRunSparkline;
  if (data.length < 2) return;

  // Grid line at 100%
  ctx.strokeStyle = '#e2e8f0';
  ctx.lineWidth = 0.5;
  ctx.beginPath();
  ctx.moveTo(0, 2); ctx.lineTo(w, 2);
  ctx.stroke();

  // Curve
  ctx.strokeStyle = '#10b981';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  const step_w = w / Math.max(data.length - 1, 1);
  for (let i = 0; i < data.length; i++) {
    const x = i * step_w;
    const y = h - data[i] * (h - 4) - 2;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.stroke();

  // Fill under curve
  ctx.lineTo((data.length - 1) * step_w, h);
  ctx.lineTo(0, h);
  ctx.closePath();
  ctx.fillStyle = 'rgba(16,185,129,0.1)';
  ctx.fill();
}

// Fetch wrapper for autorun — adds AbortController signal
function _autoFetch(url, opts = {}) {
  if (_autoRunAbort) opts.signal = _autoRunAbort.signal;
  return fetch(url, opts);
}

async function _autoRunSmartDC(idx, hp) {
  const node = graphData.nodes[idx];
  const evidenceContext = [];
  if (node && graphData.nodes) {
    for (const n of graphData.nodes) {
      if (n.type === 'evidence' && n.evidence_target === idx) {
        evidenceContext.push((n.evidence_relation === 'contradicts' ? '[-] ' : '[+] ') + n.text);
      }
    }
  }
  const res = await _autoFetch('/graph/smartdc', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      index: idx,
      lang: document.getElementById('lang-select').value,
      temp: hp.temperature || parseFloat(document.getElementById('graph-temp').value) || 0.9,
      top_k: hp.top_k || parseInt(document.getElementById('graph-topk').value) || 40,
      seed: parseInt(document.getElementById('graph-seed').value) || -1,
      threshold: parseFloat(document.getElementById('graph-threshold').value) || 0.91,
      sim_mode: document.getElementById('graph-sim-mode').value,
      evidence_context: evidenceContext.length > 0 ? evidenceContext : undefined,
    })
  });
  const d = await res.json();
  if (d.synthesis) {
    // Autorun: always replace in place — no new nodes, just update text + confidence
    graphData.nodes[idx].text = d.synthesis;
    graphData.nodes[idx].confidence = d.confidence;
    const r3 = await _autoFetch('/graph/studio/apply-rephrase', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        index: idx, text: d.synthesis, ...graphGetParams()
      })
    });
    const d3 = await r3.json();
    if (!d3.error) graphUpdateView(d3);
    await _autoFetch('/graph/confidence', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({index: idx, value: d.confidence})
    }).catch(() => {});
    // Send horizon feedback: surprise = 1 - confidence
    await _autoFetch('/graph/horizon-feedback', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        surprise: 1 - (d.confidence || 0.5),
        gradient: d.confidence >= 0.8 ? 1 : d.confidence < 0.5 ? -1 : 0,
        phase: 'doubt',
      })
    }).catch(() => {});
  }
}

async function _autoRunElaborate(idx, hp) {
  const params = graphGetParams();
  if (hp && hp.temperature) params.temp = hp.temperature;
  if (hp && hp.top_k) params.top_k = hp.top_k;
  const res = await _autoFetch('/graph/elaborate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ index: idx, n: 2, ...params })
  });
  const d = await res.json();
  if (!d.error) graphUpdateView(d);
}


async function _autoRunThink(hp) {
  const params = graphGetParams();
  if (hp && hp.temperature) params.temp = hp.temperature;
  if (hp && hp.top_k) params.top_k = hp.top_k;
  if (hp && hp.novelty_threshold) params.novelty_threshold = hp.novelty_threshold;
  const goalNode = graphData.nodes.find(n => n.type === 'goal');
  if (!goalNode) { console.log('[autorun] No goal node for Think'); return; }
  const res = await _autoFetch('/graph/think', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ topic: goalNode.text, n: 10, source_idx: goalNode.id, existing: graphData.nodes, ...params })
  });
  const d = await res.json();
  if (!d.error) {
    graphUpdateView(d);
    // Track model exhaustion: if mostly duplicates, signal to stop exploring
    if (d.duplicates_skipped > 0 && d.new_count <= 1) {
      _autoRunExhausted = (_autoRunExhausted || 0) + 1;
      console.log(`[autorun] model exhaustion: ${d.duplicates_skipped} rejected, ${d.new_count} new (streak: ${_autoRunExhausted})`);
    } else {
      _autoRunExhausted = 0;
    }
  }
}

async function _autoRunCollapse(indices) {
  const params = graphGetParams();
  // Autorun merge always removes originals — no_merge would cause infinite growth
  const res = await _autoFetch('/graph/collapse', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ cluster: indices, mode: 'short', no_merge: false, ...params })
  });
  const d = await res.json();
  if (!d.error) {
    graphUpdateView(d);
    const newIdx = graphData.nodes.length - 1;
    graphSelectedNode = newIdx;
    graphDrawSvg();
    graphUpdateThoughtsList();
  }
}



function graphSetNodeType(nodeType) {
  const idx = graphSelectedNode;
  if (idx < 0) return;
  fetch('/graph/set-type', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({index: idx, type: nodeType})
  }).then(r => r.json()).then(d => {
    if (d.ok) {
      graphData.nodes[idx].type = nodeType;
      document.getElementById('graph-add-evidence-btn').style.display =
        (nodeType === 'hypothesis') ? '' : 'none';
      graphRenderSvg();
      graphUpdateThoughtsList();
    }
  }).catch(e => console.error('set-type error:', e));
}

function graphShowAddEvidence() {
  const idx = graphSelectedNode;
  if (idx < 0) return;
  const detailView = document.getElementById('graph-detail-view');
  // Check if evidence form already shown
  if (document.getElementById('graph-evidence-form')) return;
  const html = '<div id="graph-evidence-form" style="margin-top:8px;border-top:1px solid #2eaadc;padding-top:8px">'
    + '<div style="color:#06b6d4;font-weight:bold;font-size:11px;margin-bottom:4px">+ Add Evidence (Bayesian update)</div>'
    + '<input id="graph-evidence-text" type="text" placeholder="Evidence text..." '
    + 'class="w-full px-2 py-1 bg-neutral-100 text-white text-xs rounded border border-neutral-300 mb-2">'
    + '<div class="flex items-center gap-3 mb-2">'
    + '<label class="text-xs text-neutral-400">Relation:</label>'
    + '<select id="graph-evidence-relation" class="text-xs bg-neutral-100 text-neutral-600 border border-neutral-300 rounded px-1">'
    + '<option value="supports">Supports \u2713</option>'
    + '<option value="contradicts">Contradicts \u2717</option>'
    + '</select>'
    + '<label class="text-xs text-neutral-400">Strength:</label>'
    + '<input id="graph-evidence-strength" type="range" min="10" max="99" value="70" step="1" '
    + 'class="w-16 h-1 accent-cyan-500" oninput="document.getElementById(\'graph-evidence-str-val\').textContent=this.value+\'%\'">'
    + '<span id="graph-evidence-str-val" class="text-xs text-neutral-600">70%</span>'
    + '</div>'
    + '<button onclick="graphSubmitEvidence(' + idx + ')" class="px-3 py-1 bg-cyan-700 hover:bg-cyan-600 text-white text-xs rounded">Apply Evidence</button>'
    + '</div>';
  detailView.insertAdjacentHTML('beforeend', html);
}

function graphSubmitEvidence(hypIdx) {
  const text = document.getElementById('graph-evidence-text').value.trim();
  if (!text) return;
  const relation = document.getElementById('graph-evidence-relation').value;
  const strength = parseInt(document.getElementById('graph-evidence-strength').value) / 100;
  fetch('/graph/add-evidence', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      hypothesis: hypIdx,
      text: text,
      relation: relation,
      strength: strength,
      threshold: parseFloat(document.getElementById('graph-threshold').value) || 0.91,
      sim_mode: document.getElementById('graph-sim-mode').value
    })
  }).then(r => r.json()).then(d => {
    if (d.error) { alert(d.error); return; }
    graphUpdateView(d);
    // Show Bayes update result
    if (d.bayes_update) {
      const bu = d.bayes_update;
      const detailView = document.getElementById('graph-detail-view');
      const resultHtml = '<div style="color:#06b6d4;font-size:11px;margin-top:4px">'
        + 'Bayes: ' + (bu.prior * 100).toFixed(0) + '% \u2192 ' + (bu.posterior * 100).toFixed(0) + '% '
        + '(' + bu.relation + ')</div>';
      detailView.insertAdjacentHTML('beforeend', resultHtml);
    }
    graphShowDetail(hypIdx);
  }).catch(e => console.error('add-evidence error:', e));
}

function graphSmartDCDeepen(originalIdx, synthesisText) {
  // Replace node text with current synthesis, then run Smart DC again
  const node = graphData.nodes[originalIdx];
  if (!node) return;
  node.text = synthesisText;
  // Run Smart DC on the updated node
  graphSelectedNode = originalIdx;
  graphSmartDC();
}

function graphSmartDCApply(idx, synthesis, confidence) {
  // Replace node text and update confidence
  const node = graphData.nodes[idx];
  if (!node) return;
  node.text = synthesis;
  node.confidence = confidence;
  // Sync to backend
  fetch('/graph/sync', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      nodes: graphData.nodes,
      edges_data: {
        manual_links: [],
        manual_unlinks: [],
        directed: graphDirectedEdges,
      }
    })
  }).then(() => {
    graphRenderSvg();
    graphUpdateThoughtsList();
    graphShowDetail(idx);
  }).catch(e => console.error('smartdc accept error:', e));
}

function graphSmartDCAdd(synthesis, confidence, parentIdx) {
  // Add synthesis as new child node
  fetch('/graph/studio/apply-child', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      index: parentIdx,
      text: synthesis,
      type: 'elaborate',
      threshold: parseFloat(document.getElementById('graph-threshold').value) || 0.91,
      sim_mode: document.getElementById('graph-sim-mode').value
    })
  }).then(r => r.json()).then(d => {
    graphUpdateView(d);
    graphShowDetail(d.nodes.length - 1);
  }).catch(e => console.error('smartdc add error:', e));
}

function graphDetailClose() {
  graphSelectedNode = -1;
  _graphWalkPath = null;
  document.getElementById('graph-detail').style.display = 'none';
  graphRenderSvg();
  document.getElementById('graph-thoughts').querySelectorAll('div').forEach(d => { d.style.background = ''; });
}

// ── Random Walk ──
let _graphWalkPath = null;
let _graphTraps = [];

async function graphWalk() {
  const idx = graphSelectedNode;
  if (idx < 0) return;
  const btn = document.getElementById('graph-walk-btn');
  btn.disabled = true; btn.textContent = '...';
  try {
    const res = await fetch('/graph/walk', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        start: idx, steps: 5, runs: 50,
        threshold: parseFloat(document.getElementById('graph-threshold').value) || 0.91,
        sim_mode: document.getElementById('graph-sim-mode').value
      })
    });
    const d = await res.json();
    _graphWalkPath = d.path || [];
    graphRenderSvg();
    // Show endpoints in detail view
    const detailView = document.getElementById('graph-detail-view');
    let html = '<div style="color:#10b981;font-weight:bold;margin-bottom:4px">Walk: ' + d.steps + ' steps, ' + d.runs + ' runs</div>';
    html += '<div style="font-size:12px;color:#94a3b8;margin-bottom:6px">Most likely path: ' + _graphWalkPath.map(i => '#' + i).join(' \u2192 ') + '</div>';
    if (d.endpoints && d.endpoints.length) {
      html += '<div style="font-size:12px;color:#37352f;margin-bottom:4px">Top endpoints:</div>';
      d.endpoints.forEach(ep => {
        html += '<div style="font-size:11px;color:#9ca3af;padding:2px 0">'
          + '<span style="color:#10b981;font-weight:bold">' + ep.pct + '%</span> \u2192 '
          + '<span style="cursor:pointer;text-decoration:underline" onclick="graphSelectFromList(' + ep.idx + ')">#' + ep.idx + '</span> '
          + ep.text + '</div>';
      });
    }
    detailView.innerHTML = html;
  } catch(e) {
    console.error('Walk error:', e);
  }
  btn.disabled = false; btn.textContent = '\uD83D\uDEB6 Walk';
}

function graphDetailEdit() {
  const idx = graphSelectedNode;
  if (idx < 0) return;
  const view = document.getElementById('graph-detail-view');
  const wrap = document.getElementById('graph-detail-edit-wrap');
  const btn = document.getElementById('graph-detail-edit-btn');
  if (wrap.style.display === 'none') {
    document.getElementById('graph-detail-textarea').value = graphData.nodes[idx].text;
    wrap.style.display = 'flex';
    view.style.display = 'none';
    btn.textContent = 'Cancel';
  } else {
    wrap.style.display = 'none';
    view.style.display = '';
    btn.textContent = 'Edit';
  }
}

async function graphDetailSave() {
  const idx = graphSelectedNode;
  if (idx < 0) return;
  const newText = document.getElementById('graph-detail-textarea').value.trim();
  if (!newText) return;
  graphSaveUndo();
  // Update via backend: remove old, add new, then reselect
  const r1 = await fetch('/graph/remove', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ index: idx, ...graphGetParams() })
  });
  const d1 = await r1.json();
  if (d1.error) { alert(d1.error); return; }
  if (idx < graphNodePositions.length) graphNodePositions.splice(idx, 1);

  const r2 = await fetch('/graph/add', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ text: newText, ...graphGetParams() })
  });
  const d2 = await r2.json();
  if (d2.error) { alert(d2.error); return; }
  graphSelectedNode = d2.nodes.length - 1;
  graphUpdateView(d2);
  graphShowDetail(graphSelectedNode);
}

async function graphDetailExpand() {
  // Generate child ideas branching from this thought
  const idx = graphSelectedNode;
  if (idx < 0) return;
  const thought = graphData.nodes[idx].text;
  const btn = document.querySelector('[onclick*="graphDetailExpand"]');
  btn.disabled = true; btn.textContent = '...';
  const r = await fetch('/graph/expand', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ index: idx, n: 3, ...graphGetParams() })
  });
  const d = await r.json();
  btn.disabled = false; btn.textContent = '\u261E Expand';
  if (d.error) { alert(d.error); return; }
  graphSaveUndo();
  graphUpdateView(d);
}

function graphDetailElaborate() {
  // Show input for direction, then send
  const wrap = document.getElementById('graph-detail-elaborate-wrap');
  const input = document.getElementById('graph-detail-elaborate-input');
  if (wrap.style.display === 'none') {
    wrap.style.display = 'flex';
    input.value = '';
    input.focus();
  } else {
    wrap.style.display = 'none';
  }
}

async function graphDetailElaborateSend() {
  const idx = graphSelectedNode;
  if (idx < 0) return;
  const direction = document.getElementById('graph-detail-elaborate-input').value.trim();
  document.getElementById('graph-detail-elaborate-wrap').style.display = 'none';
  const r = await fetch('/graph/elaborate', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ index: idx, n: 3, direction, ...graphGetParams() })
  });
  const d = await r.json();
  if (d.error) { alert(d.error); return; }
  graphSaveUndo();
  graphUpdateView(d);
}

async function graphThink() {
  const topic = document.getElementById('graph-topic').value.trim();
  if (!topic) return;
  const n = parseInt(document.getElementById('graph-n').value) || 6;

  const thinkBtn = document.getElementById('graph-btn-think-top') || document.getElementById('graph-btn-think');
  if (thinkBtn) { thinkBtn.disabled = true; thinkBtn.textContent = 'Thinking...'; }

  const r = await fetch('/graph/think', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ topic, n, existing: graphData.nodes, ...graphGetParams() })
  });
  const d = await r.json();
  if (d.error) { alert(d.error); return; }

  graphSaveUndo();
  graphData = d;
  if (d.hub_nodes) d.hub_nodes.forEach(i => graphHubNodes.add(i));
  if (d.directed_edges) graphDirectedEdges = d.directed_edges;
  graphDrawSvg();
  graphUpdateThoughtsList();

  if (thinkBtn) { thinkBtn.disabled = false; thinkBtn.textContent = 'Think'; }

  graphUpdateCollapseButtons();
  document.getElementById('graph-actions-bar').style.display = '';
}

function graphUpdateCollapseButtons() {
  // Update badge and disabled state on Collapse button
  const badge = document.getElementById('graph-collapse-badge');
  const collapseBtn = document.getElementById('graph-btn-collapse-main');
  const clusters = graphData.clusters || [];
  const hasSelection = graphManualCollapseMode && graphManualCollapseSet.size > 0;
  if (clusters.length > 0 || hasSelection) {
    badge.textContent = hasSelection ? graphManualCollapseSet.size : clusters.length;
    badge.style.display = '';
    if (collapseBtn) { collapseBtn.disabled = false; collapseBtn.style.opacity = '1'; }
  } else {
    badge.style.display = 'none';
    if (collapseBtn) { collapseBtn.disabled = true; collapseBtn.style.opacity = '0.4'; }
  }
  // Keep hidden span for backward compat
  const span = document.getElementById('graph-collapse-btns');
  span.innerHTML = '';
}

function graphCollapse(clusterIdx) {
  const clusters = graphData.clusters || [];
  if (clusterIdx >= clusters.length) return;
  const cluster = clusters[clusterIdx];
  const ideas = cluster.map(i => graphData.nodes[i]?.text).filter(Boolean);
  openStudio('collapse_preview', { ideas, cluster });
}

async function graphAddThought() {
  const input = document.getElementById('graph-topic');
  const text = input.value.trim();
  if (!text) return;
  let addType = document.getElementById('graph-add-type').value;
  // If no goal exists yet and mode needs one, auto-set type to goal
  const hasGoal = graphData.nodes && graphData.nodes.some(n => n.type === 'goal');
  const currentMode = document.getElementById('graph-mode-select').value || 'free';
  const needsGoal = currentMode !== 'free' && currentMode !== 'scout';
  if (!hasGoal && needsGoal && addType === 'auto') addType = 'goal';
  const body = { text, node_type: addType, ...graphGetParams() };
  if (addType === 'goal') body.mode = document.getElementById('graph-mode-select').value || 'horizon';
  const r = await fetch('/graph/add', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  const d = await r.json();
  if (d.error) { alert(d.error); return; }
  graphSaveUndo();
  input.value = '';
  graphUpdateView(d);
}

async function graphToGraphAdd() {
  const input = document.getElementById('graph-topic-top');
  const text = input ? input.value.trim() : '';
  if (!text) return;
  const r = await fetch('/graph/add', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ text, ...graphGetParams() })
  });
  const d = await r.json();
  if (d.error) { alert(d.error); return; }
  graphSaveUndo();
  input.value = '';
  graphUpdateView(d);
}

async function graphRemoveThought(i) {
  const r = await fetch('/graph/remove', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ index: i, ...graphGetParams() })
  });
  const d = await r.json();
  if (d.error) { alert(d.error); return; }
  graphSaveUndo();
  if (i < graphNodePositions.length) graphNodePositions.splice(i, 1);
  graphUpdateView(d);
}



function graphExport() {
  const state = {
    topic: document.getElementById('graph-topic').value,
    nodes: graphData.nodes,
    edges: graphData.edges,
    clusters: graphData.clusters,
    positions: graphNodePositions,
    collapsed: [...graphCollapsedNodes],
    hubs: [...graphHubNodes],
    directed: graphDirectedEdges,
    manual_links: graphData.manual_links || [],
    manual_unlinks: graphData.manual_unlinks || [],
    threshold: parseFloat(document.getElementById('graph-threshold').value),
    sim_mode: document.getElementById('graph-sim-mode').value,
  };
  const blob = new Blob([JSON.stringify(state, null, 2)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  const topic = state.topic.slice(0, 30).replace(/[^a-zA-Z\u0430-\u044F\u0410-\u042F0-9 ]/g, '').trim().replace(/\s+/g, '_') || 'graph';
  a.download = `baddle_${topic}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function graphImport(event) {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async function(e) {
    try {
      const state = JSON.parse(e.target.result);
      if (!state.nodes || !Array.isArray(state.nodes)) {
        alert('Invalid graph file'); return;
      }
      // Restore topic and settings
      document.getElementById('graph-topic').value = state.topic || '';
      document.getElementById('graph-topic-top').value = state.topic || '';
      if (state.threshold) document.getElementById('graph-threshold').value = state.threshold;
      if (state.sim_mode) document.getElementById('graph-sim-mode').value = state.sim_mode;

      // Sync state to backend
      const r = await fetch('/graph/sync', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          nodes: state.nodes,
          edges: state.edges || [],
          clusters: state.clusters || [],
          topic: state.topic || '',
          threshold: state.threshold || 0.91,
          sim_mode: state.sim_mode || 'embedding',
          manual_links: state.manual_links || [],
          manual_unlinks: state.manual_unlinks || [],
          directed_edges: state.directed || [],
          hub_nodes: state.hubs || [],
        })
      });
      const d = await r.json();
      if (d.error) { alert(d.error); return; }

      // Restore frontend state
      graphData = d;
      graphNodePositions = state.positions || [];
      graphCollapsedNodes = new Set(state.collapsed || []);
      graphHubNodes = new Set(state.hubs || []);
      graphDirectedEdges = state.directed || [];
      graphUndoStack = [];

      // Ensure positions exist for all nodes
      while (graphNodePositions.length < graphData.nodes.length) {
        graphNodePositions.push({x: Math.random() * 600 + 50, y: Math.random() * 400 + 50, vx: 0, vy: 0});
      }

      document.getElementById('graph-actions-bar').style.display = '';
      document.getElementById('graph-btn-undo').style.display = 'none';
      graphRenderSvg();
      graphUpdateThoughtsList();
      graphUpdateCollapseButtons();
    } catch (err) {
      alert('Error loading graph: ' + err.message);
    }
  };
  reader.readAsText(file);
  event.target.value = '';  // reset file input
}

async function graphBrainstorm(idx) {
  const node = graphData.nodes[idx];
  if (!node) return;
  const params = graphGetParams();
  const hp = await _getHorizonParams();
  if (hp.temperature) params.temp = hp.temperature;
  if (hp.top_k) params.top_k = hp.top_k;
  if (hp.novelty_threshold) params.novelty_threshold = hp.novelty_threshold;
  graphSaveUndo();
  const res = await fetch('/graph/think', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ topic: node.text, n: 5, source_idx: idx, existing: graphData.nodes, ...params })
  });
  const d = await res.json();
  if (!d.error) graphUpdateView(d);
}

// ── Pump (Накачка) ──
let _pumpSourceIdx = -1;

function graphPumpStart(idx) {
  _pumpSourceIdx = idx;
  const node = graphData.nodes[idx];
  // Visual hint
  const bar = document.getElementById('graph-actions-bar');
  if (bar) {
    let hint = document.getElementById('pump-hint');
    if (!hint) {
      hint = document.createElement('div');
      hint.id = 'pump-hint';
      hint.style.cssText = 'color:#f59e0b;font-size:12px;padding:4px 12px;background:#fff;border:1px solid #f59e0b;border-radius:4px;cursor:pointer;';
      hint.onclick = function() { _pumpSourceIdx = -1; this.remove(); };
      bar.prepend(hint);
    }
    hint.textContent = '🔗 Pump: click second node (click here to cancel) — source: #' + idx + ' ' + (node ? node.text.slice(0, 30) : '') + '...';
  }
}

async function graphPumpTo(targetIdx) {
  if (_pumpSourceIdx < 0 || _pumpSourceIdx === targetIdx) return;
  const sourceIdx = _pumpSourceIdx;
  _pumpSourceIdx = -1;
  const hint = document.getElementById('pump-hint');
  if (hint) hint.textContent = '🔗 Pump: searching...';

  try {
    const params = graphGetParams();
    const res = await fetch('/graph/pump', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ node_a: sourceIdx, node_b: targetIdx, max_iterations: 3, ...params })
    });
    const d = await res.json();

    if (hint) hint.remove();

    // Show result in detail panel
    const panel = document.getElementById('graph-detail');
    panel.style.display = '';
    const view = document.getElementById('graph-detail-view');

    if (d.error) {
      view.innerHTML = '<div style="color:#ef4444;font-weight:bold;margin-bottom:4px">🔗 Pump — no bridge found</div>'
        + '<div style="color:#9ca3af;font-size:12px">' + d.error + '</div>';
      return;
    }

    const bridges = d.all_bridges || [];
    let html = '<div style="color:#f59e0b;font-weight:bold;margin-bottom:8px">🔗 Pump — ' + bridges.length + ' bridges (verified)</div>';

    // Store bridges data for save access
    window._pumpBridges = bridges;
    window._pumpSource = sourceIdx;
    window._pumpTarget = targetIdx;

    bridges.forEach((b, i) => {
      const qualPct = ((b.quality || 0) * 100).toFixed(0);
      const leanStr = b.lean !== undefined ? (b.lean > 0.05 ? '→thesis' : b.lean < -0.05 ? '→anti' : '≈balanced') : '';
      const tensionStr = b.tension !== undefined ? (b.tension < 0.6 ? 'deep' : b.tension > 0.8 ? 'trivial' : 'moderate') : '';
      const qualColor = qualPct > 50 ? '#10b981' : qualPct > 30 ? '#f59e0b' : '#ef4444';

      html += '<div style="padding:8px;margin-bottom:6px;background:' + (i === 0 ? '#fef3c7' : '#f8fafc') + ';border-radius:4px;border:1px solid ' + (i === 0 ? '#f59e0b' : '#e2e8f0') + '">'
        + '<div style="color:#37352f;font-size:13px;font-weight:500;margin-bottom:4px">' + b.text + '</div>'
        + '<div style="color:#9ca3af;font-size:10px;margin-bottom:2px">'
        + '<span style="color:' + qualColor + ';font-weight:bold">quality=' + qualPct + '%</span>'
        + (leanStr ? ' · ' + leanStr : '')
        + (tensionStr ? ' · ' + tensionStr : '')
        + (b.dc_confidence ? ' · DC=' + (b.dc_confidence * 100).toFixed(0) + '%' : '')
        + '</div>';

      // Expandable thesis/antithesis
      if (b.thesis || b.antithesis) {
        html += '<div style="margin-bottom:4px">'
          + '<a style="color:#9ca3af;font-size:10px;cursor:pointer;text-decoration:underline" '
          + 'onclick="var d=this.nextElementSibling;d.style.display=d.style.display===\'none\'?\'block\':\'none\'">thesis / antithesis</a>'
          + '<div style="display:none;margin-top:4px;padding:6px;background:#f1f5f9;border-radius:4px;font-size:11px">';
        if (b.thesis) {
          html += '<div style="margin-bottom:4px"><span style="color:#10b981;font-weight:bold">FOR:</span> ' + b.thesis + '</div>';
        }
        if (b.antithesis) {
          html += '<div style="margin-bottom:4px"><span style="color:#ef4444;font-weight:bold">AGAINST:</span> ' + b.antithesis + '</div>';
        }
        if (b.neutral) {
          html += '<div><span style="color:#64748b;font-weight:bold">NEUTRAL:</span> ' + b.neutral + '</div>';
        }
        html += '</div></div>';
      }

      if (b.synthesis) {
        html += '<div style="color:#64748b;font-size:11px;margin-bottom:4px;font-style:italic">'
          + b.synthesis + '</div>';
      }

      html += '<div style="display:flex;gap:4px;margin-top:4px">'
        + '<button onclick="graphPumpSave(' + i + ', \'bridge\', this)" '
        + 'class="px-2 py-0.5 bg-emerald-700 hover:bg-emerald-600 text-white text-xs rounded" title="Save short bridge text">Save bridge</button>';
      if (b.synthesis) {
        html += '<button onclick="graphPumpSave(' + i + ', \'synthesis\', this)" '
          + 'class="px-2 py-0.5 bg-blue-700 hover:bg-blue-600 text-white text-xs rounded" title="Save full synthesis">Save synthesis</button>';
      }
      html += '</div></div>';
    });

    html += '<div style="color:#9ca3af;font-size:10px;margin-top:4px">Iterations: ' + d.iterations + ' · Clouds: A=' + (d.cloud_a||[]).length + ' B=' + (d.cloud_b||[]).length + '</div>';
    view.innerHTML = html;

  } catch(e) {
    if (hint) hint.remove();
    console.error('Pump error:', e);
  }
}

async function graphPumpSave(bridgeIdx, saveType, btnEl) {
  const bridges = window._pumpBridges;
  const sourceIdx = window._pumpSource;
  const targetIdx = window._pumpTarget;
  if (!bridges || bridgeIdx >= bridges.length) return;

  const b = bridges[bridgeIdx];
  const text = saveType === 'synthesis' ? b.synthesis : b.text;
  const confidence = b.quality || 0.5;

  const params = graphGetParams();
  graphSaveUndo();
  const r = await fetch('/graph/add', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ text: text, node_type: 'hypothesis', ...params })
  });
  const d = await r.json();
  if (d.error) { alert(d.error); return; }
  graphUpdateView(d);
  const newIdx = graphData.nodes.length - 1;
  // Set confidence
  await fetch('/graph/confidence', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ index: newIdx, value: confidence })
  }).catch(() => {});
  // Link to both source nodes
  await fetch('/graph/link', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ a: newIdx, b: sourceIdx, ...params })
  }).then(r => r.json()).then(d2 => { if (!d2.error) graphUpdateView(d2); }).catch(() => {});
  await fetch('/graph/link', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ a: newIdx, b: targetIdx, ...params })
  }).then(r => r.json()).then(d2 => { if (!d2.error) graphUpdateView(d2); }).catch(() => {});

  // Mark saved visually but keep panel open
  if (btnEl) {
    btnEl.textContent = 'Saved ✓';
    btnEl.disabled = true;
    btnEl.style.opacity = '0.5';
  }
}

async function graphPumpVerify(bridgeText, sourceIdx, targetIdx) {
  const nodeA = graphData.nodes[sourceIdx];
  const nodeB = graphData.nodes[targetIdx];
  if (!nodeA || !nodeB) return;

  const view = document.getElementById('graph-detail-view');
  view.innerHTML = '<div style="color:#f59e0b">⏳ Verifying bridge...</div>';

  const hp = await _getHorizonParams();
  const res = await fetch('/graph/smartdc', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      index: sourceIdx,
      lang: document.getElementById('lang-select').value,
      temp: hp.temperature || 0.7,
      top_k: hp.top_k || 40,
      seed: -1,
      threshold: parseFloat(document.getElementById('graph-threshold').value) || 0.91,
      sim_mode: document.getElementById('graph-sim-mode').value,
      // Override statement with bridge context
      pump_context: {
        bridge: bridgeText,
        node_a: nodeA.text,
        node_b: nodeB.text,
      }
    })
  });
  const d = await res.json();
  if (d.error) { view.innerHTML = '<div style="color:#ef4444">Error: ' + d.error + '</div>'; return; }

  let html = '<div style="color:#f59e0b;font-weight:bold;margin-bottom:8px">⚡ Bridge Verification</div>';
  html += '<div style="color:#9ca3af;font-size:11px;margin-bottom:6px">A: ' + nodeA.text.slice(0, 50) + ' · B: ' + nodeB.text.slice(0, 50) + '</div>';
  html += '<div style="background:#fef3c7;padding:6px 8px;border-radius:4px;margin-bottom:8px;font-weight:500">' + bridgeText + '</div>';
  if (d.poles) {
    d.poles.forEach(p => {
      const color = p.role === 'thesis' ? '#10b981' : p.role === 'antithesis' ? '#ef4444' : '#64748b';
      html += '<div style="margin-bottom:6px"><span style="color:' + color + ';font-size:11px;font-weight:bold">' + p.role.toUpperCase() + '</span><div style="color:#37352f;font-size:12px">' + p.text + '</div></div>';
    });
  }
  if (d.synthesis) {
    html += '<div style="border-top:1px solid #e2e8f0;padding-top:6px;margin-top:6px"><span style="color:#f59e0b;font-size:11px;font-weight:bold">SYNTHESIS (conf: ' + (d.confidence * 100).toFixed(0) + '%)</span><div style="color:#37352f;font-size:12px">' + d.synthesis + '</div></div>';
  }
  // Store for save
  window._pumpBridges = [{ text: bridgeText, synthesis: d.synthesis || '', quality: d.confidence || 0.5 }];
  window._pumpSource = sourceIdx;
  window._pumpTarget = targetIdx;
  html += '<div style="display:flex;gap:4px;margin-top:8px">'
    + '<button onclick="graphPumpSave(0, \'bridge\', this)" class="px-3 py-1 bg-emerald-700 hover:bg-emerald-600 text-white text-xs rounded">Save bridge</button>'
    + (d.synthesis ? '<button onclick="graphPumpSave(0, \'synthesis\', this)" class="px-3 py-1 bg-blue-700 hover:bg-blue-600 text-white text-xs rounded">Save synthesis</button>' : '')
    + '</div>';
  view.innerHTML = html;
}

function onGraphModeChange() {
  const sel = document.getElementById('graph-mode-select');
  const mode = window._graphModes && window._graphModes[sel.value];
  if (!mode) return;

  // Update tooltip
  const tip = document.getElementById('graph-mode-tooltip');
  if (tip) tip.textContent = mode.tooltip || '';

  // Update placeholder
  const lang = document.getElementById('lang-select').value;
  const ph = (lang === 'ru' ? mode.placeholder : mode.placeholder_en) || mode.placeholder || 'Topic / Goal...';
  const topicInput = document.getElementById('graph-topic-top');
  if (topicInput) topicInput.placeholder = ph;
}

function graphReset() {
  if (!confirm('Reset graph? This cannot be undone.')) return;
  fetch('/graph/reset', { method: 'POST' });
  // Delete autosave
  fetch('/graph/delete', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: '_autosave'}) }).catch(() => {});
  graphData = { nodes: [], edges: [], clusters: [] };
  graphSelectedNode = -1;
  graphLinkMode = false;
  graphFlowMode = false;
  graphManualCollapseMode = false;
  graphManualCollapseSet.clear();
  _graphCurrentSaveName = '';
  const flowBtn = document.getElementById('graph-btn-flow');
  if (flowBtn) { flowBtn.style.background = ''; flowBtn.onmouseover = null; flowBtn.onmouseout = null; }
  graphZoom = 1; graphPanX = 0; graphPanY = 0;
  const linkBtn = document.getElementById('graph-btn-link');
  if (linkBtn) { linkBtn.style.background = ''; linkBtn.onmouseover = null; linkBtn.onmouseout = null; }
  graphNodePositions = [];
  graphUndoStack = [];
  graphCollapsedNodes = new Set();
  graphHubNodes = new Set();
  graphDirectedEdges = [];
  if (graphSimTimer) { cancelAnimationFrame(graphSimTimer); graphSimTimer = null; }
  document.getElementById('graph-svg').innerHTML = '';
  document.getElementById('graph-thoughts').innerHTML =
    '<span style="color:#b4b2ad;font-size:12px;">Thoughts will appear here...</span>';
  document.getElementById('graph-collapse-btns').innerHTML = '';
  document.getElementById('graph-collapse-panel').style.display = 'none';
  document.getElementById('graph-detail').style.display = 'none';
  document.getElementById('graph-btn-undo').style.display = 'none';
  document.getElementById('graph-actions-bar').style.display = 'none';
  document.getElementById('graph-topic').value = '';
  document.getElementById('graph-topic-top').value = '';
}

// --------------- Server Save / Load / AutoSave ---------------

function _graphBuildSaveState() {
  return {
    topic: document.getElementById('graph-topic').value,
    nodes: graphData.nodes,
    edges: graphData.edges,
    clusters: graphData.clusters,
    positions: graphNodePositions,
    collapsed: [...graphCollapsedNodes],
    hubs: [...graphHubNodes],
    directed: graphDirectedEdges,
    manual_links: graphData.manual_links || [],
    manual_unlinks: graphData.manual_unlinks || [],
    threshold: parseFloat(document.getElementById('graph-threshold').value),
    sim_mode: document.getElementById('graph-sim-mode').value,
  };
}

let _graphCurrentSaveName = '';

function graphSave() {
  if (!graphData.nodes || !graphData.nodes.length) {
    alert('Nothing to save — graph is empty'); return;
  }
  const topic = document.getElementById('graph-topic').value || '';
  const defaultName = _graphCurrentSaveName || topic.slice(0, 40).replace(/[^\w\u0400-\u04FF\s-]/g, '').trim().replace(/\s+/g, '_') || 'untitled';
  const name = prompt('Save graph as:', defaultName);
  if (!name) return;

  const state = _graphBuildSaveState();
  state.name = name;
  fetch('/graph/save', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(state)
  }).then(r => r.json()).then(d => {
    if (d.error) { alert(d.error); return; }
    _graphCurrentSaveName = d.name;
    console.log('[save] saved as', d.name);
  }).catch(e => alert('Save error: ' + e.message));
}

function graphLoadList() {
  fetch('/graph/list').then(r => r.json()).then(d => {
    const graphs = d.graphs || [];
    const panel = document.getElementById('graph-load-panel-top') || document.getElementById('graph-load-panel');
    if (!panel) return;
    panel.innerHTML = '<div style="color:var(--text-primary);font-size:12px;font-weight:600;margin-bottom:8px">Load graph</div>';

    if (!graphs.length) {
      panel.innerHTML += '<div style="color:var(--text-secondary);font-size:11px;padding:8px 0">No saved graphs yet</div>';
    } else {
      graphs.forEach(g => {
        const modified = new Date(g.modified * 1000).toLocaleString();
        const isAuto = g.name === '_autosave';
        const label = isAuto ? '(autosave)' : g.name;
        const div = document.createElement('div');
        div.style.cssText = 'padding:6px 8px;cursor:pointer;border-radius:4px;margin-bottom:2px;display:flex;justify-content:space-between;align-items:center';
        div.onmouseover = () => div.style.background = '#e8f4fd';
        div.onmouseout = () => div.style.background = '';
        div.innerHTML = '<div>'
          + '<div style="font-size:12px;color:var(--text-primary)">' + label + '</div>'
          + '<div style="font-size:10px;color:var(--text-secondary)">' + (g.topic || '') + ' &middot; ' + g.nodes_count + ' nodes &middot; ' + modified + '</div>'
          + '</div>'
          + '<div style="display:flex;gap:4px">'
          + '<button onclick="event.stopPropagation();graphLoadByName(\'' + g.name + '\')" class="btn-action" style="font-size:10px;padding:2px 8px">Load</button>'
          + (isAuto ? '' : '<button onclick="event.stopPropagation();graphDeleteByName(\'' + g.name + '\')" class="btn-action" style="font-size:10px;padding:2px 8px;color:#dc2626;border-color:#dc2626">&#10005;</button>')
          + '</div>';
        panel.appendChild(div);
      });
    }

    // Footer: Import/Export file buttons
    const footer = document.createElement('div');
    footer.style.cssText = 'border-top:1px solid var(--border);margin-top:8px;padding-top:8px;display:flex;gap:6px';
    footer.innerHTML = '<button onclick="document.getElementById(\'graph-import-file-top\').click();this.closest(\'[id^=graph-load-panel]\').style.display=\'none\'" class="btn-action" style="font-size:10px;padding:3px 10px">Import file</button>'
      + '<button onclick="graphExport();this.closest(\'[id^=graph-load-panel]\').style.display=\'none\'" class="btn-action" style="font-size:10px;padding:3px 10px">Export file</button>';
    panel.appendChild(footer);
    panel.style.display = '';

    // Close on outside click
    setTimeout(() => document.addEventListener('click', function handler(e) {
      if (!panel.contains(e.target)) {
        panel.style.display = 'none';
        document.removeEventListener('click', handler);
      }
    }), 10);
  });
}

function graphLoadByName(name) {
  fetch('/graph/load', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name})
  }).then(r => r.json()).then(d => {
    if (d.error) { alert(d.error); return; }
    const state = d.data;
    _graphRestoreState(state);
    _graphCurrentSaveName = name === '_autosave' ? '' : name;
    // Hide load panel
    const panel = document.getElementById('graph-load-panel-top') || document.getElementById('graph-load-panel');
    if (panel) panel.style.display = 'none';
  }).catch(e => alert('Load error: ' + e.message));
}

function graphDeleteByName(name) {
  if (!confirm('Delete graph "' + name + '"?')) return;
  fetch('/graph/delete', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name})
  }).then(r => r.json()).then(d => {
    if (d.ok) graphLoadList(); // refresh list
    else alert(d.error || 'Delete failed');
  });
}

async function _graphRestoreState(state) {
  document.getElementById('graph-topic').value = state.topic || '';
  document.getElementById('graph-topic-top').value = state.topic || '';
  if (state.threshold) document.getElementById('graph-threshold').value = state.threshold;
  if (state.sim_mode) document.getElementById('graph-sim-mode').value = state.sim_mode;

  const r = await fetch('/graph/sync', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      nodes: state.nodes,
      edges: state.edges || [],
      clusters: state.clusters || [],
      topic: state.topic || '',
      threshold: state.threshold || 0.91,
      sim_mode: state.sim_mode || 'embedding',
      manual_links: state.manual_links || [],
      manual_unlinks: state.manual_unlinks || [],
      directed_edges: state.directed || [],
      hub_nodes: state.hubs || [],
    })
  });
  const d = await r.json();
  if (d.error) { alert(d.error); return; }

  graphData = d;
  graphNodePositions = state.positions || [];
  graphCollapsedNodes = new Set(state.collapsed || []);
  graphHubNodes = new Set(state.hubs || []);
  graphDirectedEdges = state.directed || [];
  graphUndoStack = [];

  while (graphNodePositions.length < graphData.nodes.length) {
    graphNodePositions.push({x: Math.random() * 600 + 50, y: Math.random() * 400 + 50, vx: 0, vy: 0});
  }

  document.getElementById('graph-actions-bar').style.display = '';
  document.getElementById('graph-btn-undo').style.display = 'none';
  graphRenderSvg();
  graphUpdateThoughtsList();
  graphUpdateCollapseButtons();
}

// AutoSave — debounced, saves after every mutation
let _autoSaveTimer = null;
function graphAutoSave() {
  if (!graphData.nodes || !graphData.nodes.length) return;
  if (_autoSaveTimer) clearTimeout(_autoSaveTimer);
  _autoSaveTimer = setTimeout(() => {
    const state = _graphBuildSaveState();
    state.name = '_autosave';
    fetch('/graph/save', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(state)
    }).catch(() => {}); // silent
  }, 2000);
}

// Check for autosave on startup
function graphCheckAutosave() {
  fetch('/graph/list').then(r => r.json()).then(d => {
    const graphs = d.graphs || [];
    const autosave = graphs.find(g => g.name === '_autosave');
    if (autosave && autosave.nodes_count > 0) {
      const modified = new Date(autosave.modified * 1000).toLocaleString();
      if (confirm('Restore previous session?\n(' + autosave.topic + ', ' + autosave.nodes_count + ' nodes, ' + modified + ')')) {
        graphLoadByName('_autosave');
      }
    }
  }).catch(() => {});
}

// Call on page load (after a small delay to let UI initialize)
setTimeout(graphCheckAutosave, 800);


// ═══════════════════════════════════════════════════════════════════════════
// Generation Studio — universal modal for rephrase/elaborate/expand/collapse
// ═══════════════════════════════════════════════════════════════════════════

let studioMode = 'rephrase';
let studioNodeIdx = -1;
let studioVariants = [];
let studioCollapseData = null;

function openStudio(mode, opts) {
  opts = opts || {};
  studioMode = mode;
  studioNodeIdx = (typeof opts.index === 'number') ? opts.index : graphSelectedNode;
  studioVariants = [];
  studioCollapseData = opts.cluster ? opts : null;

  document.getElementById('studio-variants').innerHTML = '<span class="text-neutral-500 text-sm">Click Generate to create variants...</span>';
  document.getElementById('studio-apply-btn').disabled = true;
  document.getElementById('studio-apply-btn').style.opacity = '0.5';

  // For collapse, set source before _studioUpdatePlaceholder
  if (mode === 'collapse_preview' && opts.ideas) {
    document.getElementById('studio-source').textContent = opts.ideas.map((t, i) => `${i + 1}. ${t}`).join('\n');
  }

  // Filter available modes based on context
  const sel = document.getElementById('studio-mode-select');
  const isCollapse = (mode === 'collapse_preview');
  sel.querySelectorAll('option').forEach(opt => {
    if (isCollapse) {
      opt.hidden = (opt.value !== 'collapse_preview' && opt.value !== 'freeform');
    } else {
      opt.hidden = (opt.value === 'collapse_preview');
    }
  });
  sel.value = mode;
  _studioUpdatePlaceholder(mode);

  document.getElementById('studio-instruction').value = opts.instruction || '';
  document.getElementById('studio-modal').style.display = 'flex';
  document.getElementById('studio-instruction').focus();
}

function _studioUpdatePlaceholder(mode) {
  const source = document.getElementById('studio-source');
  const instruction = document.getElementById('studio-instruction');

  if (mode === 'rephrase') {
    source.textContent = (graphData.nodes[studioNodeIdx] && graphData.nodes[studioNodeIdx].text) || '';
    instruction.placeholder = 'Instruction: "make shorter", "translate to English", "add specifics"...';
  } else if (mode === 'elaborate_preview') {
    source.textContent = (graphData.nodes[studioNodeIdx] && graphData.nodes[studioNodeIdx].text) || '';
    instruction.placeholder = 'Direction to elaborate...';
  } else if (mode === 'expand_preview') {
    source.textContent = (graphData.nodes[studioNodeIdx] && graphData.nodes[studioNodeIdx].text) || '';
    instruction.placeholder = 'Focus for branching...';
  } else if (mode === 'collapse_preview') {
    // source already set by openStudio for collapse
    instruction.placeholder = 'Collapse instruction...';
  } else if (mode === 'ask') {
    source.textContent = (graphData.nodes[studioNodeIdx] && graphData.nodes[studioNodeIdx].text) || '';
    instruction.placeholder = 'What kind of question? (leave empty for auto)';
  } else {
    source.textContent = (graphData.nodes[studioNodeIdx] && graphData.nodes[studioNodeIdx].text) || '';
    instruction.placeholder = 'Instruction...';
  }
  studioMode = mode;
}

function studioSwitchMode(newMode) {
  studioVariants = [];
  _studioUpdatePlaceholder(newMode);
  document.getElementById('studio-variants').innerHTML = '<span class="text-neutral-500 text-sm">Click Generate to create variants...</span>';
  document.getElementById('studio-apply-btn').disabled = true;
  document.getElementById('studio-apply-btn').style.opacity = '0.5';
}


function closeStudio() {
  document.getElementById('studio-modal').style.display = 'none';
  studioVariants = [];
}

async function studioGenerate() {
  const instruction = document.getElementById('studio-instruction').value.trim();
  const temp = parseFloat(document.getElementById('studio-temp').value) || 0.9;
  const topK = parseInt(document.getElementById('studio-topk').value) || 40;
  const maxTok = parseInt(document.getElementById('studio-maxtok').value) || 1000;
  const count = parseInt(document.getElementById('studio-count').value) || 1;
  const genBtn = document.getElementById('studio-gen-btn');

  const body = {
    mode: studioMode,
    source_text: (graphData.nodes[studioNodeIdx] && graphData.nodes[studioNodeIdx].text) || '',
    instruction,
    temp, top_k: topK, max_tokens: maxTok,
    lang: document.getElementById('lang-select') ? document.getElementById('lang-select').value : 'en',
  };

  // For collapse, pass ideas
  if (studioMode === 'collapse_preview') {
    body.ideas = document.getElementById('studio-source').textContent.split('\n').map(l => l.replace(/^\d+\.\s*/, ''));
  }
  // Ask mode → freeform with question instruction + source context
  if (studioMode === 'ask') {
    body.mode = 'freeform';
    body.index = studioNodeIdx;
    body.text = body.source_text;
    const askPrompt = body.instruction || 'Generate ONE probing question that challenges this idea or reveals a hidden assumption. Just the question, nothing else.';
    body.instruction = 'Idea: ' + body.source_text + '\n\n' + askPrompt;
  }

  genBtn.disabled = true;

  try {
    for (let i = 0; i < count; i++) {
      genBtn.textContent = count > 1 ? `Generating ${i + 1}/${count}...` : 'Generating...';
      const r = await fetch('/graph/studio/generate', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
      });
      const d = await r.json();
      console.log('[studio] response:', d);
      if (d.error) { alert(d.error); break; }
      if (!d.text || !d.text.trim()) { continue; }  // skip empty, try next

      studioVariants.push(d);
      renderStudioVariants();
    }
  } finally {
    genBtn.disabled = false;
    genBtn.textContent = '+ Generate';
  }
}

function renderStudioVariants() {
  const div = document.getElementById('studio-variants');
  const applyBtn = document.getElementById('studio-apply-btn');
  if (!studioVariants.length) {
    div.innerHTML = '<span class="text-neutral-500 text-sm">No variants yet...</span>';
    applyBtn.disabled = true;
    return;
  }

  div.innerHTML = studioVariants.map((v, i) => {
    const selected = (v._selected) ? 'border-amber-500' : 'border-neutral-300';
    const entStr = v.entropy ? ` <span class="text-neutral-500">(ent: ${(v.entropy.avg || 0).toFixed(2)})</span>` : '';
    return `<div class="p-3 border ${selected} rounded cursor-pointer hover:border-neutral-500 transition-colors"
                 onclick="studioSelectVariant(${i})" style="background:#ffffff;">
              <div class="text-neutral-700 text-sm whitespace-pre-wrap">${v.text}</div>
              <div class="text-xs text-neutral-500 mt-1">Variant ${i + 1}${entStr}</div>
            </div>`;
  }).join('');

  const selectedCount = studioVariants.filter(v => v._selected).length;
  applyBtn.disabled = !selectedCount;
  applyBtn.style.opacity = selectedCount ? '1' : '0.5';
  applyBtn.textContent = selectedCount > 1 ? `Accept (${selectedCount})` : 'Accept';
}

function studioSelectVariant(idx) {
  // Rephrase: single select (radio). Others: multi-select (toggle).
  if (studioMode === 'rephrase') {
    studioVariants.forEach((v, i) => v._selected = (i === idx));
  } else {
    studioVariants[idx]._selected = !studioVariants[idx]._selected;
  }
  renderStudioVariants();
}

async function studioApply() {
  const selected = studioVariants.find(v => v._selected);
  const anySelected = studioVariants.some(v => v._selected);
  if (!anySelected) return;

  if (studioMode === 'rephrase') {
    if (!selected) return;
    // Apply rephrase: replace node text
    const r = await fetch('/graph/studio/apply-rephrase', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ index: studioNodeIdx, text: selected.text, ...graphGetParams() })
    });
    const d = await r.json();
    if (d.error) { alert(d.error); return; }
    graphSaveUndo();
    graphData = d;
    if (d.hub_nodes) d.hub_nodes.forEach(i => graphHubNodes.add(i));
    if (d.directed_edges) graphDirectedEdges = d.directed_edges;
    graphRenderSvg();
    graphUpdateThoughtsList();
    graphShowDetail(studioNodeIdx);
  } else if (studioMode === 'collapse_preview' && studioCollapseData) {
    // Apply collapse: use the existing collapse endpoint with the selected text as override
    const cluster = studioCollapseData.cluster || [];
    const body = { cluster, collapse_mode: 'short', ...graphGetParams() };
    body.collapse_override = selected.text;  // pre-generated text
    const r = await fetch('/graph/collapse', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    const d = await r.json();
    if (d.error) { alert(d.error); return; }
    graphSaveUndo();
    // Update positions: remove collapsed, add new at centroid
    const removedSet = new Set(cluster);
    let cx = 0, cy = 0, cnt = 0;
    cluster.forEach(idx => {
      if (idx < graphNodePositions.length) {
        cx += graphNodePositions[idx].x;
        cy += graphNodePositions[idx].y;
        cnt++;
      }
    });
    if (cnt) { cx /= cnt; cy /= cnt; }
    [...removedSet].sort((a, b) => b - a).forEach(idx => {
      if (idx < graphNodePositions.length) graphNodePositions.splice(idx, 1);
    });
    graphNodePositions.push({ x: cx || 250, y: cy || 225, vx: 0, vy: 0, fixed: false });
    // Remap collapsed node markers
    const newCollapsed = new Set();
    graphCollapsedNodes.forEach(ci => {
      if (removedSet.has(ci)) return;
      let newIdx = ci;
      for (const r of [...removedSet].sort((a,b) => a-b)) { if (r < ci) newIdx--; }
      newCollapsed.add(newIdx);
    });
    newCollapsed.add(d.nodes.length - 1);
    graphCollapsedNodes = newCollapsed;
    graphData = d;
    if (d.hub_nodes) d.hub_nodes.forEach(i => graphHubNodes.add(i));
    if (d.directed_edges) graphDirectedEdges = d.directed_edges;
    graphRenderSvg();
    graphUpdateThoughtsList();
    graphUpdateCollapseButtons();
    const newIdx = d.nodes.length - 1;
    if (newIdx >= 0) graphSelectFromList(newIdx);
  } else if (studioMode === 'elaborate_preview' && studioNodeIdx >= 0) {
    // Add all selected as elaborations — linked to parent with directed edge
    const allSelected = studioVariants.filter(v => v._selected);
    graphSaveUndo();
    for (const sel of allSelected) {
      const r = await fetch('/graph/studio/apply-child', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ index: studioNodeIdx, text: sel.text, type: 'elaborate', ...graphGetParams() })
      });
      const d = await r.json();
      if (d.error) { alert(d.error); continue; }
      graphData = d;
      if (d.hub_nodes) d.hub_nodes.forEach(i => graphHubNodes.add(i));
      if (d.directed_edges) graphDirectedEdges = d.directed_edges;
    }
    graphDrawSvg(true);
    graphUpdateThoughtsList();
  } else if (studioMode === 'expand_preview' && studioNodeIdx >= 0) {
    // Add all selected as expansions — linked to parent with directed edge
    const allSelected = studioVariants.filter(v => v._selected);
    graphSaveUndo();
    for (const sel of allSelected) {
      const r = await fetch('/graph/studio/apply-child', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ index: studioNodeIdx, text: sel.text, type: 'expand', ...graphGetParams() })
      });
      const d = await r.json();
      if (d.error) { alert(d.error); continue; }
      graphData = d;
      if (d.hub_nodes) d.hub_nodes.forEach(i => graphHubNodes.add(i));
      if (d.directed_edges) graphDirectedEdges = d.directed_edges;
    }
    graphDrawSvg(true);
    graphUpdateThoughtsList();
  } else if (studioMode === 'ask' && studioNodeIdx >= 0) {
    // Add all selected as question nodes linked to parent
    const allSelected = studioVariants.filter(v => v._selected);
    graphSaveUndo();
    for (const sel of allSelected) {
      const r = await fetch('/graph/studio/apply-child', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ index: studioNodeIdx, text: sel.text, type: 'elaborate', ...graphGetParams() })
      });
      const d = await r.json();
      if (d.error) { alert(d.error); continue; }
      graphData = d;
      if (d.hub_nodes) d.hub_nodes.forEach(i => graphHubNodes.add(i));
      if (d.directed_edges) graphDirectedEdges = d.directed_edges;
      // Set type to question
      const qIdx = graphData.nodes.length - 1;
      fetch('/graph/set-type', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({index: qIdx, type: 'question'})
      }).catch(() => {});
      if (graphData.nodes[qIdx]) graphData.nodes[qIdx].type = 'question';
    }
    graphDrawSvg(true);
    graphUpdateThoughtsList();
  } else {
    // Freeform and others: add all selected as unlinked thoughts
    const allSelected = studioVariants.filter(v => v._selected);
    graphSaveUndo();
    for (const sel of allSelected) {
      const r = await fetch('/graph/add', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ text: sel.text, ...graphGetParams() })
      });
      const d = await r.json();
      if (d.error) { alert(d.error); continue; }
      graphData = d;
      if (d.hub_nodes) d.hub_nodes.forEach(i => graphHubNodes.add(i));
      if (d.directed_edges) graphDirectedEdges = d.directed_edges;
    }
    graphDrawSvg(true);
    graphUpdateThoughtsList();
  }
  closeStudio();
}

// Init UI state
setMode('graph');
