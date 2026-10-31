/* cc-dash Docs view: a read-only reader for each project's Markdown and HTML.

   Layout: one toolbar row (project picker, filter/search box), then the file
   tree beside the reader, and a table of contents on wide screens. The tree
   is grouped the way cc_tool projects are laid out (Vault, Plans & specs,
   Docs, Project root, Skills, Other) with "Recently changed" on top. On a
   phone the tree is a drawer.

   The route is #/docs?p=<project>&f=<path>&h=<heading>. Markdown is rendered
   by markdown.js into plain objects and built here with createElement and
   textContent through an allowlist of tags and attributes; nothing is ever
   assigned to innerHTML. HTML documents never enter this page: they load in
   an <iframe sandbox> (never allow-same-origin) from a one-time
   /docs-frame/<ticket> URL whose response carries its own strict CSP (see
   docs.py). Images are fetched with the token and shown as object URLs.

   Live reload: while the view is open the app's poll (every 3 s) asks for the
   open file with its last version; a changed file is rebuilt and morphed into
   the page (ccDom.into), so scroll position and selection stay. */
'use strict';
(() => {
  const A = window.ccApp;
  if (!A) return;
  const { h, s, api, P, S, views, main, projectHead, errorBox, chev, ago, plural, int, store } = A;
  const MD = window.ccMarkdown;

  const SECTIONS = [
    { k: 'vault', label: 'Vault', base: 'vault/' },
    { k: 'plans', label: 'Plans & specs', base: 'docs/superpowers/' },
    { k: 'docs', label: 'Docs', base: 'docs/' },
    { k: 'root', label: 'Project root', base: '' },
    { k: 'skills', label: 'Skills', base: '.claude/skills/' },
    { k: 'board', label: 'Agent runs', base: '.cc-board/', closed: true },
    { k: 'other', label: 'Other', base: '', closed: true },
  ];
  const RECENT_N = 6;
  const D = {
    projects: null, projectsAt: 0, project: '', tree: null, treeFor: '', treeAt: 0, treeErr: null,
    file: null, fileKey: '', fileErr: null, loading: '', q: '', search: null, searchSeq: 0, searchTimer: null,
    sel: -1, closed: new Set(SECTIONS.filter((x) => x.closed).map((x) => x.k)), scripts: new Set(),
    images: new Map(), frame: null, scrolledTo: '', drawer: false, lastF: {},
  };
  try { const c = JSON.parse(store.get('cc-dash-docs-closed') || 'null'); if (Array.isArray(c)) D.closed = new Set(c); } catch (e) { /* default */ }
  try { D.lastF = JSON.parse(store.get('cc-dash-docs-last') || '{}') || {}; } catch (e) { D.lastF = {}; }

  const enc = encodeURIComponent;
  const docHash = (p, f, hd) => '#/docs?' + new URLSearchParams(Object.entries({ p: p || '', f: f || '', h: hd || '' }).filter(([, v]) => v)).toString();
  const baseName = (p) => p.slice(p.lastIndexOf('/') + 1);
  const dirName = (p) => (p.lastIndexOf('/') < 0 ? '' : p.slice(0, p.lastIndexOf('/')));
  const stem = (p) => baseName(p).replace(/\.(md|markdown)$/i, '');
  const isDated = (name) => /^\d{4}-\d{2}(-\d{2})?|^\d{4}-W\d{2}/.test(name);

  // ── building the rendered tree (allowlist) ─────────────────────────────
  const TAGS = new Set(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'a', 'em', 'strong', 'del', 'code', 'pre', 'blockquote', 'ul', 'ol', 'li',
    'hr', 'br', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'img', 'input', 'div', 'span']);
  const ATTRS = new Set(['id', 'class', 'title', 'alt', 'target', 'rel', 'start', 'type', 'checked', 'disabled', 'loading', 'data-doc', 'data-asset', 'data-wiki']);
  function build(n, project) {
    if (typeof n === 'string') return document.createTextNode(n);
    const tag = TAGS.has(n.t) ? n.t : 'span';
    const e = document.createElement(tag);
    for (const k in n.a) {
      const v = n.a[k];
      if (v == null) continue;
      if (k === 'href') {
        if (/^#\/docs\?/.test(v) || /^(https?:|mailto:)/i.test(v)) e.setAttribute('href', v);
      } else if (k === 'src') {
        if (/^data:image\//i.test(v)) e.setAttribute('src', v);
      } else if (ATTRS.has(k)) {
        if (tag === 'input' && k === 'type' && v !== 'checkbox') continue;
        e.setAttribute(k, v);
      }
    }
    if (tag === 'img' && n.a['data-asset']) {
      const url = D.images.get(project + '\u0000' + n.a['data-asset']);
      if (url && url !== 'fail') e.setAttribute('src', url);
      else e.className = 'md-img-wait' + (url === 'fail' ? ' fail' : '');
    }
    if (tag === 'input') e.tabIndex = -1;
    for (const c of n.c) e.appendChild(build(c, project));
    return e;
  }
  async function loadImages(root, project) {
    const want = [...root.querySelectorAll('img[data-asset]')].map((i) => i.getAttribute('data-asset'));
    for (const path of new Set(want)) {
      const key = project + '\u0000' + path;
      if (D.images.has(key)) continue;
      D.images.set(key, 'pending');
      let url = 'fail';
      try {
        const r = await window.ccAuth.fetch('/api/docs/asset?project=' + enc(project) + '&path=' + enc(path));
        if (r.ok) {
          const blob = await r.blob();
          // SVG as a data: URL (opened alone it gets an opaque origin); raster images as blob: URLs.
          url = /svg/.test(blob.type) ? await new Promise((res) => { const fr = new FileReader(); fr.onload = () => res(String(fr.result)); fr.onerror = () => res('fail'); fr.readAsDataURL(blob); })
            : URL.createObjectURL(blob);
        }
      } catch (e) { url = 'fail'; }
      D.images.set(key, url);
      if (D.project !== project) continue;
      for (const img of document.querySelectorAll('.docs-read img[data-asset]')) {
        if (img.getAttribute('data-asset') !== path) continue;
        if (url !== 'fail') { img.src = url; img.className = ''; } else img.className = 'md-img-wait fail';
      }
    }
  }
  /* Markdown inside timeline entries (app.js clampText): the same parser and the same allowlist
     builder, so raw HTML stays text and links follow its scheme rules. No front matter, no table of
     contents, no heading ids (many entries share a page) and no project links: a relative link is
     plain text. Parsed trees are cached by source, so a poll rebuilds identical nodes cheaply. */
  const mdCache = new Map();
  A.markdown = (src) => {
    let body = mdCache.get(src);
    if (!body) {
      body = MD.render(src, { docHref: () => '', wiki: () => null }).body;
      if (mdCache.size > 3000) mdCache.clear();
      mdCache.set(src, body);
    }
    const strip = (n) => (typeof n === 'string' ? n : { t: n.t === 'img' ? 'span' : n.t, a: Object.fromEntries(Object.entries(n.a || {}).filter(([k]) => k !== 'id' && k !== 'data-asset' && k !== 'data-doc')), c: (n.c || []).map(strip) });
    return body.map((n) => build(strip(n), ''));
  };
  function dropImages() {
    for (const u of D.images.values()) if (typeof u === 'string' && u.startsWith('blob:')) URL.revokeObjectURL(u);
    D.images.clear();
  }

  // ── wikilink resolution by file name, within the project ───────────────
  function wikiIndex() {
    if (D.tree && D.tree.__wiki) return D.tree.__wiki;
    const idx = new Map();
    for (const f of (D.tree ? D.tree.files : [])) {
      const k = stem(f.path).toLowerCase();
      if (!idx.has(k)) idx.set(k, []);
      idx.get(k).push(f.path);
    }
    if (D.tree) { D.tree.__wiki = idx; D.tree.__paths = new Set(D.tree.files.map((f) => f.path)); }
    return idx;
  }
  function resolveWiki(target, from) {
    const t = target.replace(/\.(md|markdown)$/i, '').replace(/^\/+/, '');
    const k = baseName(t).toLowerCase();
    let hits = wikiIndex().get(k) || [];
    if (t.includes('/')) hits = hits.filter((p) => p.toLowerCase().replace(/\.(md|markdown)$/i, '').endsWith(t.toLowerCase()));
    if (!hits.length) return null;
    // Prefer the same top folder (the vault links within the vault), then the shortest path.
    const top = from.split('/')[0];
    const best = hits.slice().sort((a, b) => ((b.split('/')[0] === top) - (a.split('/')[0] === top)) || a.length - b.length || a.localeCompare(b))[0];
    return { path: best };
  }

  // ── data ───────────────────────────────────────────────────────────────
  async function loadProjects(force) {
    if (D.projects && !force && Date.now() - D.projectsAt < 60000) return;
    const d = await api('/api/docs/projects');
    D.projects = d.projects || [];
    D.projectsAt = Date.now();
  }
  async function loadTree(force) {
    const p = D.project;
    if (!p) return;
    if (!force && D.tree && D.treeFor === p && Date.now() - D.treeAt < 10000) return;
    try {
      const t = await api('/api/docs/tree?project=' + enc(p));
      if (D.project !== p) return;
      D.tree = t; D.treeFor = p; D.treeAt = Date.now(); D.treeErr = null;
    } catch (e) { if (D.project === p) { D.treeErr = e; D.treeAt = Date.now(); } if (e.status === 401) throw e; }
  }
  async function loadFile(f, poll) {
    const p = D.project;
    const key = p + '\u0000' + f;
    const known = poll && D.file && D.fileKey === key ? D.file.version : '';
    if (!poll) D.loading = key;
    try {
      const d = await api('/api/docs/file?project=' + enc(p) + '&path=' + enc(f) + (known ? '&known=' + enc(known) : ''));
      if (D.project !== p || (S.route.f || '') !== f) return false;
      D.fileErr = null;
      if (d.unchanged) return false;
      const changed = D.fileKey === key;
      D.file = d; D.fileKey = key;
      if (d.kind === 'html') D.frame = null;
      return changed ? 'changed' : 'new';
    } catch (e) {
      if (D.project === p && (S.route.f || '') === f) { D.fileErr = e; if (!poll) { D.file = null; D.fileKey = key; } }
      if (e.status === 401) throw e;
      return 'error';
    } finally { if (D.loading === key) D.loading = ''; }
  }
  async function frameUrl() {
    const d = D.file;
    const scripts = D.scripts.has(D.fileKey);
    const r = await api('/api/docs/frame?project=' + enc(D.project) + '&path=' + enc(d.path) + (scripts ? '&scripts=1' : ''));
    return { url: r.url, scripts, version: d.version };
  }

  // ── the view ───────────────────────────────────────────────────────────
  const V = {
    /* The Docs link for a project scope; with none, the project read last. */
    hashFor(scope) { const p = scope || D.project || store.get('cc-dash-docs-project') || ''; return docHash(p, p ? (D.lastF[p] || '') : ''); },
    focusSearch() { if (V.input) { if (window.matchMedia('(max-width: 900px)').matches) setDrawer(true); V.input.focus(); V.input.select(); } },
    mount(root) {
      D.sel = -1; D.scrolledTo = '';
      const icon = s('svg', { viewBox: '0 0 16 16', 'aria-hidden': 'true' });
      icon.append(s('circle', { cx: 7, cy: 7, r: 4.5, fill: 'none', stroke: 'currentColor', 'stroke-width': 1.5 }), s('path', { d: 'm10.5 10.5 3 3', stroke: 'currentColor', 'stroke-width': 1.5, 'stroke-linecap': 'round' }));
      V.input = h('input', { type: 'search', id: 'docs-q', placeholder: 'Filter files, search contents', 'aria-label': 'Filter file names and search the project\'s docs', value: D.q, autocomplete: 'off', spellcheck: 'false',
        oninput: (e) => { D.q = e.target.value; D.sel = -1; queueSearch(); renderNav(); },
        onkeydown: (e) => {
          if (e.key === 'Escape') { e.preventDefault(); if (D.q) { D.q = ''; e.target.value = ''; D.search = null; renderNav(); } else e.target.blur(); }
          else if (e.key === 'ArrowDown' || (e.key === 'Enter' && D.sel < 0)) { e.preventDefault(); e.target.blur(); D.sel = 0; renderNav(); scrollSel(); if (e.key === 'Enter') openSel(); }
        } });
      V.head = projectHead('docs', { noAll: true, counts: false });
      V.summary = V.head.sub;
      V.filesBtn = h('button', { type: 'button', class: 'btn docs-files-btn', 'aria-controls': 'docs-nav', 'aria-expanded': 'false', onclick: () => setDrawer(!D.drawer) }, 'Files');
      V.nav = h('aside', { class: 'docs-nav', id: 'docs-nav', 'aria-label': 'Documents' });
      V.read = h('article', { class: 'docs-read' });
      V.toc = h('nav', { class: 'docs-toc', 'aria-label': 'On this page' });
      V.scrim = h('div', { class: 'docs-scrim', hidden: true, onclick: () => setDrawer(false) });
      V.searchBox = h('div', { class: 'search' }, icon, V.input, h('kbd', { text: '/' }));
      V.navList = h('div', { class: 'docs-nav-list' });
      V.nav.append(V.navList);
      V.bar = h('div', { class: 'filters docs-bar' }, V.searchBox, V.filesBtn);
      root.append(V.head.el, V.bar, h('div', { class: 'docs' }, V.nav, V.read, V.toc), V.scrim);
      setDrawer(false);
      placeSearch();
    },
    update() { route(); },
    async poll() {
      if (S.route.view !== 'docs') return;
      await loadProjects();
      if (!D.project) { route(); return; }
      await loadTree();
      renderNav();
      const f = S.route.f || '';
      if (f && D.fileKey === D.project + '\u0000' + f && !D.loading) {
        const res = await loadFile(f, true);
        if (res === 'changed') renderRead(true);
        else if (res === 'error' || D.fileErr) renderRead(true);
      }
    },
    keys(k) {
      const items = navItems();
      if (k === 'j' || k === 'k') {
        if (!items.length) return true;
        D.sel = Math.max(0, Math.min(items.length - 1, D.sel + (k === 'j' ? 1 : -1)));
        renderNav(); scrollSel();
        return true;
      }
      if (k === 'Enter' && D.sel >= 0) { openSel(); return true; }
      if (k === 'Escape' && D.drawer) { setDrawer(false); return true; }
      return false;
    },
  };
  views.docs = V;

  /* On a phone the search box lives at the top of the drawer, next to its results. */
  const narrow = window.matchMedia('(max-width: 900px)');
  function placeSearch() {
    if (!V.searchBox) return;
    if (narrow.matches) { if (V.searchBox.parentNode !== V.nav) V.nav.insertBefore(V.searchBox, V.navList); } else if (V.searchBox.parentNode !== V.bar) V.bar.insertBefore(V.searchBox, V.filesBtn);
  }
  if (narrow.addEventListener) narrow.addEventListener('change', placeSearch);
  function setDrawer(on) {
    D.drawer = on;
    if (!V.nav) return;
    V.nav.classList.toggle('open', on);
    V.scrim.hidden = !on;
    V.filesBtn.setAttribute('aria-expanded', String(on));
    document.documentElement.classList.toggle('docs-drawer-open', on);
  }
  function go(p, f, hd, replace) {
    const hash = docHash(p, f, hd);
    if (location.hash === hash) return;
    if (replace) { history.replaceState(null, '', hash); window.dispatchEvent(new HashChangeEvent('hashchange')); } else location.hash = hash;
  }
  function navItems() { return V.navList ? [...V.navList.querySelectorAll('[data-nav]')] : []; }
  function scrollSel() { const el = V.nav && V.nav.querySelector('.sel'); if (el) el.scrollIntoView({ block: 'nearest' }); }
  function openSel() { const it = navItems()[D.sel]; if (it) { const href = it.getAttribute('href'); if (href) location.hash = href; } }

  let routing = false, again = false;
  async function route() {
    if (routing) { again = true; return; }
    routing = true;
    try {
      do { again = false; await routeOnce(); } while (again);
    } finally { routing = false; }
  }
  async function routeOnce() {
    {
      if (!D.projects) {
        if (!V.read.firstChild) V.read.append(h('div', { class: 'sk sk-line', style: { width: '40%', height: '22px' } }), h('div', { class: 'sk sk-block' }));
        try { await loadProjects(); } catch (e) { if (e.status !== 401) P.into(V.read, errorBox(e, 'the project list')); return; }
      }
      const r = S.route;
      const names = D.projects.map((x) => x.project);
      let p = r.p && names.includes(r.p) ? r.p : '';
      if (!p) {
        // The shared project scope first, then the project read last, then the most recent one.
        const last = store.get('cc-dash-docs-project');
        p = S.ui.scope && names.includes(S.ui.scope) ? S.ui.scope : last && names.includes(last) ? last : (D.projects.slice().sort((a, b) => b.last_active - a.last_active)[0] || {}).project || '';
        if (p) { go(p, D.lastF[p] || '', '', true); return; }
      }
      V.head.set(D.projects.map((x) => ({ project: x.project, sessions: x.sessions, worktrees: [] })), { p, wt: '' }, D.projects.length);
      if (!p) { renderEmptyProjects(); return; }
      if (p !== D.project) {
        D.project = p; D.tree = null; D.file = null; D.fileKey = ''; D.search = null; D.sel = -1; D.scrolledTo = '';
        dropImages();
        store.set('cc-dash-docs-project', p);
        if (D.q.trim().length >= 2) queueSearch();
      }
      renderNav();
      await loadTree();
      renderNav();
      const f = r.f || '';
      if (!f) {
        const pick = defaultFile();
        if (pick) { go(p, pick, '', true); return; }
        renderRead(); return;
      }
      D.lastF[p] = f;
      store.set('cc-dash-docs-last', JSON.stringify(D.lastF));
      A.syncTabLinks();
      if (D.fileKey !== p + '\u0000' + f) {
        if (D.drawer) setDrawer(false);
        renderRead();                       // loading state
        await loadFile(f, false);
        D.scrolledTo = '';
        renderRead();
        window.scrollTo(0, 0);
      } else renderRead();
      scrollToHeading();
    }
  }
  function defaultFile() {
    const files = D.tree ? D.tree.files : [];
    if (!files.length) return '';
    const pref = ['README.md', 'readme.md', 'vault/README.md', 'docs/README.md', 'CLAUDE.md'];
    for (const x of pref) if (files.some((f) => f.path === x)) return x;
    return files.slice().sort((a, b) => b.mtime - a.mtime)[0].path;
  }
  function scrollToHeading() {
    const hd = S.route.h || '';
    const key = D.fileKey + '#' + hd;
    if (!hd || D.scrolledTo === key) return;
    const t = document.getElementById('doc-' + hd);
    if (t) { D.scrolledTo = key; t.scrollIntoView({ block: 'start' }); }
  }

  // ── search ─────────────────────────────────────────────────────────────
  function queueSearch() {
    clearTimeout(D.searchTimer);
    const q = D.q.trim();
    if (q.length < 2 || !D.project) { D.search = null; return; }
    D.searchTimer = setTimeout(async () => {
      const seq = ++D.searchSeq;
      try {
        const d = await api('/api/docs/search?project=' + enc(D.project) + '&q=' + enc(q));
        if (seq !== D.searchSeq) return;
        D.search = d;
      } catch (e) { if (seq === D.searchSeq) D.search = { q, error: e.message, results: [] }; }
      renderNav();
    }, 220);
  }

  // ── the tree ───────────────────────────────────────────────────────────
  const GENERIC = /^(skill|readme|index|spec|plan|notes|overview|changelog|todo|tasks)$/i;
  function fileRow(f, i, opts) {
    const cur = D.fileKey === D.project + '\u0000' + f.path;
    const name = baseName(f.path);
    let label = /\.(md|markdown)$/i.test(name) ? name.replace(/\.(md|markdown)$/i, '') : name;
    if (opts.ago && GENERIC.test(label) && dirName(f.path)) label = baseName(dirName(f.path)) + '/' + label;   // which SKILL, which README
    return h('a', { href: docHash(D.project, f.path), class: 'docs-file' + (cur ? ' cur' : '') + (i === D.sel ? ' sel' : ''), 'data-nav': '', dataset: { key: (opts.prefix || 'f') + ':' + f.path },
      'aria-current': cur ? 'page' : null, title: f.path },
    h('span', { class: 'ellipsis', text: opts.full ? f.path : label }),
    opts.ago ? h('span', { class: 'docs-ago', dataset: { ago: f.mtime }, text: ago(f.mtime) }) : (f.kind !== 'md' ? h('span', { class: 'docs-ext', text: f.kind }) : null));
  }
  function sortFiles(files) {
    return files.slice().sort((a, b) => {
      const an = baseName(a.path), bn = baseName(b.path);
      const ar = /^(readme|index)\./i.test(an), br = /^(readme|index)\./i.test(bn);
      if (ar !== br) return ar ? -1 : 1;
      if (isDated(an) && isDated(bn)) return bn.localeCompare(an);
      return an.toLowerCase().localeCompare(bn.toLowerCase());
    });
  }
  function sectionBlock(sec, files, counter) {
    const open = !D.closed.has(sec.k);
    const groups = new Map();
    for (const f of files) {
      let d = dirName(f.path);
      if (sec.base && d.startsWith(sec.base.replace(/\/$/, ''))) d = d.slice(sec.base.length);
      if (sec.k === 'docs' && d === 'docs') d = '';
      if (sec.k === 'vault' && d === 'vault') d = '';
      if (!groups.has(d)) groups.set(d, []);
      groups.get(d).push(f);
    }
    const dirs = [...groups.keys()].sort((a, b) => (a === '' ? -1 : b === '' ? 1 : a.localeCompare(b)));
    const body = [];
    if (open) {
      for (const d of dirs) {
        if (d) body.push(h('div', { class: 'docs-dir ellipsis', dataset: { key: 'd:' + sec.k + ':' + d }, text: d + '/' , title: d }));
        for (const f of sortFiles(groups.get(d))) body.push(fileRow(f, counter.i++, {}));
      }
    }
    return h('section', { class: 'docs-sec', dataset: { key: 's:' + sec.k } },
      h('button', { type: 'button', class: 'docs-sec-h' + (open ? ' open' : ''), 'aria-expanded': String(open),
        onclick: () => { if (D.closed.has(sec.k)) D.closed.delete(sec.k); else D.closed.add(sec.k); store.set('cc-dash-docs-closed', JSON.stringify([...D.closed])); D.sel = -1; renderNav(); } },
      chev(), h('span', { text: sec.label }), h('span', { class: 'docs-n', text: int(files.length) })),
      open ? h('div', { class: 'docs-sec-b' }, body) : null);
  }
  function renderNav() {
    if (!V.nav) return;
    const t = D.tree && D.treeFor === D.project ? D.tree : null;
    if (!t) {
      if (D.treeErr) P.into(V.navList, h('div', { class: 'docs-note bad', dataset: { key: 'err' }, text: 'Could not list files: ' + D.treeErr.message }));
      else P.into(V.navList, h('div', { dataset: { key: 'sk' } }, [80, 60, 70, 50, 65].map((w) => h('div', { class: 'sk sk-line', style: { width: w + '%' } }))));
      P.setText(V.summary, 'Markdown and HTML in the project\u2019s main checkout.');
      return;
    }
    P.setText(V.summary, plural(t.files.length, 'document') + (t.truncated ? ' (list capped)' : '') + ' in the project\u2019s main checkout. Read only; changes on disk show up within seconds.');
    const counter = { i: 0 };
    const q = D.q.trim().toLowerCase();
    const kids = [];
    if (q) {
      const byName = t.files.filter((f) => f.path.toLowerCase().includes(q)).slice(0, 60);
      kids.push(h('section', { class: 'docs-sec', dataset: { key: 'q:names' } }, h('div', { class: 'docs-sec-t' }, h('span', { text: 'File names' }), h('span', { class: 'docs-n', text: int(byName.length) })),
        byName.length ? h('div', { class: 'docs-sec-b' }, byName.map((f) => fileRow(f, counter.i++, { full: true, prefix: 'n' }))) : h('div', { class: 'docs-note', text: 'No file name matches.' })));
      if (q.length >= 2) {
        const sr = D.search && D.search.q.toLowerCase() === q ? D.search : null;
        const hits = sr ? sr.results.filter((r) => r.line) : [];
        kids.push(h('section', { class: 'docs-sec', dataset: { key: 'q:text' } }, h('div', { class: 'docs-sec-t' }, h('span', { text: 'In contents' }), sr ? h('span', { class: 'docs-n', text: int(hits.length) + (sr.truncated ? '+' : '') }) : null),
          !sr ? h('div', { class: 'docs-note', text: 'Searching' }) : sr.error ? h('div', { class: 'docs-note bad', text: sr.error })
            : !hits.length ? h('div', { class: 'docs-note', text: 'No matches in ' + plural(sr.files, 'file') + '.' })
              : h('div', { class: 'docs-sec-b' }, hits.map((r) => hitRow(r, counter.i++)),
                sr.truncated ? h('div', { class: 'docs-note', text: 'Stopped after ' + plural(sr.results.length, 'match', 'matches') + ' in ' + plural(sr.files, 'file') + '. Narrow the search for more.' }) : null)));
      }
    } else {
      if (!t.files.length) {
        P.into(V.navList, h('div', { class: 'docs-note', dataset: { key: 'none' }, text: 'No Markdown, HTML or text files in this project.' }));
        return;
      }
      const recent = t.files.slice().sort((a, b) => b.mtime - a.mtime).slice(0, RECENT_N);
      const rOpen = !D.closed.has('recent');
      kids.push(h('section', { class: 'docs-sec', dataset: { key: 's:recent' } },
        h('button', { type: 'button', class: 'docs-sec-h' + (rOpen ? ' open' : ''), 'aria-expanded': String(rOpen), onclick: () => { if (rOpen) D.closed.add('recent'); else D.closed.delete('recent'); store.set('cc-dash-docs-closed', JSON.stringify([...D.closed])); D.sel = -1; renderNav(); } },
          chev(), h('span', { text: 'Recently changed' })),
        rOpen ? h('div', { class: 'docs-sec-b' }, recent.map((f) => fileRow(f, counter.i++, { ago: true, prefix: 'r' }))) : null));
      for (const sec of SECTIONS) {
        const files = t.files.filter((f) => f.section === sec.k);
        if (files.length) kids.push(sectionBlock(sec, files, counter));
      }
    }
    P.into(V.navList, ...kids);
  }
  function hitRow(r, i) {
    const text = r.text || '';
    const parts = [];
    let at = 0;
    for (const [a, b] of r.hits || []) {
      if (a < at || b > text.length) continue;
      if (a > at) parts.push(text.slice(at, a));
      parts.push(h('mark', { text: text.slice(a, b) }));
      at = b;
    }
    parts.push(text.slice(at));
    return h('a', { href: docHash(D.project, r.path), class: 'docs-hit' + (i === D.sel ? ' sel' : ''), 'data-nav': '', dataset: { key: 'h:' + r.path + ':' + r.line }, title: r.path + ', line ' + r.line },
      h('div', { class: 'docs-hit-p' }, h('span', { class: 'ellipsis', text: r.path }), h('span', { class: 'docs-ago', text: 'line ' + r.line })),
      h('div', { class: 'docs-hit-t' }, parts));
  }

  // ── the reader ─────────────────────────────────────────────────────────
  function renderEmptyProjects() {
    P.into(V.navList);
    P.into(V.read, h('div', { class: 'empty', dataset: { key: 'noproj' } }, h('h2', { text: 'No projects in this window' }),
      h('p', { text: 'The Docs view lists the projects your Claude Code sessions ran in. Start a session in a project, or pick a longer window in the header.' })));
    P.into(V.toc);
  }
  function renderRead(soft) {
    const r = S.route;
    const f = r.f || '';
    const key = D.project + '\u0000' + f;
    if (!f) {
      P.into(V.read, h('div', { class: 'empty', dataset: { key: 'nofile' } }, h('h2', { text: 'No documents here' }), h('p', { text: 'This project has no Markdown, HTML or text files outside skipped folders.' })));
      P.into(V.toc);
      return;
    }
    if (D.fileKey !== key || (!D.file && !D.fileErr)) {
      if (!soft) P.into(V.read, h('div', { dataset: { key: 'loading' } }, h('div', { class: 'sk sk-line', style: { width: '30%' } }), h('div', { class: 'sk sk-line', style: { width: '55%', height: '24px', margin: '16px 0' } }),
        [90, 84, 92, 60].map((w) => h('div', { class: 'sk sk-line', style: { width: w + '%' } }))));
      return;
    }
    if (D.fileErr && !D.file) {
      const e = D.fileErr;
      const box = e.status === 404 ? h('div', { class: 'empty', dataset: { key: 'err404' } }, h('h2', { text: 'Not found' }), h('p', {}, 'There is no ', h('code', { text: f }), ' in ', D.project, '. It may have been moved or deleted.'))
        : errorBox(e, f);
      box.dataset.key = 'err';
      P.into(V.read, box);
      P.into(V.toc);
      return;
    }
    const d = D.file;
    const head = h('div', { class: 'docs-head', dataset: { key: 'head' } },
      h('span', { class: 'docs-path ellipsis', text: d.path, title: d.path }),
      h('span', { class: 'docs-ago', dataset: { ago: d.mtime }, text: ago(d.mtime), title: 'Last changed' }),
      d.kind === 'html' ? scriptsToggle() : null);
    if (d.kind === 'html') { renderHtml(head); return; }
    wikiIndex();
    const rendered = d.kind === 'md'
      ? MD.render(d.text, { path: d.path, docHref: (p, hd) => docHash(D.project, p, hd), wiki: (t) => resolveWiki(t, d.path), isFile: (p) => !D.tree || !D.tree.__paths || D.tree.__paths.has(p) })
      : MD.renderText(d.text);
    const body = h('div', { class: 'md' + (d.kind === 'txt' ? ' md-txt' : ''), dataset: { key: 'body' } }, rendered.body.map((n) => build(n, D.project)));
    const front = rendered.front.length ? h('dl', { class: 'md-front', dataset: { key: 'front' } }, rendered.front.map(([k, v]) => [h('dt', { text: k }), h('dd', {}, v.map((n) => build(n, D.project)))]).flat()) : null;
    const back = d.backlinks && d.backlinks.length ? h('section', { class: 'docs-back', dataset: { key: 'back' } }, h('h2', { text: 'Linked from' }),
      h('ul', {}, d.backlinks.map((p) => h('li', { dataset: { key: p } }, h('a', { href: docHash(D.project, p), text: p.replace(/\.(md|markdown)$/i, '') }))))) : null;
    P.into(V.read, head, front, body, back);   // a morph: on a live reload the scroll position stays
    loadImages(V.read, D.project);
    renderToc(rendered.headings);
  }
  function scriptsToggle() {
    const on = D.scripts.has(D.fileKey);
    return h('div', { class: 'seg', dataset: { key: 'scripts' } }, h('button', { type: 'button', 'aria-pressed': String(on),
      title: 'Runs this file\'s scripts inside an isolated frame. They still cannot reach cc-dash, its token or the network.',
      onclick: () => { if (on) D.scripts.delete(D.fileKey); else D.scripts.add(D.fileKey); D.frame = null; renderRead(true); } }, 'Allow scripts in this preview'));
  }
  async function renderHtml(head) {
    const d = D.file;
    const want = d.version + (D.scripts.has(D.fileKey) ? ':s' : '');
    if (D.frame && D.frame.want === want && D.frame.key === D.fileKey) {
      // Never move the frame (that reloads it, and its ticket is spent): patch only the head.
      const curHead = V.read.querySelector('.docs-head');
      if (V.read.contains(D.frame.el) && curHead) P.morph(curHead, head);
      else P.into(V.read, head, D.frame.el);
      P.into(V.toc);
      return;
    }
    const holder = h('div', { class: 'docs-frame-wrap', dataset: { key: 'frame:' + want } }, h('div', { class: 'sk sk-block' }));
    P.into(V.read, head, holder);
    P.into(V.toc);
    try {
      const fr = await frameUrl();
      if (D.file !== d) return;
      // sandbox is set before src, so the first load is already sandboxed; never allow-same-origin.
      const frame = document.createElement('iframe');
      frame.setAttribute('sandbox', fr.scripts ? 'allow-scripts' : '');
      frame.setAttribute('referrerpolicy', 'no-referrer');
      frame.setAttribute('title', d.path);
      frame.className = 'docs-frame';
      frame.setAttribute('src', fr.url);
      const el = h('div', { class: 'docs-frame-wrap', dataset: { key: 'frame:' + want } }, frame);
      D.frame = { want, key: D.fileKey, el };
      const cur = V.read.querySelector('.docs-frame-wrap');
      if (cur) cur.replaceWith(el);
    } catch (e) {
      const cur = V.read.querySelector('.docs-frame-wrap');
      if (cur) cur.replaceWith(errorBox(e, d.path));
    }
  }
  let tocObserver = null;
  function renderToc(headings) {
    const list = headings.filter((x) => x.level <= 3);
    const firstH1 = list.findIndex((x) => x.level === 1);
    const items = list.filter((x, i) => !(i === firstH1 && i === 0 && list.filter((y) => y.level === 1).length === 1));
    if (items.length < 2) { P.into(V.toc); if (tocObserver) tocObserver.disconnect(); return; }
    const minL = Math.min(...items.map((x) => x.level));
    P.into(V.toc, h('div', { class: 'docs-toc-t', dataset: { key: 't' }, text: 'On this page' }),
      h('ul', { dataset: { key: 'l' } }, items.map((x) => h('li', { dataset: { key: x.id } }, h('a', { href: docHash(D.project, D.file.path, x.id), class: 'lv' + (x.level - minL), dataset: { id: x.id }, text: x.text,
        onclick: (e) => { e.preventDefault(); const t = document.getElementById('doc-' + x.id); if (t) t.scrollIntoView({ block: 'start' }); history.replaceState(null, '', docHash(D.project, D.file.path, x.id)); } })))));
    if (tocObserver) tocObserver.disconnect();
    if (!('IntersectionObserver' in window)) return;
    const visible = new Set();
    tocObserver = new IntersectionObserver((ents) => {
      for (const en of ents) { if (en.isIntersecting) visible.add(en.target.id); else visible.delete(en.target.id); }
      const hs = items.map((x) => document.getElementById('doc-' + x.id)).filter(Boolean);
      let cur = hs.find((x) => visible.has(x.id));
      if (!cur) { const above = hs.filter((x) => x.getBoundingClientRect().top < 120); cur = above[above.length - 1]; }
      for (const a of V.toc.querySelectorAll('a')) a.classList.toggle('on', !!cur && 'doc-' + a.dataset.id === cur.id);
    }, { rootMargin: '-64px 0px -66% 0px' });
    for (const x of items) { const t = document.getElementById('doc-' + x.id); if (t) tocObserver.observe(t); }
  }
})();
