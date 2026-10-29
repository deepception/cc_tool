/* cc-dash graph engine: the Factory floor's live node graph. No dependencies.
   Exposes window.ccGraph = { create(host, opts) }; the view (factory.js) owns
   every visual decision through face(), nodeClass(), nodeLabel(), edgeStyle()
   and CSS on the classes below. This file owns geometry, layout, the camera,
   interaction, keyboard and accessibility plumbing, and the frame budget.

   Rendering split. Edges, particles, glow and the dot lattice go on one
   <canvas> (.gx-canvas): they are thousands of moving primitives that are
   cheap to repaint every frame, carry no text and nothing in them needs to be
   reachable by keyboard or a screen reader. Nodes are real DOM (.gx-node
   inside .gx-world): text stays crisp at any zoom, each node takes real focus
   with a roving tabindex, carries an aria-label, gets hover and click for
   free, is themed by plain CSS, and the needs-you breathing runs on the
   compositor instead of in this loop. The world div holds the camera
   (translate(x, y) scale(k), origin 0 0); the canvas draws the same transform
   with ctx.setTransform, so the two layers never drift apart.

   Layout (cold: first data, or no node survived the poll):
   1. Hierarchy from node.parent (a missing parent or a cycle makes a root).
      Each root is a cluster; hub kinds (projects) are the usual roots.
   2. Seeding, deterministic (FNV-1a hash of the id, never Math.random): a
      balloon tree per cluster. Bottom-up, every subtree gets an enclosing
      radius sized for wide boxes; top-down, any non-root node's children fan
      out on an arc facing away from its parent, and a root's children go on a
      full ring whose spacing is measured, not estimated (each branch's real
      angular extent, every box corner seen from the root, is fed back and the
      ring re-packed until neighbouring branches stop overlapping). Orbit kinds
      (orders) take ring slots next to the branch their `runs` edge points
      into, on the side where the target hangs. The ring is then turned so the
      heaviest branch lies horizontal: floors come out wide, like screens.
   3. Force simulation per cluster (clusters never interact), run to
      completion synchronously from a calm start: parent links whose rest
      length keeps a kind-dependent gap between the two shapes along the link,
      orbit kinds on a stiff circle round their hub, box collision (AABB with
      padding, circles for shape 'circle') resolved along the line of centres
      so bearings and the tree's structure survive, mild charge inside a
      cluster, children kept ahead of their parent (away from the
      grandparent), other edges such as `runs` a weak spring. A position pass
      then removes any overlap left.
   4. Clusters are packed as boxes: each goes beside a placed one at the spot
      that keeps the zoom needed to fit highest (the fit's rectangles, see
      Camera); a few aspect biases are tried and the best whole floor wins.
   ~300 nodes lay out in ~110 ms of script; the last part of the run (from
   alpha 0.06) is replayed as a 460 ms eased tail, so first load is settled
   well inside 1.5 s.
   Warm restart (ids key everything): a poll with the same id set moves nothing
   and leaves the camera alone. A structural poll pins every surviving node;
   each new subtree is seeded beside its parent (in the widest free angular gap
   round it, a hash-jittered angle, searched outward until its boxes overlap
   nothing) or, as a new cluster, packed beside the existing ones; only the new
   nodes are simulated, and one the simulation squeezed is re-seated at the
   nearest free spot. Survivors therefore move 0 px. New elements carry
   .is-entering for their first two frames (CSS fades/scales them in with the
   independent `scale` property, since `transform` carries the position) and
   glide out from their parent; removed ones get .is-leaving and go after
   220 ms. Dragging a node moves it and its subtree; it stays where dropped.

   Camera. setInsets() names the screen px along each edge that glass covers
   (the view's instrument panel, an open inspector) and one rect that floats
   over the floor (insets.avoid: the zoom buttons). A fit puts everything in
   the free rect (host minus insets), centred, unless a node would then land
   on the avoid rect; then it uses the part of the free rect beside the avoid
   rect that fits the floor at the highest zoom, so no node ever sits under
   the buttons. When the free rect changes or a node is picked, reframe(id)
   applies one rule (autoFrame): a camera the user has not moved since the
   last fit is fitted again while that stays readable (at or above mid, or no
   lower than now); otherwise the picked node, its parent and its children are
   panned (zoomed out only if they cannot fit) into the free rect, clear of
   the avoid rect. Closing the inspector (reframe(null)) fits a floor that was
   fitted again; a camera the user moved stays where it is.

   Level of detail: host[data-lod] = near (k >= .85), mid (k >= .55), far.
   The lattice doubles its pitch at mid and is hidden at far.

   Frame loop and pause rules: one requestAnimationFrame loop that runs only
   while something moves (the layout tail, a camera move, particles on active
   edges, entering nodes, a pending redraw); it stops when settled with no
   particles (no active edge, or reduced motion), never runs while the
   document is hidden, the host has no size or the view paused it (pause(),
   the List mode), and restarts on any change. New node elements go into the
   world at their place in data order (the view sends tree order, so reading
   order follows the tree); an element already there is never moved.
   prefers-reduced-motion (live): no particles, layout shown settled at once,
   camera moves jump, no entering/leaving delays. Edge colours are CSS custom
   property names resolved on the host and re-resolved when <html data-theme>
   or prefers-color-scheme changes; the numeric --gx-* tokens are read the
   same way. CSP (style-src 'self') holds: styles are set through the CSSOM. */
'use strict';
(() => {
  const TAU = Math.PI * 2;
  const K_MIN = 0.2, K_MAX = 2.5, FIT_MIN = K_MIN, FIT_MAX = 1.25, FIT_MARGIN = 28;   // a fit shows everything if any zoom can
  const LOD_NEAR = 0.85, LOD_MID = 0.55;
  const CAM_MS = 320, TAIL_MS = 460, SPAWN_MS = 420, LEAVE_MS = 220;
  const SLOP = 4, DBL_MS = 400;
  const P_CAP = 900, P_SPEED = 140;
  const GRID = 32, EDGE_PAD = 3;
  const AVOID_PAD = 8, FRAME_MARGIN = 24;
  const DEFAULT_KIND = { w: 180, h: 56 };
  /* Layout knobs, world px at zoom 1. */
  const LY = {
    gapHub: 44,        // hub boundary -> child boundary along the link
    gapOrbit: 36,      // hub -> orbit kind (orders): the inner ring
    gapChild: 18,      // any other parent -> child
    pad: 10,           // collision padding inside a cluster
    padCluster: 36,    // between cluster bounding boxes
    arc: 3.6,          // radians available to a non-root node's children
    spread: 0.15,      // extra radians between siblings when the arc has room
    linkK: 0.45, orbitK: 0.8, springK: 0.02,
    charge: 250, chargeCut: 560, outK: 0.3,
    coll: 0.75,
    vKeep: 0.6, vMax: 24,
    ticksCold: 300, ticksWarm: 120, alphaMin: 0.002, alphaCold: 0.5, alphaWarm: 0.3, tailAlpha: 0.06,
  };

  const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
  const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
  function fnv(s) {
    let h = 0x811c9dc5;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); }
    return h >>> 0;
  }
  const unit = (s) => fnv(String(s)) / 4294967296;
  /* CSS cubic-bezier timing function: Newton steps, bisection fallback. */
  function cubicBezier(x1, y1, x2, y2) {
    const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
    const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
    const sx = (t) => ((ax * t + bx) * t + cx) * t;
    const sy = (t) => ((ay * t + by) * t + cy) * t;
    const dx = (t) => (3 * ax * t + 2 * bx) * t + cx;
    return (x) => {
      if (x <= 0) return 0;
      if (x >= 1) return 1;
      let t = x;
      for (let i = 0; i < 8; i++) {
        const e = sx(t) - x;
        if (Math.abs(e) < 1e-6) return sy(t);
        const d = dx(t);
        if (Math.abs(d) < 1e-6) break;
        t -= e / d;
      }
      let lo = 0, hi = 1;
      t = x;
      for (let i = 0; i < 40; i++) {
        const v = sx(t);
        if (Math.abs(v - x) < 1e-6) break;
        if (v < x) lo = t; else hi = t;
        t = (lo + hi) / 2;
      }
      return sy(t);
    };
  }
  const easeInOut = cubicBezier(0.77, 0, 0.175, 1);     // --ease-in-out in app.css
  const easeOut = (t) => 1 - (1 - t) * (1 - t) * (1 - t);

  /* Centre-to-boundary distance of a node's shape along unit vector (ux, uy). */
  function rayR(n, ux, uy) {
    if (n.circle) return n.hw;
    const ax = Math.abs(ux), ay = Math.abs(uy);
    const tx = ax > 1e-9 ? n.hw / ax : Infinity, ty = ay > 1e-9 ? n.hh / ay : Infinity;
    return tx < ty ? tx : ty;
  }
  /* Seeding footprint radius: a wide box needs most of its half-width. */
  const ownR = (n) => (n.circle ? n.hw : 0.64 * n.hw + 0.36 * n.hh);

  /* Overlap test between two shapes with padding. On overlap it returns true and
     leaves in SEP the vector that pushes b clear of a: along the line of
     centres for two boxes (so relative bearings, and with them the tree's
     structure, survive), from the closest point for circle-box. */
  const SEP = [0, 0];
  function sepOf(ax, ay, ahw, ahh, ac, bx, by, bhw, bhh, bc, pad) {
    let dx = bx - ax, dy = by - ay;
    if (ac && bc) {
      const r = ahw + bhw + pad, d2 = dx * dx + dy * dy;
      if (d2 >= r * r) return false;
      let d = Math.sqrt(d2);
      if (d < 1e-6) { dx = 1e-3; dy = 0; d = 1e-3; }
      const o = (r - d) / d;
      SEP[0] = dx * o; SEP[1] = dy * o;
      return true;
    }
    if (ac || bc) {
      const vx = ac ? ax - bx : bx - ax, vy = ac ? ay - by : by - ay;   // box centre -> circle centre
      const qhw = ac ? bhw : ahw, qhh = ac ? bhh : ahh, need = (ac ? ahw : bhw) + pad;
      const ex = vx - clamp(vx, -qhw, qhw), ey = vy - clamp(vy, -qhh, qhh), e2 = ex * ex + ey * ey;
      if (e2 >= need * need) return false;
      let sx, sy;
      if (e2 > 1e-9) { const e = Math.sqrt(e2), o = (need - e) / e; sx = ex * o; sy = ey * o; } else {
        const ox = qhw + need - Math.abs(vx), oy = qhh + need - Math.abs(vy);
        if (ox < oy) { sx = vx >= 0 ? ox : -ox; sy = 0; } else { sx = 0; sy = vy >= 0 ? oy : -oy; }
      }
      if (ac) { SEP[0] = -sx; SEP[1] = -sy; } else { SEP[0] = sx; SEP[1] = sy; }   // (sx, sy) moves the circle
      return true;
    }
    const ox = ahw + bhw + pad - Math.abs(dx);
    if (ox <= 0) return false;
    const oy = ahh + bhh + pad - Math.abs(dy);
    if (oy <= 0) return false;
    const d = Math.sqrt(dx * dx + dy * dy);
    if (d < 1e-6) { SEP[0] = ox; SEP[1] = 0; return true; }
    const ux = dx / d, uy = dy / d, qx = Math.abs(ux), qy = Math.abs(uy);
    const t = Math.min(qx > 1e-9 ? (ahw + bhw + pad) / qx : Infinity, qy > 1e-9 ? (ahh + bhh + pad) / qy : Infinity);
    SEP[0] = ux * (t - d); SEP[1] = uy * (t - d);              // along the line of centres: keeps their bearing
    return true;
  }

  function create(host, opts) {
    opts = opts || {};
    const doc = host.ownerDocument || document;
    const win = doc.defaultView || window;
    const kinds = opts.kinds && typeof opts.kinds === 'object' ? opts.kinds : {};
    const perf = win.performance || Date;
    const now = () => perf.now();
    const mm = (q) => { try { return win.matchMedia ? win.matchMedia(q) : null; } catch (_) { return null; } };
    const mqReduce = mm('(prefers-reduced-motion: reduce)');
    const mqDark = mm('(prefers-color-scheme: dark)');
    let reduced = !!(mqReduce && mqReduce.matches);
    const warned = new Set();
    function call(fn, ...args) {
      if (typeof fn !== 'function') return undefined;
      try { return fn(...args); } catch (err) {
        const k = fn.name || String(fn).slice(0, 40);
        if (!warned.has(k) && win.console) { warned.add(k); win.console.error('ccGraph callback failed', err); }
        return undefined;
      }
    }

    // ── DOM ─────────────────────────────────────────────────────────────
    const prev = { touchAction: host.style.touchAction, userSelect: host.style.userSelect, position: host.style.position, overflow: host.style.overflow };
    host.classList.add('gx');
    host.style.touchAction = 'none';
    host.style.userSelect = 'none';
    try {
      const hcs = win.getComputedStyle(host);
      if (hcs.position === 'static') host.style.position = 'relative';
      if (hcs.overflow === 'visible') host.style.overflow = 'hidden';
    } catch (_) { /* detached host: geometry comes on the first resize */ }
    const canvas = doc.createElement('canvas');
    canvas.className = 'gx-canvas';
    canvas.setAttribute('aria-hidden', 'true');
    Object.assign(canvas.style, { position: 'absolute', left: '0', top: '0', width: '0', height: '0' });
    const world = doc.createElement('div');
    world.className = 'gx-world';
    world.setAttribute('role', 'group');
    world.setAttribute('aria-label', typeof opts.label === 'string' && opts.label ? opts.label : 'Graph');
    Object.assign(world.style, { position: 'absolute', left: '0', top: '0', width: '0', height: '0', transformOrigin: '0 0' });
    host.append(canvas, world);
    let ctx = null;
    try { ctx = canvas.getContext('2d'); } catch (_) { ctx = null; }
    const scratch = doc.createElement('canvas').getContext('2d');

    // ── State ───────────────────────────────────────────────────────────
    const nodes = new Map();           // id -> node record (live)
    let order = [];                    // live nodes in data order
    let roots = [];
    let edges = [];
    let edgeById = new Map();
    let activeCount = 0;
    const leaving = new Map();         // id -> { n, timer }
    const entering = new Set();
    const cam = { x: 0, y: 0, k: 1 };
    let camAnim = null, camUser = false, fitted = false, pendingFit = false, keep = null;
    const insets = { top: 0, right: 0, bottom: 0, left: 0, avoid: null };
    let W = 0, H = 0, dpr = 1, lod = '';
    let selected = null, related = null, rover = null, tabNode = null, hoverId = null;
    let tween = null;
    let simAlpha = 0, simTicks = 0, settled = true, settleT0 = now(), settleMs = 0;
    let raf = 0, lastTs = 0, dirty = true, posDirty = false, groupsDirty = true, groups = [];
    const frames = new Float64Array(60);
    let frameN = 0;
    let pCount = 0;
    const pT = new Float32Array(P_CAP);
    const pE = new Array(P_CAP);
    const tok = { edgeAlpha: 0.28, activeAlpha: 0.75, glow: 0.5, blend: 'source-over', dimAlpha: 0.1, psize: 2.2, grid: null };
    let themeCS = null;
    const colors = new Map();          // css name or colour -> normalised colour
    const sprites = new Map();         // normalised colour -> particle sprite canvas
    let destroyed = false, paused = false;
    const invEls = new Set();          // nodes carrying --gx-inv (see syncInv)
    let gesture = null, lastTap = null;
    const ptrs = new Map();
    let unkeyed = 0;

    // ── Theme: tokens and colours ───────────────────────────────────────
    function normColor(v) {
      if (!v || !scratch) return null;
      scratch.fillStyle = '#000000';
      scratch.fillStyle = v;
      const a = scratch.fillStyle;
      scratch.fillStyle = '#ffffff';
      scratch.fillStyle = v;
      return a === scratch.fillStyle ? a : null;
    }
    function rgbOf(c) {
      if (c[0] === '#') {
        const n = parseInt(c.slice(1, 7), 16);
        return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1];
      }
      const m = c.match(/[\d.]+/g) || [];
      return [+m[0] || 0, +m[1] || 0, +m[2] || 0, m.length > 3 ? +m[3] : 1];
    }
    function resolve(name) {
      let c = colors.get(name);
      if (c) return c;
      let v = name;
      if (typeof name === 'string' && name.startsWith('--')) v = themeCS ? themeCS.getPropertyValue(name).trim() : '';
      c = normColor(v) || '#8a8880';
      colors.set(name, c);
      return c;
    }
    function readTheme() {
      try { themeCS = win.getComputedStyle(host); } catch (_) { themeCS = null; }
      const cs = themeCS;
      const num = (name, d) => {
        const v = cs ? parseFloat(cs.getPropertyValue(name)) : NaN;
        return Number.isFinite(v) ? v : d;
      };
      tok.edgeAlpha = clamp(num('--gx-edge-alpha', 0.28), 0, 1);
      tok.activeAlpha = clamp(num('--gx-edge-active-alpha', 0.75), 0, 1);
      tok.glow = clamp(num('--gx-glow', 0.5), 0, 1);
      tok.dimAlpha = clamp(num('--gx-dim-alpha', 0.1), 0, 1);
      tok.psize = clamp(num('--gx-particle-size', 2.2), 0.3, 12);
      const bl = cs ? cs.getPropertyValue('--gx-blend').trim() : '';
      tok.blend = 'source-over';
      if (bl && scratch) {
        scratch.globalCompositeOperation = 'source-over';
        scratch.globalCompositeOperation = bl;
        if (scratch.globalCompositeOperation === bl) tok.blend = bl;
        scratch.globalCompositeOperation = 'source-over';
      }
      const gr = cs ? cs.getPropertyValue('--gx-grid').trim() : '';
      tok.grid = !gr || gr === 'none' ? null : normColor(gr);
      colors.clear();
      if (sprites.size > 48) sprites.clear();
      for (const e of edges) e.color = resolve(e.cname);
      groupsDirty = true;
      dirty = true;
    }
    function sprite(col) {
      let s = sprites.get(col);
      if (s) return s;
      const S = 64;
      s = doc.createElement('canvas');
      s.width = S; s.height = S;
      const g = s.getContext('2d');
      const [r, gg, b] = rgbOf(col);
      const mix = (t) => Math.round(r + (255 - r) * t) + ',' + Math.round(gg + (255 - gg) * t) + ',' + Math.round(b + (255 - b) * t);
      const grd = g.createRadialGradient(S / 2, S / 2, 0, S / 2, S / 2, S / 2);
      grd.addColorStop(0, 'rgba(' + mix(0.55) + ',1)');
      grd.addColorStop(0.12, 'rgba(' + mix(0.25) + ',0.95)');
      grd.addColorStop(0.3, 'rgba(' + mix(0) + ',0.42)');
      grd.addColorStop(0.6, 'rgba(' + mix(0) + ',0.1)');
      grd.addColorStop(1, 'rgba(' + mix(0) + ',0)');
      g.fillStyle = grd;
      g.fillRect(0, 0, S, S);
      sprites.set(col, s);
      return s;
    }

    // ── Nodes ───────────────────────────────────────────────────────────
    function specOf(kind) {
      const k = kinds[kind] && typeof kinds[kind] === 'object' ? kinds[kind] : DEFAULT_KIND;
      const circle = k.shape === 'circle';
      const w = isNum(k.w) && k.w > 0 ? k.w : DEFAULT_KIND.w;
      const h = circle ? w : isNum(k.h) && k.h > 0 ? k.h : DEFAULT_KIND.h;
      return { w, h, circle, hub: !!k.hub, orbit: !!k.orbit };
    }
    function applySpec(n) {
      const sp = specOf(n.kind);
      n.w = sp.w; n.h = sp.h; n.hw = sp.w / 2; n.hh = sp.h / 2;
      n.circle = sp.circle; n.hub = sp.hub; n.orbit = sp.orbit;
      n.el.style.width = sp.w + 'px';
      n.el.style.height = sp.h + 'px';
      n.el.dataset.kind = n.kind;
    }
    function makeNode(id, d) {
      const el = doc.createElement('div');
      el.dataset.id = id;
      el.setAttribute('role', 'button');
      el.tabIndex = -1;
      Object.assign(el.style, { position: 'absolute', left: '0', top: '0' });
      const n = {
        id, d, kind: String(d.kind == null ? '' : d.kind), el,
        w: 0, h: 0, hw: 0, hh: 0, circle: false, hub: false, orbit: false,
        x: 0, y: 0, fx: 0, fy: 0, sx: 0, sy: 0, placed: false, fixed: false,
        parent: null, kids: [], root: null, mass: 1, idx: 0,
        R: 0, off: 0, ringD: 0,
        json: null, ucls: '', label: '', cls: null, tf: '', enter: 0,
      };
      applySpec(n);
      return n;
    }
    function syncNode(n, force) {
      let json;
      try { json = JSON.stringify(n.d); } catch (_) { json = '\u0000' + (++unkeyed); }   // not serialisable: always refresh
      if (!force && json === n.json) return;
      n.json = json;
      const uc = call(opts.nodeClass, n.d);
      n.ucls = typeof uc === 'string' ? uc.trim() : '';
      const lb = call(opts.nodeLabel, n.d);
      n.label = typeof lb === 'string' && lb ? lb : String(n.d.label == null ? n.id : n.d.label);
      if (n.el.getAttribute('aria-label') !== n.label) n.el.setAttribute('aria-label', n.label);
      if (typeof opts.face === 'function') {
        const f = call(opts.face, n.d);
        if (f != null) {
          if (win.ccDom && win.ccDom.into) win.ccDom.into(n.el, f);
          else n.el.replaceChildren(...[].concat(f).filter((x) => x != null));
        }
      }
    }
    function composeClass(n) {
      let c = 'gx-node';
      if (n.ucls) c += ' ' + n.ucls;
      if (selected === n.id) c += ' is-selected';
      else if (related) c += related.has(n.id) ? ' is-related' : ' is-dim';
      if (n.enter > 0) c += ' is-entering';
      if (hoverId === n.id) c += ' is-hover';
      if (c !== n.cls) { n.el.className = c; n.cls = c; }
    }
    function applyClasses() { for (const n of order) composeClass(n); }
    function writeTransforms() {
      for (const n of order) {
        const tf = 'translate3d(' + n.x.toFixed(1) + 'px, ' + n.y.toFixed(1) + 'px, 0) translate(-50%, -50%)';
        if (tf !== n.tf) { n.tf = tf; n.el.style.transform = tf; }
      }
      posDirty = false;
    }
    function updateTabindex() {
      const t = (rover && nodes.get(rover)) || (selected && nodes.get(selected)) || order[0] || null;
      if (tabNode && tabNode !== t) tabNode.el.tabIndex = -1;
      if (t && t.el.tabIndex !== 0) t.el.tabIndex = 0;
      tabNode = t;
    }
    function leave(n) {
      if (hoverId === n.id) { hoverId = null; call(opts.onHover, null); }
      if (rover === n.id) rover = null;
      if (tabNode === n) tabNode = null;
      n.el.tabIndex = -1;
      n.el.removeAttribute('aria-current');
      entering.delete(n);
      n.enter = 0;
      if (reduced || doc.hidden) { n.el.remove(); return; }
      n.el.classList.remove('is-entering', 'is-hover');
      n.el.classList.add('is-leaving');
      const timer = setTimeout(() => { leaving.delete(n.id); n.el.remove(); }, LEAVE_MS);
      leaving.set(n.id, { n, timer });
    }
    function flushLeaving() {
      for (const { n, timer } of leaving.values()) { clearTimeout(timer); n.el.remove(); }
      leaving.clear();
    }

    // ── Hierarchy ───────────────────────────────────────────────────────
    function rebuildTree() {
      for (const n of order) { n.parent = null; n.kids = []; }
      for (const n of order) {
        const pid = n.d.parent;
        if (pid == null) continue;
        const p = nodes.get(String(pid));
        if (p && p !== n) n.parent = p;
      }
      for (const n of order) {               // a cycle cuts the parent link of the node that closes it
        let p = n.parent, steps = 0;
        while (p && p !== n && steps++ <= order.length) p = p.parent;
        if (p === n) n.parent = null;
      }
      for (const n of order) if (n.parent) n.parent.kids.push(n);
      roots = order.filter((n) => !n.parent);
      const post = (n, r) => {
        n.root = r;
        let m = 1;
        for (const c of n.kids) m += post(c, r);
        n.mass = m;
        return m;
      };
      for (const r of roots) post(r, r);
    }
    function subtree(n, set) {
      const out = [];
      const st = [n];
      while (st.length) {
        const c = st.pop();
        if (set && !set.has(c)) continue;
        out.push(c);
        for (const k of c.kids) st.push(k);
      }
      return out;
    }

    // ── Edges ───────────────────────────────────────────────────────────
    function rebuildEdges(raw) {
      const out = [];
      const seen = new Set();
      activeCount = 0;
      for (const d of Array.isArray(raw) ? raw : []) {
        if (!d || typeof d !== 'object') continue;
        const s = nodes.get(String(d.source)), t = nodes.get(String(d.target));
        if (!s || !t || s === t) continue;
        const id = d.id != null ? String(d.id) : s.id + '>' + t.id;
        if (seen.has(id)) continue;
        seen.add(id);
        let e = edgeById.get(id);
        const wasActive = !!(e && e.active && e.s === s && e.t === t);
        if (!e) e = { id, acc: unit(id), vis: false, x0: 0, y0: 0, cx: 0, cy: 0, x1: 0, y1: 0, len: 1, sprite: null, mode: 0 };
        e.d = d; e.s = s; e.t = t;
        e.kind = d.kind;
        e.tree = t.parent === s || s.parent === t;
        e.active = !!d.active;
        e.rate = isNum(d.rate) && d.rate > 0 ? d.rate : 0;
        e.emit = e.active ? clamp(0.35 + 0.6 * Math.log10(1 + e.rate / 1000), 0.35, 3) : 0;
        const st = call(opts.edgeStyle, d, s.d, t.d) || {};
        e.cname = typeof st.color === 'string' && st.color.trim() ? st.color.trim() : '--other';
        e.width = isNum(st.width) && st.width > 0 ? st.width : 1.5;
        e.dash = Array.isArray(st.dash) && st.dash.length && st.dash.every((v) => isNum(v) && v >= 0) ? st.dash.slice() : null;
        e.color = resolve(e.cname);
        e.prime = e.active && !wasActive;
        if (e.active) activeCount++;
        out.push(e);
      }
      edges = out;
      edgeById = new Map(out.map((e) => [e.id, e]));
      let j = 0;
      for (let i = 0; i < pCount; i++) {
        const e = pE[i];
        if (edgeById.get(e.id) === e) { pE[j] = e; pT[j] = pT[i]; j++; }
      }
      for (let i = j; i < pCount; i++) pE[i] = undefined;
      pCount = j;
      groupsDirty = true;
    }

    // ── Layout: seeding ─────────────────────────────────────────────────
    const gapOf = (p, c) => (!p.parent && p.hub ? (c.orbit ? LY.gapOrbit : LY.gapHub) : LY.gapChild);
    const linkRest = (p, c, ux, uy) => rayR(p, ux, uy) + gapOf(p, c) + rayR(c, ux, uy);
    const kidsIn = (n, set) => n.kids.filter((k) => set.has(k));
    function halfAng(c, D) {
      return Math.asin(Math.min(1, (c.R + LY.pad / 2) / Math.max(1, D + c.off)));
    }
    /* Bottom-up: ring radius for n's children and n's subtree disc (R round a
       centre `off` px outward of n, since a fan sits on the far side). */
    function measure(n, set) {
      const kids = kidsIn(n, set);
      for (const c of kids) measure(c, set);
      const own = ownR(n);
      if (!kids.length) { n.R = own; n.off = 0; n.ringD = 0; return; }
      const full = !n.parent;
      const arc = full ? TAU : LY.arc;
      let D = 0;
      for (const c of kids) D = Math.max(D, Math.min(n.hw, n.hh) + gapOf(n, c) + Math.min(c.hw, c.hh));
      for (let it = 0; it < 80; it++) {
        let s = 0;
        for (const c of kids) s += 2 * halfAng(c, D);
        if (s <= arc) break;
        D *= 1.06;
      }
      n.ringD = D;
      let far = own;
      for (const c of kids) far = Math.max(far, D + c.off + c.R);
      if (full) { n.R = far; n.off = 0; } else { n.R = (far + own) / 2; n.off = (far - own) / 2; }
    }
    /* Orbit kinds sit next to the child their non-tree edge points into, on the
       side where the target hangs (its branch index within that child). */
    function arrangeRing(n, kids, set) {
      const reg = kids.filter((c) => !c.orbit), orb = kids.filter((c) => c.orbit);
      if (!orb.length || !reg.length) return kids;
      const before = new Map(reg.map((c) => [c, []])), after = new Map(reg.map((c) => [c, []]));
      const loose = [];
      for (const o of orb) {
        let at = null, side = after;
        for (const e of edges) {
          if (e.s !== o || e.tree) continue;
          let t = e.t, step = null;
          while (t && t.parent !== n) { step = t; t = t.parent; }
          if (!t || !before.has(t)) continue;
          at = t;
          if (step) {
            while (step.parent !== t) step = step.parent;
            const sib = kidsIn(t, set);
            if (sib.indexOf(step) < (sib.length - 1) / 2) side = before;
          }
          break;
        }
        if (at) side.get(at).push(o); else loose.push(o);
      }
      const out = [];
      for (const c of reg) out.push(...before.get(c), c, ...after.get(c));
      loose.forEach((o, j) => out.splice(Math.min(out.length, Math.round(((j + 0.5) / loose.length) * out.length) + j), 0, o));
      return out;
    }
    /* Top-down: put n's children round it, fanning towards `fan` (radians).
       A root's ring is measured, not estimated: after placing, each branch's
       real angular extent (every box corner seen from the root) is fed back
       and the ring re-packed, growing its radius while the branches do not
       fit, until neighbouring branches no longer overlap. */
    function place(n, fan, set) {
      const kids = kidsIn(n, set);
      if (!kids.length) return;
      if (n.parent) {
        const D = n.ringD, m = kids.length;
        const hs = kids.map((c) => halfAng(c, D));
        let tot = 0;
        for (const h of hs) tot += 2 * h;
        const slack = Math.min(LY.spread, Math.max(0, LY.arc - tot) / m);
        let a = fan - (tot + slack * m) / 2;
        for (let i = 0; i < m; i++) {
          const th = a + hs[i] + slack / 2;
          a = th + hs[i] + slack / 2;
          putChild(n, kids[i], th, D, set);
        }
        return;
      }
      const ring = arrangeRing(n, kids, set), m = ring.length;
      let D = n.ringD;
      let lo = ring.map((c) => halfAng(c, D)), hi = lo.slice();
      const ths = new Array(m);
      for (let it = 0; it < 14; it++) {
        const gapA = m > 1 ? LY.pad / Math.max(D, 1) : 0;
        let tot = 0;
        for (let i = 0; i < m; i++) tot += lo[i] + hi[i];
        const room = TAU - m * gapA, sc = tot > room ? room / tot : 1, slack = gapA + Math.max(0, room - tot) / m;
        let a = fan - lo[0] * sc;
        for (let i = 0; i < m; i++) {
          ths[i] = a + lo[i] * sc;
          a = ths[i] + hi[i] * sc + slack;
          putChild(n, ring[i], ths[i], D, set);
        }
        if (m < 2) break;
        for (let i = 0; i < m; i++) {                   // measured extents round each child's bearing
          let l = 0.02, h = 0.02;
          for (const q of subtree(ring[i], set)) {
            for (let cx = -1; cx <= 1; cx += 2) {
              for (let cy = -1; cy <= 1; cy += 2) {
                let r = Math.atan2(q.y + cy * q.hh - n.y, q.x + cx * q.hw - n.x) - ths[i];
                r = ((r + Math.PI) % TAU + TAU) % TAU - Math.PI;
                if (-r > l) l = -r;
                if (r > h) h = r;
              }
            }
          }
          lo[i] = l; hi[i] = h;
        }
        let clash = false, need = 0;
        for (let i = 0; i < m; i++) {
          const j = (i + 1) % m, next = ths[j] + (j === 0 ? TAU : 0);
          if (next - lo[j] - (ths[i] + hi[i]) < gapA * 0.5) clash = true;
          need += lo[i] + hi[i] + gapA;
        }
        if (!clash) break;
        if (need > TAU) D *= 1 + clamp((need - TAU) / TAU, 0.06, 0.5);
      }
      n.ringD = D;
    }
    function putChild(n, c, th, D, set) {
      const ux = Math.cos(th), uy = Math.sin(th);
      const d = Math.max(D, linkRest(n, c, ux, uy));
      c.x = n.x + ux * d;
      c.y = n.y + uy * d;
      place(c, th, set);
    }
    function boxOf(mem) {
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const m of mem) { x0 = Math.min(x0, m.x - m.hw); x1 = Math.max(x1, m.x + m.hw); y0 = Math.min(y0, m.y - m.hh); y1 = Math.max(y1, m.y + m.hh); }
      return { x0, y0, x1, y1 };
    }
    /* Clusters are laid out as boxes: each goes beside a placed one (12 aligned
       spots per neighbour), choosing the spot that keeps the zoom kOf(w, h)
       needed to fit everything highest, then the one nearest the centre. */
    const unionOf = (list) => list.reduce((u, p) => (u ? { x0: Math.min(u.x0, p.x0), y0: Math.min(u.y0, p.y0), x1: Math.max(u.x1, p.x1), y1: Math.max(u.y1, p.y1) } : { ...p }), null);
    function packPlan(boxes, placed0, kOf) {
      const placed = placed0.slice(), pos = [], G = LY.padCluster;
      let U = unionOf(placed);
      for (const b of boxes) {
        const w = b.x1 - b.x0, h = b.y1 - b.y0;
        let best = null;
        if (!U) best = { x: -w / 2, y: -h / 2 };
        else {
          const ucx = (U.x0 + U.x1) / 2, ucy = (U.y0 + U.y1) / 2;
          for (const p of placed) {
            const pcx = (p.x0 + p.x1) / 2, pcy = (p.y0 + p.y1) / 2;
            const spots = [
              [p.x1 + G, p.y0], [p.x1 + G, pcy - h / 2], [p.x1 + G, p.y1 - h],
              [p.x0 - G - w, p.y0], [p.x0 - G - w, pcy - h / 2], [p.x0 - G - w, p.y1 - h],
              [p.x0, p.y1 + G], [pcx - w / 2, p.y1 + G], [p.x1 - w, p.y1 + G],
              [p.x0, p.y0 - G - h], [pcx - w / 2, p.y0 - G - h], [p.x1 - w, p.y0 - G - h],
            ];
            for (const [x, y] of spots) {
              let free = true;
              for (const q of placed) {
                if (x < q.x1 + G - 0.5 && x + w > q.x0 - G + 0.5 && y < q.y1 + G - 0.5 && y + h > q.y0 - G + 0.5) { free = false; break; }
              }
              if (!free) continue;
              const k = kOf(Math.max(U.x1, x + w) - Math.min(U.x0, x), Math.max(U.y1, y + h) - Math.min(U.y0, y));
              const dc = Math.hypot(x + w / 2 - ucx, y + h / 2 - ucy);
              if (!best || k > best.k + 1e-9 || (k > best.k - 1e-9 && dc < best.dc - 1e-6)) best = { x, y, k, dc };
            }
          }
        }
        if (!best) best = { x: U.x1 + G, y: U.y0 };
        const nb = { x0: best.x, y0: best.y, x1: best.x + w, y1: best.y + h };
        pos.push(nb);
        placed.push(nb);
        U = unionOf([U || nb, nb]);
      }
      return { pos, U };
    }
    /* A cold layout tries the greedy packing under a few aspect biases (greedy
       is myopic: the fifth of six clusters decides the grid) and keeps the one
       that fits at the highest zoom (within 3%, the smaller area); a warm one
       packs new clusters round the pinned ones once. The zoom is the fit's
       (fitRects): the free rectangle, or with the avoid rect in it only the
       parts beside it, which are sure to keep every node out from under it. */
    function packBoxes(list, placed) {
      if (!list.length) return;
      const rs = W > 0 && H > 0 ? fitRects() : [{ x: 0, y: 0, w: 1440, h: 900 }];
      const use = rs.length > 1 ? rs.slice(1) : rs;
      const kOf = (w, h) => {
        let k = 0;
        for (const r of use) { const m = fitMargin(r); k = Math.max(k, Math.min(FIT_MAX, Math.max(1, r.w - 2 * m) / w, Math.max(1, r.h - 2 * m) / h)); }
        return k;
      };
      const boxes = list.map((c) => boxOf(c.mem));
      let best = null;
      for (const m of placed.length ? [1] : [1, 0.6, 0.8, 1.25, 1.6, 2.2]) {
        const plan = packPlan(boxes, placed, (w, h) => kOf(w / m, h));
        const k = kOf(plan.U.x1 - plan.U.x0, plan.U.y1 - plan.U.y0);
        const area = (plan.U.x1 - plan.U.x0) * (plan.U.y1 - plan.U.y0);
        if (!best || k > best.k * 1.03 || (k > best.k * 0.97 && area < best.area)) best = { plan, k, area };   // near-equal zoom: the tighter floor
      }
      list.forEach((c, i) => {
        const dx = best.plan.pos[i].x0 - boxes[i].x0, dy = best.plan.pos[i].y0 - boxes[i].y0;
        for (const m of c.mem) { m.x += dx; m.y += dy; m.sx += dx; m.sy += dy; }
      });
    }
    /* A cluster's ring is turned so its heaviest branch lies horizontal (the
       side its hashed angle already favours): floors are wider than tall. */
    function seedCluster(r, set) {
      measure(r, set);
      r.x = 0; r.y = 0;
      const ring0 = r.ringD;
      let fan = unit(r.id) * TAU;
      place(r, fan, set);
      let big = null;
      for (const c of kidsIn(r, set)) if (!big || c.mass > big.mass) big = c;
      if (big && big.mass > 1) {
        const th = Math.atan2(big.y - r.y, big.x - r.x);
        fan += (Math.cos(th) >= 0 ? 0 : Math.PI) - th;
        r.ringD = ring0;
        place(r, fan, set);
      }
      return { r, mem: subtree(r, set) };
    }
    /* Cold: every cluster is seeded round the origin and simulated on its own
       (clusters never interact); packClusters() then lays them out. */
    function seedCold() {
      const set = new Set(order);
      for (const r of roots) seedCluster(r, set);
      for (const n of order) n.placed = true;
    }
    function packClusters() {
      packBoxes(roots.map((r) => ({ r, mem: subtree(r) })), []);
    }
    function overlapsPlaced(mem, memSet, f = 1) {
      for (const m of mem) {
        for (const o of order) {
          if (!o.placed || memSet.has(o)) continue;
          const pad = (o.root === m.root ? LY.pad : LY.padCluster) * f;
          if (sepOf(o.x, o.y, o.hw, o.hh, o.circle, m.x, m.y, m.hw, m.hh, m.circle, pad)) return true;
        }
      }
      return false;
    }
    /* A new subtree under a surviving parent: aim at the widest free angular gap
       round the parent, then search angle and distance until nothing overlaps. */
    function placeBeside(r, set) {
      const P = r.parent;
      measure(r, set);
      const angs = [];
      for (const k of P.kids) if (k.placed) angs.push(Math.atan2(k.y - P.y, k.x - P.x));
      if (P.parent) angs.push(Math.atan2(P.parent.y - P.y, P.parent.x - P.x));
      let base = unit(r.id) * TAU;
      if (angs.length) {
        angs.sort((a, b) => a - b);
        let gap = -1;
        for (let i = 0; i < angs.length; i++) {
          const a = angs[i], b = i + 1 < angs.length ? angs[i + 1] : angs[0] + TAU;
          if (b - a > gap) { gap = b - a; base = (a + b) / 2; }
        }
      }
      base += (unit(r.id + '#') - 0.5) * 0.25;
      const cands = [];
      for (let s = 0; s <= 18; s++) {
        for (const sg of s ? [1, -1] : [1]) for (let di = 0; di < 8; di++) cands.push({ a: base + (sg * s * TAU) / 36, di, sc: di + s * 0.5 });
      }
      cands.sort((p, q) => p.sc - q.sc);
      let pickC = cands[0];
      for (const c of cands) {
        const ux = Math.cos(c.a), uy = Math.sin(c.a);
        const d = linkRest(P, r, ux, uy) + c.di * 44;
        r.x = P.x + ux * d;
        r.y = P.y + uy * d;
        place(r, c.a, set);
        const mem = subtree(r, set);
        if (!overlapsPlaced(mem, new Set(mem))) { pickC = null; break; }
      }
      if (pickC) {
        const ux = Math.cos(pickC.a), uy = Math.sin(pickC.a);
        const d = linkRest(P, r, ux, uy) + pickC.di * 44;
        r.x = P.x + ux * d; r.y = P.y + uy * d;
        place(r, pickC.a, set);
      }
      for (const m of subtree(r, set)) m.placed = true;
    }
    /* After a warm run, a new node the simulation squeezed against pinned ones
       moves (with its new subtree, rigidly) to the nearest free spot round its
       parent, or round where it stands when it has none. */
    function reseat(n, set) {
      const grp = subtree(n, set), gs = new Set(grp);
      if (!overlapsPlaced(grp, gs, 0.5)) return;
      const P = n.parent && !gs.has(n.parent) ? n.parent : null;
      const ox = P ? P.x : n.x, oy = P ? P.y : n.y;
      const offs = grp.map((m) => [m.x - n.x, m.y - n.y]);
      const base = P ? Math.atan2(n.y - oy, n.x - ox) : unit(n.id) * TAU;
      const cands = [];
      for (let s = 0; s <= 18; s++) {
        for (const sg of s ? [1, -1] : [1]) for (let di = P ? 0 : 1; di < 14; di++) cands.push({ a: base + (sg * s * TAU) / 36, di, sc: di + s * 0.5 });
      }
      cands.sort((p, q) => p.sc - q.sc);
      const x0 = n.x, y0 = n.y;
      for (const c of cands) {
        const ux = Math.cos(c.a), uy = Math.sin(c.a);
        const d = (P ? linkRest(P, n, ux, uy) : 0) + c.di * 44;
        grp.forEach((m, i) => { m.x = ox + ux * d + offs[i][0]; m.y = oy + uy * d + offs[i][1]; });
        if (!overlapsPlaced(grp, gs)) return;
      }
      grp.forEach((m, i) => { m.x = x0 + offs[i][0]; m.y = y0 + offs[i][1]; });
    }
    function seedWarm(added) {
      const set = new Set(added);
      for (const n of order) n.placed = !set.has(n);
      const tops = added.filter((n) => !n.parent || !set.has(n.parent));
      for (const r of tops) if (r.parent) placeBeside(r, set);
      const fresh = tops.filter((r) => !r.parent);
      if (!fresh.length) return;
      const placed = [];
      for (const rt of roots) {
        const mem = subtree(rt).filter((m) => m.placed);
        if (mem.length) placed.push(boxOf(mem));
      }
      const cl = fresh.map((r) => seedCluster(r, set));
      packBoxes(cl, placed);
      for (const c of cl) for (const m of c.mem) m.placed = true;
    }

    // ── Layout: simulation ──────────────────────────────────────────────
    /* Runs to completion; nodes with fixed=true never move. In a cold run the
       clusters are independent; in a warm run new nodes also keep clear of
       other clusters' (pinned) nodes. Returns the ticks run. */
    function runSim(cold) {
      const list = order, N = list.length;
      if (!N) return 0;
      const X = new Float64Array(N), Y = new Float64Array(N), VX = new Float64Array(N), VY = new Float64Array(N);
      const HW = new Float64Array(N), HH = new Float64Array(N), M = new Float64Array(N), GAP = new Float64Array(N);
      const CI = new Uint8Array(N), FX = new Uint8Array(N), ORB = new Uint8Array(N);
      const PAR = new Int32Array(N), CL = new Int32Array(N);
      const rootIdx = new Map(roots.map((r, i) => [r, i]));
      for (let i = 0; i < N; i++) {
        const n = list[i];
        X[i] = n.x; Y[i] = n.y; HW[i] = n.hw; HH[i] = n.hh; M[i] = n.mass;
        CI[i] = n.circle ? 1 : 0; FX[i] = n.fixed ? 1 : 0;
        PAR[i] = n.parent ? n.parent.idx : -1;
        ORB[i] = n.orbit && n.parent && !n.parent.parent && n.parent.hub ? 1 : 0;
        GAP[i] = !n.parent ? 0 : ORB[i] ? n.parent.hw + LY.gapOrbit + ownR(n) : gapOf(n.parent, n);
        CL[i] = rootIdx.get(n.root) || 0;
      }
      const springs = [];
      for (const e of edges) if (!e.tree && e.s.root === e.t.root && !(e.s.fixed && e.t.fixed)) springs.push(e.s.idx, e.t.idx);
      const rayI = (i, ux, uy) => {
        if (CI[i]) return HW[i];
        const ax = Math.abs(ux), ay = Math.abs(uy);
        const tx = ax > 1e-9 ? HW[i] / ax : Infinity, ty = ay > 1e-9 ? HH[i] / ay : Infinity;
        return tx < ty ? tx : ty;
      };
      let alpha = cold ? LY.alphaCold : LY.alphaWarm;
      const ticks = cold ? LY.ticksCold : LY.ticksWarm;
      const decay = 1 - Math.pow(LY.alphaMin / alpha, 1 / ticks);
      const cut2 = LY.chargeCut * LY.chargeCut;
      let snap = null, tick = 0;
      for (; tick < ticks; tick++) {
        alpha *= 1 - decay;
        for (let c = 0; c < N; c++) {                              // parent links
          const p = PAR[c];
          if (p < 0 || (FX[c] && FX[p])) continue;
          let dx = X[c] + VX[c] - X[p] - VX[p], dy = Y[c] + VY[c] - Y[p] - VY[p];
          let d = Math.sqrt(dx * dx + dy * dy);
          if (d < 1e-6) { dx = 1e-3; dy = 0; d = 1e-3; }
          const ux = dx / d, uy = dy / d;
          const rest = ORB[c] ? GAP[c] : rayI(p, ux, uy) + GAP[c] + rayI(c, ux, uy);   // orbit: a true circle
          const f = ((d - rest) / d) * alpha * (ORB[c] ? LY.orbitK : LY.linkK);
          const b = FX[p] ? 1 : FX[c] ? 0 : M[p] / (M[p] + M[c]);
          VX[c] -= dx * f * b; VY[c] -= dy * f * b;
          VX[p] += dx * f * (1 - b); VY[p] += dy * f * (1 - b);
        }
        for (let s = 0; s < springs.length; s += 2) {               // weak non-tree springs (runs)
          const i = springs[s], j = springs[s + 1];
          const dx = X[j] + VX[j] - X[i] - VX[i], dy = Y[j] + VY[j] - Y[i] - VY[i];
          const d = Math.sqrt(dx * dx + dy * dy) || 1e-3;
          const rest = rayI(i, dx / d, dy / d) + rayI(j, dx / d, dy / d) + LY.gapChild * 2;
          if (d <= rest) continue;
          const f = ((d - rest) / d) * alpha * LY.springK;
          const b = FX[i] ? 1 : FX[j] ? 0 : M[i] / (M[i] + M[j]);
          VX[j] -= dx * f * b; VY[j] -= dy * f * b;
          VX[i] += dx * f * (1 - b); VY[i] += dy * f * (1 - b);
        }
        for (let i = 0; i < N; i++) {                               // charge + collision
          const xi = X[i] + VX[i], yi = Y[i] + VY[i], hwi = HW[i], hhi = HH[i], ci = CI[i], fi = FX[i], cli = CL[i], mi = M[i];
          for (let j = i + 1; j < N; j++) {
            if (fi && FX[j]) continue;
            const same = cli === CL[j];
            if (!same && cold) continue;
            const dx = X[j] + VX[j] - xi;
            const reach = same ? LY.chargeCut : hwi + HW[j] + LY.padCluster;
            if (dx > reach || dx < -reach) continue;
            const dy = Y[j] + VY[j] - yi;
            if (dy > reach || dy < -reach) continue;
            const wa = fi ? 0 : FX[j] ? 1 : M[j] / (mi + M[j]), wb = 1 - wa;
            if (same) {
              const d2 = dx * dx + dy * dy;
              if (d2 < cut2) {
                const f = (LY.charge * alpha) / Math.max(d2, 900);
                VX[i] -= dx * f * wa; VY[i] -= dy * f * wa;
                VX[j] += dx * f * wb; VY[j] += dy * f * wb;
              }
            }
            if (!sepOf(xi, yi, hwi, hhi, ci, xi + dx, yi + dy, HW[j], HH[j], CI[j], same ? LY.pad : LY.padCluster)) continue;
            const sx = SEP[0] * LY.coll, sy = SEP[1] * LY.coll;
            VX[i] -= sx * wa; VY[i] -= sy * wa;
            VX[j] += sx * wb; VY[j] += sy * wb;
          }
        }
        for (let c = 0; c < N; c++) {                               // fans point away from the grandparent
          const p = PAR[c];
          if (p < 0 || FX[c]) continue;
          const g = PAR[p];
          if (g < 0) continue;
          let ux = X[p] - X[g], uy = Y[p] - Y[g];
          const ul = Math.sqrt(ux * ux + uy * uy);
          if (ul < 1e-6) continue;
          ux /= ul; uy /= ul;
          const ahead = (X[c] - X[p]) * ux + (Y[c] - Y[p]) * uy, want = GAP[c];
          if (ahead >= want) continue;
          VX[c] += ux * (want - ahead) * LY.outK * alpha;
          VY[c] += uy * (want - ahead) * LY.outK * alpha;
        }
        let maxV = 0;
        for (let i = 0; i < N; i++) {
          if (FX[i]) { VX[i] = 0; VY[i] = 0; continue; }
          let vx = VX[i] * LY.vKeep, vy = VY[i] * LY.vKeep;
          const v = Math.sqrt(vx * vx + vy * vy);
          if (v > LY.vMax) { vx *= LY.vMax / v; vy *= LY.vMax / v; }
          VX[i] = vx; VY[i] = vy;
          X[i] += vx; Y[i] += vy;
          if (v > maxV) maxV = v;
        }
        if (cold && !snap && alpha < LY.tailAlpha) snap = [X.slice(), Y.slice()];
        if (tick > 30 && maxV < 0.01) { tick++; break; }
      }
      // Position pass: nothing may overlap; the lighter (or unpinned) node yields.
      for (let it = 0; it < 80; it++) {
        let moved = false;
        for (let i = 0; i < N; i++) {
          for (let j = i + 1; j < N; j++) {
            if (FX[i] && FX[j]) continue;
            const same = CL[i] === CL[j];
            if (!same && cold) continue;
            if (!sepOf(X[i], Y[i], HW[i], HH[i], CI[i], X[j], Y[j], HW[j], HH[j], CI[j], (same ? LY.pad : LY.padCluster) * 0.6)) continue;
            moved = true;
            const sx = SEP[0] * 1.02, sy = SEP[1] * 1.02;
            const wa = FX[i] ? 0 : FX[j] ? 1 : M[j] / (M[i] + M[j]), wb = 1 - wa;
            X[i] -= sx * wa; Y[i] -= sy * wa; X[j] += sx * wb; Y[j] += sy * wb;
          }
        }
        if (!moved) break;
      }
      for (let i = 0; i < N; i++) {
        const n = list[i];
        n.x = X[i]; n.y = Y[i];
        if (snap) { n.sx = snap[0][i]; n.sy = snap[1][i]; } else { n.sx = n.x; n.sy = n.y; }
      }
      simAlpha = alpha;
      return tick;
    }

    function layoutStructural(added, t0) {
      settleT0 = t0;
      const addSet = new Set(added);
      const cold = order.every((n) => addSet.has(n));
      for (const n of order) n.fixed = !cold && !addSet.has(n);
      if (cold) seedCold();
      else if (added.length) seedWarm(added);
      simTicks = cold || added.length ? runSim(cold) : 0;
      if (cold) packClusters();
      else if (added.length) {
        for (const n of order) n.placed = true;
        for (const n of added) reseat(n, addSet);
      }
      for (const n of order) n.fixed = false;
      for (const n of order) { n.fx = n.x; n.fy = n.y; }
      const animate = !reduced && !doc.hidden && !paused && W > 0 && H > 0;
      const list = [];
      if (animate) {
        if (cold) {
          for (const n of order) { n.x = n.sx; n.y = n.sy; list.push(n); }
        } else {
          for (const n of added) {
            const p = n.parent && !addSet.has(n.parent) ? n.parent : null;
            if (!p) continue;
            n.sx = p.fx + (n.fx - p.fx) * 0.35;
            n.sy = p.fy + (n.fy - p.fy) * 0.35;
            n.x = n.sx; n.y = n.sy;
            list.push(n);
          }
          // descendants of a gliding subtree root glide with it
          for (const n of added) {
            if (list.includes(n)) continue;
            let a = n.parent;
            while (a && addSet.has(a) && !list.includes(a)) a = a.parent;
            if (a && list.includes(a)) {
              n.sx = n.fx + (a.sx - a.fx); n.sy = n.fy + (a.sy - a.fy);
              n.x = n.sx; n.y = n.sy;
              list.push(n);
            }
          }
        }
      }
      if (list.length) {
        tween = { t0: now(), dur: cold ? TAIL_MS : SPAWN_MS, list };
        settled = false;
      } else {
        tween = null;
        settled = true;
        settleMs = now() - t0;
      }
      posDirty = true;
    }
    function finishTween() {
      if (!tween) return;
      for (const n of tween.list) { n.x = n.fx; n.y = n.fy; }
      tween = null;
      settled = true;
      settleMs = now() - settleT0;
      posDirty = true;
    }
    function stepTween(t) {
      const p = Math.min(1, (t - tween.t0) / tween.dur), e = easeOut(p);
      for (const n of tween.list) { n.x = n.sx + (n.fx - n.sx) * e; n.y = n.sy + (n.fy - n.sy) * e; }
      posDirty = true;
      if (p >= 1) finishTween();
    }

    // ── setData ─────────────────────────────────────────────────────────
    function applyData(data) {
      const t0 = now();
      readTheme();
      const raw = data && Array.isArray(data.nodes) ? data.nodes : [];
      const next = new Map();
      const ids = [];
      for (const d of raw) {
        if (!d || typeof d !== 'object' || d.id == null) continue;
        const id = String(d.id);
        if (next.has(id)) continue;
        next.set(id, d);
        ids.push(id);
      }
      let structural = false;
      for (const id of nodes.keys()) if (!next.has(id)) { structural = true; break; }
      if (!structural) for (const id of ids) if (!nodes.has(id)) { structural = true; break; }
      if (structural) finishTween();
      const focusIn = doc.activeElement && world.contains(doc.activeElement) ? doc.activeElement.closest('.gx-node') : null;
      let refocus = null;
      for (const [id, n] of nodes) {
        if (next.has(id)) continue;
        if (focusIn === n.el) {
          let p = n.parent;
          while (p && !next.has(p.id)) p = p.parent;
          refocus = p ? p.id : '';
        }
        nodes.delete(id);
        leave(n);
      }
      const added = [];
      const list = [];
      for (const id of ids) {
        const d = next.get(id);
        let n = nodes.get(id);
        if (!n) {
          const lv = leaving.get(id);
          if (lv) {                               // back before its exit finished: keep it where it was
            clearTimeout(lv.timer);
            leaving.delete(id);
            n = lv.n;
            n.d = d;
            n.el.classList.remove('is-leaving');
            n.cls = null;
          } else {
            n = makeNode(id, d);
            added.push(n);
            if (!reduced) { n.enter = 2; entering.add(n); }
          }
          nodes.set(id, n);
        } else {
          n.d = d;
        }
        const kind = String(d.kind == null ? '' : d.kind);
        if (kind !== n.kind) { n.kind = kind; applySpec(n); }
        list.push(n);
      }
      order = list;
      // New elements go in at their place in data order (the view sends tree order), so the DOM's
      // reading order follows it. An element already in the world is never moved: that would blur it.
      for (let i = order.length - 1, after = null; i >= 0; i--) {
        const n = order[i];
        if (!n.el.parentNode) world.insertBefore(n.el, after);
        after = n.el;
      }
      order.forEach((n, i) => { n.idx = i; });
      rebuildTree();
      rebuildEdges(data && data.edges);
      if (structural) layoutStructural(added, t0);
      for (const n of order) syncNode(n, false);
      if (selected && !nodes.has(selected)) { selected = null; host.removeAttribute('data-sel'); }
      computeRelated();
      applyClasses();
      updateTabindex();
      writeTransforms();
      if (refocus !== null) {
        const t = (refocus && nodes.get(refocus)) || order[0];
        if (t) { rover = t.id; updateTabindex(); t.el.focus({ preventScroll: true }); }
      }
      if (structural && order.length) {
        if (!fitted || pendingFit) autoFrame(false, false);
        else if (!camUser && (anyOutside() || hitsAvoid(camTarget(), order))) autoFrame(true, false);
      }
      dirty = true;
      schedule();
    }

    // ── Selection ───────────────────────────────────────────────────────
    function computeRelated() {
      related = null;
      const s = selected && nodes.get(selected);
      if (!s) return;
      related = new Set();
      for (let p = s.parent; p; p = p.parent) related.add(p.id);
      const st = [...s.kids];
      while (st.length) { const c = st.pop(); related.add(c.id); for (const k of c.kids) st.push(k); }
      for (const e of edges) { if (e.s === s) related.add(e.t.id); else if (e.t === s) related.add(e.s.id); }
      related.delete(s.id);
      groupsDirty = true;
    }
    function select(id, o) {
      if (destroyed) return;
      o = o || {};
      const n = id == null ? null : nodes.get(String(id));
      const nid = n ? n.id : null;
      if (nid !== selected) {
        const was = selected && nodes.get(selected);
        if (was) was.el.removeAttribute('aria-current');
        selected = nid;
        if (n) { n.el.setAttribute('aria-current', 'true'); host.setAttribute('data-sel', nid); rover = nid; } else host.removeAttribute('data-sel');
        computeRelated();
        groupsDirty = true;
        applyClasses();
        updateTabindex();
        syncInv();
        dirty = true;
        schedule();
      }
      if (n && o.center) center(n.id, { animate: true });
      if (n && o.focus) focusNode(n.id);
    }
    function pick(id) {
      if (typeof opts.onSelect === 'function') call(opts.onSelect, id);
      else select(id);
    }

    // ── Camera ──────────────────────────────────────────────────────────
    function freeRect() {
      const x = clamp(insets.left, 0, W), y = clamp(insets.top, 0, H);
      return { x, y, w: Math.max(1, W - x - clamp(insets.right, 0, W)), h: Math.max(1, H - y - clamp(insets.bottom, 0, H)) };
    }
    /* Where a fit, a framing or the cluster packing may put nodes. The free rect
       first; when the avoid rect (insets.avoid: the zoom buttons, which float
       over the floor) cuts into it, also the parts of the free rect beside it
       (left of, above, right of, below) that are big enough to use. A camera on
       the free rect itself must keep every node box clear of the avoid rect
       (hitsAvoid); one on a part beside it is clear by construction. */
    function fitRects() {
      const fr = freeRect(), a = insets.avoid;
      if (!a) return [fr];
      const x1 = fr.x + fr.w, y1 = fr.y + fr.h, ax1 = a.x + a.w, ay1 = a.y + a.h;
      if (ax1 <= fr.x || a.x >= x1 || ay1 <= fr.y || a.y >= y1) return [fr];
      const out = [fr];
      // ax, ay: the side the avoid rect is on, where a floor too big for the part must not spill
      const add = (x, y, w, h, ax, ay) => { if (w >= 120 && h >= 90) out.push({ x, y, w, h, ax, ay }); };
      add(fr.x, fr.y, a.x - AVOID_PAD - fr.x, fr.h, 1, 0);
      add(fr.x, fr.y, fr.w, a.y - AVOID_PAD - fr.y, 0, 1);
      add(ax1 + AVOID_PAD, fr.y, x1 - ax1 - AVOID_PAD, fr.h, -1, 0);
      add(fr.x, ay1 + AVOID_PAD, fr.w, y1 - ay1 - AVOID_PAD, 0, -1);
      return out;
    }
    const fitMargin = (r) => Math.min(FIT_MARGIN, r.w * 0.06, r.h * 0.06);
    /* The camera that fits world box b into rect r (raw: the zoom before the
       clamp). Centred; a floor too big for r even at FIT_MIN is held against
       the side r.ax / r.ay names (towards the avoid rect), so it spills away
       from it. */
    function camFor(b, r) {
      const m = fitMargin(r), bw = Math.max(1, b.x1 - b.x0), bh = Math.max(1, b.y1 - b.y0);
      const raw = Math.min((r.w - 2 * m) / bw, (r.h - 2 * m) / bh), k = clamp(raw, FIT_MIN, FIT_MAX);
      const at = (lo, len, a0, a1, side) => ((a1 - a0) * k > len - 2 * m && side ? (side > 0 ? lo + len - m - a1 * k : lo + m - a0 * k) : lo + len / 2 - ((a0 + a1) / 2) * k);
      return { x: at(r.x, r.w, b.x0, b.x1, r.ax), y: at(r.y, r.h, b.y0, b.y1, r.ay), k, raw };
    }
    /* Does any node of `list` (at its final position) land on the avoid rect under camera c? */
    function hitsAvoid(c, list) {
      const a = insets.avoid;
      if (!a) return false;
      const ax0 = a.x - AVOID_PAD, ay0 = a.y - AVOID_PAD, ax1 = a.x + a.w + AVOID_PAD, ay1 = a.y + a.h + AVOID_PAD;
      for (const n of list) {
        if ((n.fx + n.hw) * c.k + c.x > ax0 && (n.fx - n.hw) * c.k + c.x < ax1 && (n.fy + n.hh) * c.k + c.y > ay0 && (n.fy - n.hh) * c.k + c.y < ay1) return true;
      }
      return false;
    }
    const camTarget = () => (camAnim ? camAnim.to : cam);
    function setLod() {
      const l = cam.k >= LOD_NEAR ? 'near' : cam.k >= LOD_MID ? 'mid' : 'far';
      if (l !== lod) { lod = l; host.setAttribute('data-lod', l); }
    }
    function setCam(x, y, k) {
      if (!isNum(x) || !isNum(y) || !isNum(k)) return;
      cam.x = x; cam.y = y; cam.k = clamp(k, K_MIN, K_MAX);
      world.style.transform = 'translate(' + cam.x.toFixed(2) + 'px, ' + cam.y.toFixed(2) + 'px) scale(' + cam.k.toFixed(5) + ')';
      setLod();
      syncInv();
      dirty = true;
      schedule();
    }
    /* The focus outline and the selected ring are drawn inside the scaled world, so CSS sizes
       them by --gx-inv, about 1/k below zoom 1 (never thinner on screen than at zoom 1). It is
       ceil(2/k)/2, so a 2px outline is a whole number of px: Chrome floors outline and border
       widths to whole pixels, which would take 2/k below 2 on screen. The property sits only
       on the focused and the selected node: rewriting it on the host every camera frame would
       restyle all 300 faces (the harness measures ~30x the cost); on two elements it is free. */
    function syncInv() {
      const v = cam.k < 1 ? String(Math.ceil(2 / cam.k - 1e-6) / 2) : '1';
      const want = [];
      const s = selected && nodes.get(selected);
      if (s) want.push(s.el);
      const a = doc.activeElement;
      if (a && a.parentNode === world && !want.includes(a)) want.push(a);
      for (const el of invEls) if (!want.includes(el)) { el.style.removeProperty('--gx-inv'); el.__gxInv = null; invEls.delete(el); }
      for (const el of want) if (el.__gxInv !== v) { el.style.setProperty('--gx-inv', v); el.__gxInv = v; invEls.add(el); }
    }
    function animateCam(x, y, k, animate) {
      k = clamp(k, K_MIN, K_MAX);
      const near = (c) => Math.abs(c.x - x) < 0.5 && Math.abs(c.y - y) < 0.5 && Math.abs(c.k - k) < 1e-4;
      if (camAnim ? near(camAnim.to) : near(cam)) return;     // already there, or already on the way
      if (!animate || reduced || doc.hidden || paused || !W || !H) { camAnim = null; setCam(x, y, k); return; }
      camAnim = { t0: now(), from: { x: cam.x, y: cam.y, k: cam.k }, to: { x, y, k } };
      schedule();
    }
    /* Interpolate the world point under the free centre and log(k), so a zoom
       stays anchored and a pan is a straight line. */
    function stepCam(t) {
      const a = camAnim, p = Math.min(1, (t - a.t0) / CAM_MS), e = easeInOut(p);
      if (p >= 1) { camAnim = null; setCam(a.to.x, a.to.y, a.to.k); return; }
      const fr = freeRect(), fx = fr.x + fr.w / 2, fy = fr.y + fr.h / 2;
      const c0x = (fx - a.from.x) / a.from.k, c0y = (fy - a.from.y) / a.from.k;
      const c1x = (fx - a.to.x) / a.to.k, c1y = (fy - a.to.y) / a.to.k;
      const k = Math.exp(Math.log(a.from.k) + (Math.log(a.to.k) - Math.log(a.from.k)) * e);
      setCam(fx - (c0x + (c1x - c0x) * e) * k, fy - (c0y + (c1y - c0y) * e) * k, k);
    }
    function bounds(list) {
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const n of list || order) { x0 = Math.min(x0, n.fx - n.hw); x1 = Math.max(x1, n.fx + n.hw); y0 = Math.min(y0, n.fy - n.hh); y1 = Math.max(y1, n.fy + n.hh); }
      return { x0, y0, x1, y1 };
    }
    /* The fit: everything inside the free rect, centred, unless a node would
       then land on the avoid rect; then the part beside it that fits the floor
       at the highest zoom (slack on one axis costs no zoom at all: the floor
       just moves off the buttons). Clamped at FIT_MIN, where zooms tie, the
       part that leaves more of the floor in view wins. */
    function fitCam() {
      const b = bounds(), rs = fitRects();
      const c0 = camFor(b, rs[0]);
      if (rs.length === 1 || !hitsAvoid(c0, order)) return c0;
      const fr = rs[0];
      const seen = (c) => Math.max(0, Math.min(fr.x + fr.w, b.x1 * c.k + c.x) - Math.max(fr.x, b.x0 * c.k + c.x)) * Math.max(0, Math.min(fr.y + fr.h, b.y1 * c.k + c.y) - Math.max(fr.y, b.y0 * c.k + c.y));
      let best = null;
      for (let i = 1; i < rs.length; i++) {
        const c = camFor(b, rs[i]);
        c.seen = seen(c);
        if (!best || c.k > best.k + 1e-6 || (c.k > best.k - 1e-6 && (c.seen > best.seen + 1 || (c.seen > best.seen - 1 && c.raw > best.raw)))) best = c;
      }
      return best;
    }
    function fitNow(animate) {
      if (!order.length) return;
      if (!W || !H) { pendingFit = true; return; }
      pendingFit = false;
      fitted = true;
      const c = fitCam();
      animateCam(c.x, c.y, c.k, animate);
    }
    /* The camera when the free rect changes or a node is picked (reframe()),
       the host resizes, or a structural poll lands. keep is the node the view
       keeps in sight (the inspector's). The rule:
       - The user has not moved the camera since the last fit (camUser false):
         fit again into the new free rect, unless keep is set and that fit
         would not hold everything (held at FIT_MIN) or would drop below the mid
         level of detail (faces stop being readable) and below the current
         zoom; then frame keep instead.
       - The user has moved it: an explicit reframe() frames keep; a resize or
         a poll leaves the camera alone.
       Framing keep pans, zooming out only when they cannot fit otherwise, so
       that keep, its parent and its children are inside the free rect and
       clear of the avoid rect. None of this counts as the user moving the
       camera: when the inspector closes (reframe(null)), a floor that was
       fitted is fitted again. */
    function autoFrame(animate, explicit) {
      if (!order.length) return;
      if (!W || !H) { pendingFit = true; return; }
      const n = keep ? nodes.get(keep) : null;
      if (!camUser || !fitted || pendingFit) {
        pendingFit = false;
        fitted = true;
        const c = fitCam();
        const fits = c.raw >= c.k - 1e-6;                    // not held up at FIT_MIN with the floor spilling out
        if (!n || (fits && (c.k >= LOD_MID || c.k >= camTarget().k - 1e-3))) { animateCam(c.x, c.y, c.k, animate); return; }
        frameAround(n, animate);
        return;
      }
      if (n && explicit) frameAround(n, animate);
    }
    const shiftInto = (a0, a1, lo, hi, M) => {
      if (a1 - a0 > hi - lo - 2 * M) return (lo + hi) / 2 - (a0 + a1) / 2;
      if (a0 < lo + M) return lo + M - a0;
      if (a1 > hi - M) return hi - M - a1;
      return 0;
    };
    function frameAround(n, animate) {
      const list = [n];
      if (n.parent) list.push(n.parent);
      for (const c of n.kids) list.push(c);
      const b = bounds(list), c = camTarget(), rs = fitRects();
      const ax = rs[0].x + rs[0].w / 2, ay = rs[0].y + rs[0].h / 2;
      let best = null;
      rs.forEach((r, i) => {
        const M = Math.min(FRAME_MARGIN, r.w * 0.08, r.h * 0.08);   // a phone's sliver above the sheet keeps its room for nodes
        const need = Math.min((r.w - 2 * M) / Math.max(1, b.x1 - b.x0), (r.h - 2 * M) / Math.max(1, b.y1 - b.y0));
        const k = clamp(Math.min(c.k, need), K_MIN, K_MAX);
        let x = ax - (ax - c.x) * (k / c.k), y = ay - (ay - c.y) * (k / c.k);   // zoomed about the free centre
        x += shiftInto(b.x0 * k + x, b.x1 * k + x, r.x, r.x + r.w, M);
        y += shiftInto(b.y0 * k + y, b.y1 * k + y, r.y, r.y + r.h, M);
        if (i === 0 && rs.length > 1 && hitsAvoid({ x, y, k }, list)) return;
        const move = Math.abs(x - c.x) + Math.abs(y - c.y);
        if (!best || k > best.k + 1e-6 || (k > best.k - 1e-6 && move < best.move)) best = { x, y, k, move };
      });
      if (best) animateCam(best.x, best.y, best.k, animate);
    }
    function anyOutside() {
      const fr = freeRect(), c = camTarget();
      for (const n of order) {
        const x0 = (n.fx - n.hw) * c.k + c.x, x1 = (n.fx + n.hw) * c.k + c.x, y0 = (n.fy - n.hh) * c.k + c.y, y1 = (n.fy + n.hh) * c.k + c.y;
        if (x0 < fr.x || y0 < fr.y || x1 > fr.x + fr.w || y1 > fr.y + fr.h) return true;
      }
      return false;
    }
    function center(id, o) {
      if (destroyed) return;
      o = o || {};
      const n = nodes.get(String(id));
      if (!n || !W || !H) return;
      const fr = freeRect(), c = camTarget();
      const k = isNum(o.zoom) ? clamp(o.zoom, K_MIN, K_MAX) : c.k;
      camUser = true;
      animateCam(fr.x + fr.w / 2 - n.fx * k, fr.y + fr.h / 2 - n.fy * k, k, o.animate !== false);
    }
    function ensureVisible(n) {
      if (!W || !H) return;
      const fr = freeRect(), c = camTarget(), M = FRAME_MARGIN;
      const x0 = (n.fx - n.hw) * c.k + c.x, x1 = (n.fx + n.hw) * c.k + c.x, y0 = (n.fy - n.hh) * c.k + c.y, y1 = (n.fy + n.hh) * c.k + c.y;
      const dx = shiftInto(x0, x1, fr.x, fr.x + fr.w, M), dy = shiftInto(y0, y1, fr.y, fr.y + fr.h, M);
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) return;
      camUser = true;
      animateCam(c.x + dx, c.y + dy, c.k, true);
    }
    function focusNode(id) {
      if (destroyed) return;
      const n = nodes.get(String(id));
      if (!n) return;
      rover = n.id;
      updateTabindex();
      if (doc.activeElement !== n.el) n.el.focus({ preventScroll: true });
      ensureVisible(n);
    }
    function zoomBy(f, o) {
      if (destroyed || !isNum(f) || f <= 0 || !W || !H) return;
      o = o || {};
      const c = camTarget(), k = clamp(c.k * f, K_MIN, K_MAX), fr = freeRect();
      const cx = fr.x + fr.w / 2, cy = fr.y + fr.h / 2;
      camUser = true;
      animateCam(cx - (cx - c.x) * (k / c.k), cy - (cy - c.y) * (k / c.k), k, o.animate !== false);
    }

    // ── Drawing ─────────────────────────────────────────────────────────
    const MODE_DIM = 0, MODE_IDLE = 1, MODE_ACTIVE = 2;
    /* Edge curve: the visible stretch of the centre line between the two shapes,
       bowed gently to one side; each end is then re-clipped on the ray from its
       node's centre towards the control point, so the curve leaves the box
       square-on. Hidden when the shapes touch. */
    function geom(e) {
      const s = e.s, t = e.t;
      const dx = t.x - s.x, dy = t.y - s.y, d = Math.sqrt(dx * dx + dy * dy);
      if (d < 1) { e.vis = false; return; }
      const ux = dx / d, uy = dy / d;
      const a0 = rayR(s, ux, uy) + EDGE_PAD, a1 = d - rayR(t, ux, uy) - EDGE_PAD;
      if (a1 - a0 < 4) { e.vis = false; return; }
      const bow = Math.min(30, (a1 - a0) * 0.16), mid = (a0 + a1) / 2;
      const mx = s.x + ux * mid + uy * bow, my = s.y + uy * mid - ux * bow;
      let x0 = s.x + ux * a0, y0 = s.y + uy * a0, x1 = s.x + ux * a1, y1 = s.y + uy * a1;
      let px = mx - s.x, py = my - s.y, pl = Math.sqrt(px * px + py * py);
      if (pl > 1) { const r = rayR(s, px / pl, py / pl) + EDGE_PAD; if (r < pl - 2) { x0 = s.x + (px / pl) * r; y0 = s.y + (py / pl) * r; } }
      px = mx - t.x; py = my - t.y; pl = Math.sqrt(px * px + py * py);
      if (pl > 1) { const r = rayR(t, px / pl, py / pl) + EDGE_PAD; if (r < pl - 2) { x1 = t.x + (px / pl) * r; y1 = t.y + (py / pl) * r; } }
      e.x0 = x0; e.y0 = y0; e.x1 = x1; e.y1 = y1; e.cx = mx; e.cy = my;
      const ch = Math.hypot(x1 - x0, y1 - y0);
      e.len = Math.max(1, (2 * ch + Math.hypot(mx - x0, my - y0) + Math.hypot(x1 - mx, y1 - my)) / 3);
      e.vis = true;
    }
    function buildGroups() {
      const map = new Map();
      for (const e of edges) {
        const lit = !related || ((e.s.id === selected || related.has(e.s.id)) && (e.t.id === selected || related.has(e.t.id)));
        e.mode = !lit ? MODE_DIM : e.active ? MODE_ACTIVE : MODE_IDLE;
        e.sprite = sprite(e.color);
        const key = e.mode + '|' + e.color + '|' + e.width + '|' + (e.dash ? e.dash.join(',') : '');
        let g = map.get(key);
        if (!g) { g = { mode: e.mode, color: e.color, width: e.width, dash: e.dash, edges: [] }; map.set(key, g); }
        g.edges.push(e);
      }
      groups = [...map.values()].sort((a, b) => a.mode - b.mode);
      groupsDirty = false;
    }
    function drawGrid() {
      const pw = lod === 'mid' ? GRID * 2 : GRID, ps = pw * cam.k;
      if (ps < 8) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.globalAlpha = 1;
      ctx.fillStyle = tok.grid;
      const s = clamp(1.3 * Math.sqrt(cam.k), 1, 2);
      const ox = ((cam.x % ps) + ps) % ps, oy = ((cam.y % ps) + ps) % ps;
      ctx.beginPath();
      for (let y = oy; y < H; y += ps) for (let x = ox; x < W; x += ps) ctx.rect(x - s / 2, y - s / 2, s, s);
      ctx.fill();
    }
    function draw() {
      dirty = false;
      if (!ctx || !W || !H) return;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.globalCompositeOperation = 'source-over';
      ctx.globalAlpha = 1;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (tok.grid && lod !== 'far') drawGrid();
      const k = cam.k;
      ctx.setTransform(dpr * k, 0, 0, dpr * k, dpr * cam.x, dpr * cam.y);
      const lw = Math.max(1, 0.7 / k);
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      for (const g of groups) {
        ctx.beginPath();
        let any = false;
        for (const e of g.edges) {
          if (!e.vis) continue;
          ctx.moveTo(e.x0, e.y0);
          ctx.quadraticCurveTo(e.cx, e.cy, e.x1, e.y1);
          any = true;
        }
        if (!any) continue;
        ctx.strokeStyle = g.color;
        ctx.setLineDash(g.dash || []);
        const w = g.width * lw;
        if (g.mode === MODE_ACTIVE && tok.glow > 0) {
          ctx.globalAlpha = tok.glow * 0.14;
          ctx.lineWidth = w * 6;
          ctx.stroke();
          ctx.globalAlpha = tok.glow * 0.3;
          ctx.lineWidth = w * 2.6;
          ctx.stroke();
        }
        ctx.globalAlpha = g.mode === MODE_DIM ? tok.dimAlpha : g.mode === MODE_ACTIVE ? tok.activeAlpha : tok.edgeAlpha;
        ctx.lineWidth = w;
        ctx.stroke();
      }
      ctx.setLineDash([]);
      if (pCount) {
        ctx.globalCompositeOperation = tok.blend;
        const size = tok.psize * 6 * Math.max(1, 0.6 / k), hs = size / 2;
        const dimA = tok.activeAlpha > 0 ? tok.dimAlpha / tok.activeAlpha : 0;
        for (let i = 0; i < pCount; i++) {
          const e = pE[i], t = pT[i], u = 1 - t;
          const x = u * u * e.x0 + 2 * u * t * e.cx + t * t * e.x1, y = u * u * e.y0 + 2 * u * t * e.cy + t * t * e.y1;
          const a = Math.min(1, t * 8, u * 8) * (e.mode === MODE_DIM ? dimA : 1);
          if (a <= 0.01) continue;
          ctx.globalAlpha = a;
          ctx.drawImage(e.sprite, x - hs, y - hs, size, size);
        }
        ctx.globalCompositeOperation = 'source-over';
      }
      ctx.globalAlpha = 1;
    }
    function addParticle(e, t) {
      if (pCount >= P_CAP) return;
      pE[pCount] = e;
      pT[pCount] = t;
      pCount++;
    }
    function stepParticles(dt) {
      let i = 0;
      while (i < pCount) {
        const e = pE[i];
        const t = pT[i] + (P_SPEED * dt) / e.len;
        if (t >= 1 || !e.vis) {
          pCount--;
          pE[i] = pE[pCount]; pT[i] = pT[pCount]; pE[pCount] = undefined;
          continue;
        }
        pT[i] = t;
        i++;
      }
      for (const e of edges) {
        if (!e.active || !e.vis) continue;
        if (e.prime) {                       // newly active: start with particles already in flight
          e.prime = false;
          const n = Math.floor((e.emit * e.len) / P_SPEED);
          const o = unit(e.id + '~');
          for (let q = 0; q < n; q++) addParticle(e, (o + q / Math.max(1, n)) % 1);
        }
        e.acc += e.emit * dt;
        while (e.acc >= 1) { e.acc -= 1; addParticle(e, 0); }
      }
    }

    // ── Frame loop ──────────────────────────────────────────────────────
    function wants() {
      if (destroyed || paused || doc.hidden || !W || !H) return false;
      return !!(dirty || camAnim || tween || entering.size || posDirty || (!reduced && (activeCount > 0 || pCount > 0)));
    }
    function schedule() {
      if (raf || destroyed || paused || doc.hidden || !W || !H) return;
      raf = win.requestAnimationFrame(frame);
    }
    function frame(ts) {
      raf = 0;
      if (destroyed || paused || doc.hidden) { lastTs = 0; return; }
      const t0 = now();
      if ((win.devicePixelRatio || 1) !== dpr) resize();
      const dt = lastTs ? clamp((ts - lastTs) / 1000, 0, 0.05) : 1 / 60;
      lastTs = ts;
      if (camAnim) stepCam(t0);
      if (tween) stepTween(t0);
      if (posDirty) writeTransforms();
      if (entering.size) {
        for (const n of entering) { n.enter--; if (n.enter <= 0) { n.enter = 0; entering.delete(n); composeClass(n); } }
      }
      if (groupsDirty) buildGroups();
      for (const e of edges) geom(e);
      if (!reduced) stepParticles(dt); else if (pCount) clearParticles();
      draw();
      frames[frameN % 60] = now() - t0;
      frameN++;
      if (wants()) schedule(); else lastTs = 0;
    }
    function clearParticles() {
      for (let i = 0; i < pCount; i++) pE[i] = undefined;
      pCount = 0;
    }
    function resize() {
      const w = host.clientWidth, h = host.clientHeight, r = win.devicePixelRatio || 1;
      if (w === W && h === H && r === dpr) return;
      W = w; H = h; dpr = r;
      canvas.width = Math.max(1, Math.round(W * dpr));
      canvas.height = Math.max(1, Math.round(H * dpr));
      canvas.style.width = W + 'px';
      canvas.style.height = H + 'px';
      if (W && H && order.length && (pendingFit || !fitted || !camUser)) autoFrame(false, false);
      dirty = true;
      schedule();
    }

    // ── Input ───────────────────────────────────────────────────────────
    let hostRect = null;
    function local(e, fresh) {
      if (fresh || !hostRect) hostRect = host.getBoundingClientRect();
      return [e.clientX - hostRect.left - host.clientLeft, e.clientY - hostRect.top - host.clientTop];
    }
    function nodeAt(target) {
      const el = target && target.closest ? target.closest('.gx-node') : null;
      if (!el || el.parentNode !== world) return null;
      const n = nodes.get(el.dataset.id);
      return n && n.el === el ? n : null;
    }
    function setHover(id) {
      if (id === hoverId) return;
      const was = hoverId && nodes.get(hoverId);
      hoverId = id;
      if (was) composeClass(was);
      const n = id && nodes.get(id);
      if (n) composeClass(n);
      call(opts.onHover, id);
    }
    function endGesture() {
      gesture = null;
      host.classList.remove('is-dragging');
    }
    function startPinch() {
      const [a, b] = [...ptrs.values()];
      finishTween();
      gesture = { kind: 'pinch', d0: Math.max(1, Math.hypot(b.x - a.x, b.y - a.y)), k0: cam.k, wx: ((a.x + b.x) / 2 - cam.x) / cam.k, wy: ((a.y + b.y) / 2 - cam.y) / cam.k };
      camUser = true;
      camAnim = null;
      host.classList.add('is-dragging');
      for (const id of ptrs.keys()) { try { host.setPointerCapture(id); } catch (_) { /* gone */ } }
    }
    function onDown(e) {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      const [x, y] = local(e, true);
      if (e.isPrimary) { ptrs.clear(); endGesture(); }      // a new primary pointer: nothing else is down
      ptrs.set(e.pointerId, { x, y });
      if (ptrs.size === 2) { startPinch(); return; }
      if (ptrs.size > 2) return;
      const n = nodeAt(e.target);
      gesture = { kind: 'press', pid: e.pointerId, x0: x, y0: y, lx: x, ly: y, node: n, group: null, cam0: null };
      if (!n && e.pointerType === 'mouse') e.preventDefault();
    }
    function onMove(e) {
      if (!gesture || gesture.kind === 'press') { const n = nodeAt(e.target); setHover(n ? n.id : null); }
      const p = ptrs.get(e.pointerId);
      if (!p || !gesture) return;
      const [x, y] = local(e, false);
      p.x = x; p.y = y;
      const g = gesture;
      if (g.kind === 'pinch') {
        if (ptrs.size < 2) return;
        const [a, b] = [...ptrs.values()];
        const k = clamp((g.k0 * Math.max(1, Math.hypot(b.x - a.x, b.y - a.y))) / g.d0, K_MIN, K_MAX);
        setCam((a.x + b.x) / 2 - g.wx * k, (a.y + b.y) / 2 - g.wy * k, k);
        return;
      }
      if (g.pid !== e.pointerId) return;
      if (g.kind === 'press') {
        if (Math.hypot(x - g.x0, y - g.y0) < SLOP) return;
        try { host.setPointerCapture(e.pointerId); } catch (_) { /* gone */ }
        host.classList.add('is-dragging');
        if (g.node) { finishTween(); g.kind = 'drag'; g.group = subtree(g.node); } else {
          g.kind = 'pan'; camUser = true; camAnim = null; g.cam0 = { x: cam.x, y: cam.y };
        }
      }
      if (g.kind === 'pan') setCam(g.cam0.x + x - g.x0, g.cam0.y + y - g.y0, cam.k);
      else if (g.kind === 'drag') {
        const dx = (x - g.lx) / cam.k, dy = (y - g.ly) / cam.k;
        for (const n of g.group) { n.x += dx; n.y += dy; n.fx += dx; n.fy += dy; }
        posDirty = true;
        dirty = true;
        schedule();
      }
      g.lx = x; g.ly = y;
    }
    function onUp(e) {
      if (!ptrs.has(e.pointerId)) return;
      ptrs.delete(e.pointerId);
      const g = gesture;
      if (!g) return;
      if (g.kind === 'pinch') {
        if (ptrs.size === 1) {
          const [[pid, p]] = [...ptrs.entries()];
          gesture = { kind: 'pan', pid, x0: p.x, y0: p.y, lx: p.x, ly: p.y, node: null, cam0: { x: cam.x, y: cam.y } };
        } else if (!ptrs.size) endGesture();
        return;
      }
      if (g.pid !== e.pointerId) return;
      endGesture();
      if (g.kind === 'press' && e.type === 'pointerup') tap(g.node, e.timeStamp);
    }
    function tap(n, t) {
      const id = n ? n.id : null;
      if (n && lastTap && lastTap.id === id && t - lastTap.t < DBL_MS) {
        lastTap = null;
        call(opts.onActivate, id);
        return;
      }
      lastTap = n ? { id, t } : null;
      pick(id);
    }
    function onWheel(e) {
      e.preventDefault();
      if (!W || !H) return;
      let dy = e.deltaY;
      if (e.deltaMode === 1) dy *= 16; else if (e.deltaMode === 2) dy *= H;
      const f = clamp(Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.0015)), 0.5, 2);
      const [x, y] = local(e, true);
      camAnim = null;
      camUser = true;
      const k = clamp(cam.k * f, K_MIN, K_MAX);
      if (k === cam.k) return;
      setCam(x - (x - cam.x) * (k / cam.k), y - (y - cam.y) * (k / cam.k), k);
    }
    function onLeaveHost() { if (!gesture) setHover(null); }
    function onFocusIn(e) {
      const n = nodeAt(e.target);
      if (n && rover !== n.id) { rover = n.id; updateTabindex(); }
      syncInv();
    }
    /* Nearest node in a screen direction: distance plus an angular penalty;
       anything behind is ignored (world space ranks the same as screen). */
    function neighbour(n, ux, uy) {
      let best = null, bs = Infinity;
      for (const pass of [1.2, 3, Infinity]) {            // cones of about 50 and 72 degrees, then anything ahead
        for (const m of order) {
          if (m === n) continue;
          const vx = m.fx - n.fx, vy = m.fy - n.fy;
          const along = vx * ux + vy * uy;
          if (along <= 1) continue;
          const perp = Math.abs(vx * uy - vy * ux);
          if (perp > along * pass) continue;
          const s = Math.hypot(vx, vy) + perp * 2;
          if (s < bs) { bs = s; best = m; }
        }
        if (best) break;
      }
      return best;
    }
    function onKey(e) {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      const n = nodeAt(e.target);
      if (!n || e.target !== n.el) return;
      let to = null, handled = true;
      switch (e.key) {
        case 'ArrowLeft': to = neighbour(n, -1, 0); break;
        case 'ArrowRight': to = neighbour(n, 1, 0); break;
        case 'ArrowUp': to = neighbour(n, 0, -1); break;
        case 'ArrowDown': to = neighbour(n, 0, 1); break;
        case 'Home': to = order[0]; break;
        case 'End': to = order[order.length - 1]; break;
        case 'Enter': case ' ': case 'Spacebar': pick(n.id); break;
        default: handled = false;
      }
      if (!handled) return;
      e.preventDefault();
      e.stopPropagation();
      if (to) focusNode(to.id);
    }
    function onVisibility() {
      if (doc.hidden) { if (raf) win.cancelAnimationFrame(raf); raf = 0; lastTs = 0; return; }
      dirty = true;
      schedule();
    }
    function onTheme() { if (destroyed) return; readTheme(); schedule(); }
    function onReduce() {
      reduced = !!(mqReduce && mqReduce.matches);
      if (reduced) {
        clearParticles();
        finishTween();
        if (camAnim) { const t = camAnim.to; camAnim = null; setCam(t.x, t.y, t.k); }
        for (const n of entering) { n.enter = 0; composeClass(n); }
        entering.clear();
        flushLeaving();
      } else for (const e of edges) e.prime = e.active;
      dirty = true;
      schedule();
    }

    const listeners = [
      [host, 'pointerdown', onDown], [host, 'pointermove', onMove], [host, 'pointerup', onUp], [host, 'pointercancel', onUp],
      [host, 'pointerleave', onLeaveHost], [host, 'keydown', onKey], [host, 'focusin', onFocusIn], [doc, 'visibilitychange', onVisibility],
    ];
    for (const [t, ev, fn] of listeners) t.addEventListener(ev, fn);
    host.addEventListener('wheel', onWheel, { passive: false });
    const mqOn = (q, fn) => { if (!q) return; if (q.addEventListener) q.addEventListener('change', fn); else if (q.addListener) q.addListener(fn); };
    const mqOff = (q, fn) => { if (!q) return; if (q.removeEventListener) q.removeEventListener('change', fn); else if (q.removeListener) q.removeListener(fn); };
    mqOn(mqReduce, onReduce);
    mqOn(mqDark, onTheme);
    let mo = null, ro = null;
    try { mo = new win.MutationObserver(onTheme); mo.observe(doc.documentElement, { attributes: true, attributeFilter: ['data-theme'] }); } catch (_) { mo = null; }
    try { ro = new win.ResizeObserver(() => resize()); ro.observe(host); } catch (_) { ro = null; }

    readTheme();
    setCam(0, 0, 1);
    resize();

    function destroy() {
      if (destroyed) return;
      destroyed = true;
      if (raf) win.cancelAnimationFrame(raf);
      raf = 0;
      for (const [t, ev, fn] of listeners) t.removeEventListener(ev, fn);
      host.removeEventListener('wheel', onWheel, { passive: false });
      mqOff(mqReduce, onReduce);
      mqOff(mqDark, onTheme);
      if (mo) mo.disconnect();
      if (ro) ro.disconnect();
      for (const { timer } of leaving.values()) clearTimeout(timer);
      leaving.clear();
      nodes.clear();
      order = []; edges = []; edgeById = new Map(); entering.clear();
      clearParticles();
      host.replaceChildren();
      host.classList.remove('gx', 'is-dragging');
      host.removeAttribute('data-lod');
      host.removeAttribute('data-sel');
      Object.assign(host.style, prev);
    }

    return {
      setData(data) {
        if (destroyed) return;
        try { applyData(data); } catch (err) { if (win.console) win.console.error('ccGraph.setData', err); }
      },
      select,
      selected: () => selected,
      focusNode,
      center,
      fit(o) {
        if (destroyed) return;
        camUser = false;
        if (!order.length || !W || !H) { pendingFit = true; return; }
        fitNow(!o || o.animate !== false);
      },
      /* Screen px along each edge that something covers (fit, centre and framing
         keep nodes out from under it), and o.avoid: one rect {x, y, w, h} in host
         px that floats over the floor (the zoom buttons), or null. Stores only;
         reframe() applies it. */
      setInsets(o) {
        if (!o || typeof o !== 'object') return;
        for (const k of ['top', 'right', 'bottom', 'left']) if (isNum(o[k])) insets[k] = Math.max(0, o[k]);
        if ('avoid' in o) {
          const a = o.avoid;
          insets.avoid = a && isNum(a.x) && isNum(a.y) && isNum(a.w) && isNum(a.h) && a.w > 0 && a.h > 0 ? { x: a.x, y: a.y, w: a.w, h: a.h } : null;
        }
      },
      /* Re-frame after the free rect changed or a node was picked: id is the node
         to keep in sight with its parent and children (the inspector's), or null
         when nothing is open. The rule is autoFrame()'s. */
      reframe(id, o) {
        if (destroyed) return;
        keep = id != null && nodes.has(String(id)) ? String(id) : null;
        autoFrame(!o || o.animate !== false, true);
      },
      zoomBy,
      /* pause(true) while the floor is not shown (the view's List mode): no frames at all; the
         layout and the camera land at once, particles stop. pause(false) picks up where it is. */
      pause(on) {
        on = !!on;
        if (destroyed || on === paused) return;
        paused = on;
        if (on) {
          if (raf) win.cancelAnimationFrame(raf);
          raf = 0; lastTs = 0;
          finishTween();
          if (camAnim) { const t = camAnim.to; camAnim = null; setCam(t.x, t.y, t.k); }
          for (const n of entering) { n.enter = 0; composeClass(n); }
          entering.clear();
          clearParticles();
          writeTransforms();
        } else {
          for (const e of edges) e.prime = e.active;
          dirty = true;
          schedule();
        }
      },
      positions() {
        const out = {};
        for (const n of order) out[n.id] = { x: n.x, y: n.y };
        return out;
      },
      camera: () => ({ x: cam.x, y: cam.y, k: cam.k }),
      screenPos(id) {
        const n = nodes.get(String(id));
        return n ? { x: n.x * cam.k + cam.x, y: n.y * cam.k + cam.y } : null;
      },
      stats() {
        const c = Math.min(60, frameN);
        let s = 0;
        for (let i = 0; i < c; i++) s += frames[i];
        return { nodes: order.length, edges: edges.length, particles: pCount, alpha: simAlpha, settled, frameMs: c ? s / c : 0, ticks: simTicks, settleMs, frames: frameN, paused };
      },
      invalidate(id) {
        if (destroyed) return;
        const list = id == null ? order : [nodes.get(String(id))].filter(Boolean);
        for (const n of list) { syncNode(n, true); composeClass(n); }
      },
      destroy,
    };
  }

  window.ccGraph = { create };
})();
