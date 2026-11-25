/* Shared airway-trail computation for the Endoluminal 3D scene and the
   Anatomical 3D panel. Both read the same case-web graph.json, so the trail
   and its destination node are identical.
   Scene coords: x = patient left, y = superior, z = anterior, tree-centred. */
(function (global) {
  "use strict";

  function adjacency(graph) {
    const N = graph.radius.length;
    const P = graph.pos;
    const deg = new Int32Array(N);
    for (const v of graph.edges) deg[v]++;
    const off = new Int32Array(N + 1);
    for (let i = 0; i < N; i++) off[i + 1] = off[i] + deg[i];
    const nbr = new Int32Array(off[N]);
    const wgt = new Float32Array(off[N]);
    const fill = off.slice(0, N);
    const dist3 = (a, b) => Math.hypot(
      P[3 * a] - P[3 * b], P[3 * a + 1] - P[3 * b + 1], P[3 * a + 2] - P[3 * b + 2]);
    for (let k = 0; k < graph.edges.length; k += 2) {
      const a = graph.edges[k], b = graph.edges[k + 1], w = dist3(a, b);
      nbr[fill[a]] = b; wgt[fill[a]++] = w;
      nbr[fill[b]] = a; wgt[fill[b]++] = w;
    }
    return { deg, off, nbr, wgt };
  }

  function dijkstra(graph, adj, src) {
    const N = graph.radius.length;
    const { off, nbr, wgt } = adj;
    const dist = new Float64Array(N).fill(Infinity);
    const prev = new Int32Array(N).fill(-1);
    const hn = [], hd = [];
    const push = (n, d) => {
      let i = hn.length; hn.push(n); hd.push(d);
      while (i > 0) {
        const p = (i - 1) >> 1;
        if (hd[p] <= d) break;
        hn[i] = hn[p]; hd[i] = hd[p]; i = p;
      }
      hn[i] = n; hd[i] = d;
    };
    const pop = () => {
      const top = hn[0], td = hd[0], ln = hn.pop(), ld = hd.pop();
      if (hn.length) {
        let i = 0;
        for (;;) {
          let c = 2 * i + 1; if (c >= hn.length) break;
          if (c + 1 < hn.length && hd[c + 1] < hd[c]) c++;
          if (hd[c] >= ld) break;
          hn[i] = hn[c]; hd[i] = hd[c]; i = c;
        }
        hn[i] = ln; hd[i] = ld;
      }
      return [top, td];
    };
    dist[src] = 0; push(src, 0);
    while (hn.length) {
      const [u, d] = pop();
      if (d > dist[u]) continue;
      for (let k = off[u]; k < off[u + 1]; k++) {
        const nd = d + wgt[k];
        if (nd < dist[nbr[k]]) { dist[nbr[k]] = nd; prev[nbr[k]] = u; push(nbr[k], nd); }
      }
    }
    return prev;
  }

  function nearestNode(graph, targets) {
    const N = graph.radius.length, P = graph.pos;
    const step = Math.max(1, Math.floor(targets.length / 600));
    let best = -1, bestD = Infinity, bestT = null;
    for (let n = 0; n < N; n++) {
      for (let t = 0; t < targets.length; t += step) {
        const q = targets[t];
        const d = Math.hypot(
          P[3 * n] - q.x, P[3 * n + 1] - q.y, P[3 * n + 2] - q.z);
        if (d < bestD) { bestD = d; best = n; bestT = q; }
      }
    }
    return { node: best, gap: bestD, point: bestT };
  }

  // Ordered node path from graph.start to the node nearest any target point,
  // plus the smoothed polyline (same smoothing as the 3D tube).
  function routeToTarget(graph, targets) {
    if (!graph || !targets || !targets.length) return null;
    const adj = adjacency(graph);
    const prev = dijkstra(graph, adj, graph.start);
    const hit = nearestNode(graph, targets);
    if (hit.node < 0) return null;
    const path = [];
    for (let n = hit.node; n !== -1; n = prev[n]) path.push(n);
    path.reverse();
    if (path.length < 3) return null;
    const P = graph.pos, W = 4;
    const raw = path.map(n => [P[3 * n], P[3 * n + 1], P[3 * n + 2]]);
    const points = raw.map((_, i) => {
      const a = Math.max(0, i - W), b = Math.min(raw.length - 1, i + W);
      const v = [0, 0, 0];
      for (let j = a; j <= b; j++) {
        v[0] += raw[j][0]; v[1] += raw[j][1]; v[2] += raw[j][2];
      }
      const k = b - a + 1;
      return [v[0] / k, v[1] / k, v[2] / k];
    });
    points[0] = raw[0].slice();
    points[points.length - 1] = raw[raw.length - 1].slice();
    return {
      path,
      points,
      end: raw[raw.length - 1],
      target: hit.point,
      gap: hit.gap,
    };
  }

  // The 3D scene is RAS mapped as x = -RAS.x, y = RAS.z, z = RAS.y, then
  // offset by the scene centre. Keep both directions here so the renderer and
  // the iframe cannot drift apart.
  function sceneToRas(p, center) {
    const c = center || [0, 0, 0];
    return [-p[0] - c[0], p[2] + c[2], p[1] + c[1]];
  }

  function rasToScene(r, center) {
    const c = center || [0, 0, 0];
    return [-r[0] - c[0], r[2] - c[1], r[1] - c[2]];
  }

  global.EndoRoute = {
    adjacency, dijkstra, nearestNode, routeToTarget, sceneToRas, rasToScene,
  };
})(typeof window !== "undefined" ? window : globalThis);
