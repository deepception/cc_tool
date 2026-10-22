/* cc-dash Markdown renderer: pure functions, no DOM, no network, so tests run it in node.

   render(src, ctx) -> { front, body, headings }
     front     [[key, nodes]] from YAML front matter (--- ... --- at the top)
     body      a tree of plain objects { t: tag, a: {attrs}, c: [children] }
               and strings (text). docs.js turns it into elements with
               createElement/textContent, so nothing here is ever parsed as
               HTML: raw HTML in the source stays visible text, by design.
     headings  [{ level, text, id }] for the table of contents.

   Covers CommonMark blocks (ATX and setext headings, paragraphs, fenced and
   indented code, block quotes, ordered, bullet and nested lists, thematic
   breaks, link reference definitions), GFM tables, task lists (read-only),
   strikethrough and bare-URL autolinks, inline code, emphasis (the
   CommonMark delimiter algorithm), links, images, <autolinks>, entities,
   hard breaks, Obsidian [[wikilinks]] ([[note]], [[note|alias]],
   [[note#heading]], ![[image.png]]) and front matter. HTML comments are
   dropped; other raw HTML is shown as text.

   Links: http(s) and mailto open in a new tab (rel="noopener noreferrer");
   any other scheme (javascript:, data:, vbscript:, file:, ...) is dropped
   and only the link text stays. Relative links go through ctx:
     ctx.path            the document's path in the project ('docs/a.md')
     ctx.docHref(p, h)   href that opens project file p at heading h in the viewer
     ctx.wiki(name, h)   { path } for a wikilink target, or null (shown as missing)
     ctx.isFile(p)       optional: false marks a relative link as missing
   Images become { t: 'img', a: { 'data-asset': path } } for docs.js to fetch
   with the token; images on the network are not loaded (a link instead).
   Heading ids are 'doc-' + a GitHub-style slug, so they never collide with
   the dashboard's own ids. Exposes window.ccMarkdown (globalThis in node). */
'use strict';
(() => {
  const DOC_RE = /\.(md|markdown|html?|txt)$/i;
  const IMG_RE = /\.(png|jpe?g|gif|webp|svg|avif|bmp|ico)$/i;
  const PUNCT = /[!-/:-@[-`{-~ -⁯⸀-⹿　-〿]/;
  const WS = /\s/;
  const ENT = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', copy: '©', reg: '®',
    trade: '™', hellip: '…', mdash: '—', ndash: '–', rarr: '→', larr: '←', uarr: '↑',
    darr: '↓', harr: '↔', times: '×', divide: '÷', middot: '·', bull: '•', deg: '°',
    plusmn: '±', laquo: '«', raquo: '»', lsquo: '‘', rsquo: '’', ldquo: '“', rdquo: '”',
    para: '¶', sect: '§', check: '✓', cross: '✗', le: '≤', ge: '≥', ne: '≠', infin: '∞',
    euro: '€', pound: '£', yen: '¥', cent: '¢', shy: '­', zwj: '‍', zwnj: '‌', ensp: ' ', emsp: ' ', thinsp: ' ' };

  const el = (t, a, c) => ({ t, a: a || {}, c: c || [] });

  // ── helpers ────────────────────────────────────────────────────────────
  function decodeEntity(s) {
    const m = /^&(?:#(\d{1,7})|#[xX]([0-9a-fA-F]{1,6})|([a-zA-Z][a-zA-Z0-9]{1,31}));/.exec(s);
    if (!m) return null;
    let ch;
    if (m[1] || m[2]) {
      const cp = parseInt(m[1] || m[2], m[1] ? 10 : 16);
      ch = cp > 0 && cp <= 0x10ffff && !(cp >= 0xd800 && cp <= 0xdfff) ? String.fromCodePoint(cp) : '�';
    } else if (Object.prototype.hasOwnProperty.call(ENT, m[3])) ch = ENT[m[3]];
    else return null;
    return [ch, m[0].length];
  }
  function unescapeText(s) {
    let out = '';
    for (let i = 0; i < s.length; i++) {
      const c = s[i];
      if (c === '\\' && i + 1 < s.length && PUNCT.test(s[i + 1])) { out += s[++i]; continue; }
      if (c === '&') { const e = decodeEntity(s.slice(i)); if (e) { out += e[0]; i += e[1] - 1; continue; } }
      out += c;
    }
    return out;
  }
  function plain(nodes) {
    return nodes.map((n) => (typeof n === 'string' ? n : n.t === 'img' ? (n.a.alt || '') : plain(n.c))).join('');
  }
  function slug(text) {
    return text.toLowerCase().trim().replace(/[^\p{L}\p{N}\s_-]/gu, '').replace(/\s/g, '-');
  }
  function normLabel(s) { return s.trim().replace(/\s+/g, ' ').toLowerCase(); }

  /* 'http' | 'mailto' | 'rel' | null (a scheme we drop). Browsers ignore tabs,
     newlines and leading control characters in a scheme, so they go first. */
  function scheme(url) {
    const u = url.replace(/[\u0000- \u007f-\u009f]+/g, '');
    if (/^\/\//.test(u)) return 'http';
    const m = /^([a-zA-Z][a-zA-Z0-9+.-]*):/.exec(u);
    if (!m) return /^[a-zA-Z][a-zA-Z0-9+.-]*&#|^[a-zA-Z]+%3a/i.test(u) ? null : 'rel';
    const s = m[1].toLowerCase();
    if (s === 'http' || s === 'https') return 'http';
    if (s === 'mailto') return 'mailto';
    return null;
  }
  function dirOf(p) { const i = p.lastIndexOf('/'); return i < 0 ? '' : p.slice(0, i); }
  function joinPath(base, rel) {
    const out = rel.startsWith('/') ? [] : (base ? base.split('/') : []);
    for (const seg of rel.split('/')) {
      if (seg === '' || seg === '.') continue;
      if (seg === '..') { if (!out.length) return null; out.pop(); } else out.push(seg);
    }
    return out.join('/');
  }
  function safeDecode(s) { try { return decodeURIComponent(s); } catch (e) { return s; } }

  // ── front matter ───────────────────────────────────────────────────────
  function frontMatter(lines) {
    if (lines[0] !== '---' && lines[0] !== '---\r') return null;
    let end = -1;
    for (let i = 1; i < Math.min(lines.length, 400); i++) if (/^(---|\.\.\.)\s*$/.test(lines[i])) { end = i; break; }
    if (end < 0) return null;
    const pairs = [];
    for (const raw of lines.slice(1, end)) {
      if (!raw.trim() || /^\s*#/.test(raw)) continue;
      const item = /^\s+-\s+(.*)$/.exec(raw) || /^-\s+(.*)$/.exec(raw);
      const kv = /^([A-Za-z0-9_][^:]{0,80}):(?:\s+(.*)|\s*)$/.exec(raw);
      if (kv && !/^\s/.test(raw)) { pairs.push([kv[1].trim(), kv[2] != null ? [unquote(kv[2])] : []]); continue; }
      if (!pairs.length) continue;
      const last = pairs[pairs.length - 1][1];
      if (item) last.push(unquote(item[1]));
      else last.push(raw.trim());
    }
    return { pairs, end };
  }
  function unquote(v) {
    v = v.trim();
    if (v.length > 1 && ((v[0] === '"' && v.endsWith('"')) || (v[0] === "'" && v.endsWith("'")))) v = v.slice(1, -1);
    if (/^\[.*\]$/.test(v) && !/^\[\[/.test(v)) return v.slice(1, -1).split(',').map((x) => unquote(x)).filter(Boolean).join(', ');
    return v;
  }

  // ── block structure ────────────────────────────────────────────────────
  const RE = {
    fence: /^( {0,3})(`{3,}|~{3,})[ \t]*(.*)$/,
    hr: /^ {0,3}((\*[ \t]*){3,}|(-[ \t]*){3,}|(_[ \t]*){3,})$/,
    quote: /^ {0,3}> ?/,
    bullet: /^( {0,3})([-+*])([ \t]+|$)(.*)$/,
    ordered: /^( {0,3})(\d{1,9})([.)])([ \t]+|$)(.*)$/,
    setext1: /^ {0,3}=+[ \t]*$/,
    setext2: /^ {0,3}-+[ \t]*$/,
    delimRow: /^ {0,3}\|?[ \t]*:?-+:?[ \t]*(\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$/,
    ref: /^ {0,3}\[((?:[^\]\\]|\\.){1,999})\]:[ \t]*(<[^>\n]*>|\S+)(?:[ \t]+("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\((?:[^)\\]|\\.)*\)))?[ \t]*$/,
  };
  /* ATX heading without a regex over the rest of the line (a lazy .*? before [ \t]*$
     is quadratic on a long line of spaces). */
  function atx(line) {
    const m = /^ {0,3}(#{1,6})(?=[ \t]|$)/.exec(line);
    if (!m) return null;
    let t = line.slice(m[0].length).trim();
    let e = t.length;
    while (e > 0 && t[e - 1] === '#') e--;
    if (e < t.length && (e === 0 || t[e - 1] === ' ' || t[e - 1] === '\t')) t = t.slice(0, e).trim();
    return { level: m[1].length, text: t };
  }
  const indentOf = (s) => { let n = 0; while (n < s.length && s[n] === ' ') n++; return n; };
  const blank = (s) => /^[ \t]*$/.test(s);
  function listMarker(line) {
    let m = RE.bullet.exec(line);
    if (m && !RE.hr.test(line)) return { ordered: false, ch: m[2], ind: m[1].length, w: m[1].length + 1, sp: m[3], rest: m[4] };
    m = RE.ordered.exec(line);
    if (m) return { ordered: true, ch: m[3], start: parseInt(m[2], 10), ind: m[1].length, w: m[1].length + m[2].length + 1, sp: m[4], rest: m[5] };
    return null;
  }
  function splitRow(line) {
    let s = line.trim();
    if (s.startsWith('|')) s = s.slice(1);
    if (s.endsWith('|') && !s.endsWith('\\|')) s = s.slice(0, -1);
    const cells = [];
    let cur = '', code = 0;
    for (let i = 0; i < s.length; i++) {
      const c = s[i];
      if (c === '\\' && s[i + 1] === '|') { cur += '|'; i++; continue; }
      if (c === '`') code ^= 1;
      if (c === '|' && !code) { cells.push(cur.trim()); cur = ''; continue; }
      cur += c;
    }
    cells.push(cur.trim());
    return cells;
  }
  function isTableStart(lines, i) {
    const a = lines[i], b = lines[i + 1];
    if (b == null || !a.includes('|') || !RE.delimRow.test(b) || (!b.includes('|') && !b.includes('-'))) return false;
    if (!b.includes('|') && splitRow(a).length > 1) return false;
    return splitRow(a).length === splitRow(b).length;
  }
  function interrupts(line, lines, i) {
    if (atx(line) || RE.fence.test(line) || RE.hr.test(line) || RE.quote.test(line) || /^ {0,3}<!--/.test(line)) return true;
    const m = listMarker(line);
    if (m && m.rest.trim() && (!m.ordered || m.start === 1)) return true;
    return false;
  }

  function parseBlocks(lines, st, depth) {
    const out = [];
    if (depth > 40) { out.push({ type: 'para', text: lines.join('\n') }); return out; }
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (blank(line)) { i++; continue; }
      const ind = indentOf(line);
      if (ind >= 4) {                                   // indented code
        const buf = [];
        while (i < lines.length && (blank(lines[i]) || indentOf(lines[i]) >= 4)) { buf.push(lines[i].slice(Math.min(4, indentOf(lines[i])))); i++; }
        while (buf.length && blank(buf[buf.length - 1])) buf.pop();
        out.push({ type: 'code', lang: '', text: buf.join('\n') });
        continue;
      }
      let m = RE.fence.exec(line);
      if (m && !(m[2][0] === '`' && m[3].includes('`'))) {
        const fch = m[2][0], flen = m[2].length, fin = m[1].length;
        const lang = unescapeText(m[3].trim().split(/\s+/)[0] || '');
        const buf = [];
        i++;
        while (i < lines.length) {
          const c = new RegExp('^ {0,3}' + (fch === '`' ? '`' : '~') + '{' + flen + ',}[ \\t]*$').exec(lines[i]);
          if (c) { i++; break; }
          const l = lines[i];
          buf.push(l.slice(Math.min(fin, indentOf(l))));
          i++;
        }
        out.push({ type: 'code', lang, text: buf.join('\n') });
        continue;
      }
      if (/^ {0,3}<!--/.test(line)) {                     // HTML comment: shown as nothing
        while (i < lines.length && !lines[i].includes('-->')) i++;
        i++;
        continue;
      }
      const hd = atx(line);
      if (hd) { out.push({ type: 'heading', level: hd.level, text: hd.text }); i++; continue; }
      if (RE.hr.test(line)) { out.push({ type: 'hr' }); i++; continue; }
      if (RE.quote.test(line)) {
        const buf = [];
        let lastPara = false;
        while (i < lines.length) {
          const l = lines[i];
          if (RE.quote.test(l)) { const s = l.replace(RE.quote, ''); buf.push(s); lastPara = !blank(s) && !RE.fence.test(s); i++; continue; }
          if (lastPara && !blank(l) && !interrupts(l, lines, i)) { buf.push(l); i++; continue; }
          break;
        }
        out.push({ type: 'quote', children: parseBlocks(buf, st, depth + 1) });
        continue;
      }
      const lm = listMarker(line);
      if (lm) { i = parseList(lines, i, lm, st, depth, out); continue; }
      if (isTableStart(lines, i)) {
        const align = splitRow(lines[i + 1]).map((c) => (/^:-+:$/.test(c) ? 'center' : /-+:$/.test(c) ? 'right' : /^:-+/.test(c) ? 'left' : ''));
        const head = splitRow(lines[i]);
        const rows = [];
        i += 2;
        while (i < lines.length && !blank(lines[i]) && !interrupts(lines[i], lines, i) && rows.length < 5000) {
          const r = splitRow(lines[i]);
          while (r.length < head.length) r.push('');
          rows.push(r.slice(0, head.length));
          i++;
        }
        out.push({ type: 'table', align, head, rows });
        continue;
      }
      // paragraph (with link reference definitions at its start, and setext headings)
      const buf = [line];
      i++;
      while (i < lines.length) {
        const l = lines[i];
        if (blank(l)) break;
        if (RE.setext1.test(l) || RE.setext2.test(l)) {
          const level = RE.setext1.test(l) ? 1 : 2;
          const text = takeRefs(buf, st).join('\n').trim();
          if (text) out.push({ type: 'heading', level, text });
          else if (level === 2) out.push({ type: 'hr' });
          buf.length = 0;
          i++;
          break;
        }
        if (interrupts(l, lines, i) || isTableStart(lines, i)) break;
        buf.push(l);
        i++;
      }
      if (buf.length) {
        const text = takeRefs(buf, st).map((l) => l.replace(/^[ \t]+/, '')).join('\n');
        if (text.trim()) out.push({ type: 'para', text });
      }
    }
    return out;
  }
  function takeRefs(buf, st) {
    let k = 0;
    while (k < buf.length) {
      const m = RE.ref.exec(buf[k]);
      if (!m) break;
      const label = normLabel(m[1]);
      if (label && !(label in st.refs)) {
        let dest = m[2];
        if (dest.startsWith('<')) dest = dest.slice(1, -1);
        st.refs[label] = { href: unescapeText(dest), title: m[3] ? unescapeText(m[3].slice(1, -1)) : '' };
      }
      k++;
    }
    return buf.slice(k);
  }
  function parseList(lines, i, first, st, depth, out) {
    const list = { type: 'list', ordered: first.ordered, start: first.start || 1, tight: true, items: [] };
    let sawBlankBetween = false;
    let cur = first;
    while (cur) {
      // content offset: marker width plus 1-4 spaces; 5+ spaces means indented code (one space counts)
      const spaces = cur.sp.length;
      let contentOff, firstLine;
      if (!cur.rest.trim()) { contentOff = cur.w + 1; firstLine = ''; }
      else if (spaces > 4) { contentOff = cur.w + 1; firstLine = ' '.repeat(spaces - 1) + cur.rest; }
      else { contentOff = cur.w + spaces; firstLine = cur.rest; }
      const itemLines = [firstLine];
      i++;
      let blankRun = false;
      let next = null;
      while (i < lines.length) {
        const l = lines[i];
        if (blank(l)) { itemLines.push(''); blankRun = true; i++; continue; }
        const ind = indentOf(l);
        if (ind >= contentOff) { itemLines.push(l.slice(contentOff)); blankRun = false; i++; continue; }
        const m = listMarker(l);
        if (m && m.ordered === first.ordered && m.ch === first.ch && m.ind < contentOff) { next = m; break; }
        if (!blankRun && !interrupts(l, lines, i) && !(m) && lastIsPara(itemLines)) { itemLines.push(l.trim()); i++; continue; }
        break;
      }
      // trailing blanks are not part of the item; a blank before the next item loosens the list
      let trail = 0;
      while (itemLines.length && blank(itemLines[itemLines.length - 1])) { itemLines.pop(); trail++; }
      if (trail && next) sawBlankBetween = true;
      let task = null;
      const tm = /^\[([ xX])\][ \t]+/.exec(itemLines[0] || '');
      if (tm) { task = tm[1] !== ' '; itemLines[0] = itemLines[0].slice(tm[0].length); }
      const children = parseBlocks(itemLines, st, depth + 1);
      // blank lines between two blocks of one item also loosen the list (fence state in one pass)
      if (children.length > 1) {
        let inCode = false;
        for (let k = 0; k < itemLines.length - 1; k++) {
          if (RE.fence.test(itemLines[k])) inCode = !inCode;
          if (k > 0 && !inCode && blank(itemLines[k]) && !blank(itemLines[k + 1]) && !blank(itemLines[k - 1])) { list.tight = false; break; }
        }
      }
      list.items.push({ task, children });
      if (!next) break;
      cur = next;
    }
    if (sawBlankBetween) list.tight = false;
    out.push(list);
    return i;
  }
  function lastIsPara(itemLines) {
    const l = itemLines[itemLines.length - 1];
    return l != null && !blank(l) && !RE.fence.test(l) && indentOf(l) < 4;
  }

  // ── inline ─────────────────────────────────────────────────────────────
  function parseInline(text, ctx, st, noLinks) {
    const toks = [];
    let buf = '';
    const flush = () => { if (buf) { toks.push({ s: buf }); buf = ''; } };
    const push = (node) => { flush(); toks.push({ node }); };
    let i = 0;
    const n = text.length;
    /* Bounds that keep hostile input linear: a code-span opener of length L that found
       no closer from position p has none from any later position either; a '[' with no
       ']' ahead (or none within 2000 characters) is not a link. */
    const noCloser = {};
    let nextClose = -2;
    while (i < n) {
      const c = text[i];
      if (c === '\\') {
        if (text[i + 1] === '\n') { push(el('br')); i += 2; continue; }
        if (i + 1 < n && PUNCT.test(text[i + 1])) { buf += text[i + 1]; i += 2; continue; }
        buf += c; i++; continue;
      }
      if (c === '`') {
        let k = i; while (text[k] === '`') k++;
        const run = k - i;
        let j = k, found = -1;
        if (noCloser[run] !== undefined && i >= noCloser[run]) { buf += text.slice(i, k); i = k; continue; }
        while (j < n) {
          const p = text.indexOf('`', j);
          if (p < 0) break;
          let q = p; while (text[q] === '`') q++;
          if (q - p === run) { found = p; break; }
          j = q;
        }
        if (found < 0) { noCloser[run] = i; buf += text.slice(i, k); i = k; continue; }
        let code = text.slice(k, found).replace(/\n/g, ' ');
        if (code.length > 2 && code[0] === ' ' && code[code.length - 1] === ' ' && code.trim()) code = code.slice(1, -1);
        push(el('code', {}, [code]));
        i = found + run;
        continue;
      }
      if (c === '\n') {
        // Trailing whitespace is read from `text` (flat), and buf is flushed at every line
        // end, so it only ever holds one line: indexing a long concatenated string would
        // flatten it on every newline (quadratic).
        let sp = i;
        while (sp > 0 && text[sp - 1] === ' ') sp--;
        const hard = i - sp >= 2;
        let e = i;
        while (e > 0 && (text[e - 1] === ' ' || text[e - 1] === '\t')) e--;
        if (e < i) buf = buf.slice(0, Math.max(0, buf.length - (i - e)));
        if (hard) push(el('br')); else { buf += '\n'; flush(); }
        i++;
        continue;
      }
      if (c === '&') { const e = decodeEntity(text.slice(i, i + 40)); if (e) { buf += e[0]; i += e[1]; continue; } buf += c; i++; continue; }
      if (c === '<') {
        const m = /^<([a-zA-Z][a-zA-Z0-9+.-]{1,31}:[^\s<>]*)>/.exec(text.slice(i, i + 2100));
        if (m) { push(linkNode(m[1], [m[1]], '', ctx, true)); i += m[0].length; continue; }
        const e = /^<([a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+)>/.exec(text.slice(i, i + 400));
        if (e) { push(linkNode('mailto:' + e[1], [e[1]], '', ctx, true)); i += e[0].length; continue; }
        buf += c; i++; continue;
      }
      if (!noLinks && (c === '[' || (c === '!' && text[i + 1] === '['))) {
        const img = c === '!';
        const s = img ? i + 1 : i;
        if (text[s + 1] === '[') {                       // wikilink [[target#heading|alias]]
          const w = text.slice(s + 2, s + 404).indexOf(']]');
          const end = w < 0 ? -1 : s + 2 + w;
          const inner = end > 0 ? text.slice(s + 2, end) : '';
          if (end > 0 && inner && !/[\n[\]]/.test(inner) && inner.length < 400) {
            push(wikiNode(inner, img, ctx));
            i = end + 2;
            continue;
          }
        }
        if (nextClose !== -1 && nextClose < s) nextClose = text.indexOf(']', s);
        const close = nextClose < 0 || nextClose > s + 2000 ? -1 : matchBracket(text, s);
        if (close > 0) {
          const label = text.slice(s + 1, close);
          let r = null;
          if (text[close + 1] === '(') r = parseDest(text, close + 1);
          let consumed = r ? r.end : -1;
          if (!r) {
            let refKey = null;
            const m = /^\[((?:[^\]\\]|\\.){0,999})\]/.exec(text.slice(close + 1, close + 1100));
            if (m) { refKey = m[1] ? m[1] : label; consumed = close + 1 + m[0].length; } else { refKey = label; consumed = close + 1; }
            const ref = st.refs[normLabel(refKey)];
            if (ref) r = { href: ref.href, title: ref.title };
          }
          if (r) {
            if (img) push(imageNode(r.href, plain(parseInline(label, ctx, st, true)), r.title, ctx));
            else push(linkNode(r.href, parseInline(label, ctx, st, true), r.title, ctx, false));
            i = consumed;
            continue;
          }
        }
        buf += c; i++; continue;
      }
      if (c === '*' || c === '_' || c === '~') {
        let k = i; while (text[k] === c) k++;
        const cnt = k - i;
        if (c === '~' && cnt > 2) { buf += text.slice(i, k); i = k; continue; }
        const before = i > 0 ? text[i - 1] : '\n';
        const after = k < n ? text[k] : '\n';
        const ws = (ch) => WS.test(ch), pu = (ch) => PUNCT.test(ch);
        const left = !ws(after) && (!pu(after) || ws(before) || pu(before));
        const right = !ws(before) && (!pu(before) || ws(after) || pu(after));
        let open, closeOk;
        if (c === '_') { open = left && (!right || pu(before)); closeOk = right && (!left || pu(after)); } else { open = left; closeOk = right; }
        flush();
        toks.push({ d: c, cnt, orig: cnt, open, close: closeOk });
        i = k;
        continue;
      }
      if (!noLinks && (c === 'h' || c === 'w' || c === 'H' || c === 'W') && (i === 0 || /[\s*_~(]/.test(text[i - 1]))) {
        const m = /^(?:https?:\/\/|www\.)[^\s<]+/i.exec(text.slice(i, i + 2100));
        if (m) {
          let url = m[0];
          let e = url.length;
          while (e > 0 && `?!.,:*_~'"`.includes(url[e - 1])) e--;
          url = url.slice(0, e);
          let open = 0, shut = 0;
          for (const ch of url) { if (ch === '(') open++; else if (ch === ')') shut++; }
          while (url.endsWith(')') && open < shut) { url = url.slice(0, -1); shut--; }
          if (url.replace(/^(https?:\/\/|www\.)/i, '').length > 0 && /[a-z0-9]/i.test(url.replace(/^(https?:\/\/|www\.)/i, ''))) {
            push(linkNode(/^www\./i.test(url) ? 'https://' + url : url, [url], '', ctx, true));
            i += url.length;
            continue;
          }
        }
      }
      buf += c;
      i++;
    }
    flush();
    if (toks.filter((t) => t.d).length <= 1000) emphasis(toks);
    return toNodes(toks);
  }
  function matchBracket(text, s) {
    let depth = 0;
    const lim = Math.min(text.length, s + 2000);
    for (let j = s; j < lim; j++) {
      const c = text[j];
      if (c === '\\') { j++; continue; }
      if (c === '`') { let k = j; while (text[k] === '`') k++; const run = text.slice(j, k); const e = text.slice(k, lim).indexOf(run); if (e >= 0) { j = k + e + run.length - 1; continue; } j = k - 1; continue; }
      if (c === '[') { if (++depth > 32) return -1; }
      else if (c === ']') { depth--; if (depth === 0) return j; }
    }
    return -1;
  }
  function parseDest(text, p) {
    let j = p + 1;
    const n = text.length;
    while (j < n && /[ \t\n]/.test(text[j])) j++;
    let href = '';
    if (text[j] === '<') {
      const e = text.indexOf('>', j);
      if (e < 0 || text.slice(j, e).includes('\n')) return null;
      href = text.slice(j + 1, e);
      j = e + 1;
    } else {
      let depth = 0;
      const s = j;
      while (j < n) {
        const c = text[j];
        if (c === '\\' && j + 1 < n) { j += 2; continue; }
        if (/[\s\u0000-\u001f]/.test(c)) break;
        if (c === '(') depth++;
        if (c === ')') { if (depth === 0) break; depth--; }
        j++;
      }
      href = text.slice(s, j);
    }
    while (j < n && /[ \t\n]/.test(text[j])) j++;
    let title = '';
    const q = text[j];
    if (q === '"' || q === "'" || q === '(') {
      const endCh = q === '(' ? ')' : q;
      let k = j + 1;
      while (k < n && text[k] !== endCh) { if (text[k] === '\\') k++; k++; }
      if (k >= n) return null;
      title = text.slice(j + 1, k);
      j = k + 1;
      while (j < n && /[ \t\n]/.test(text[j])) j++;
    }
    if (text[j] !== ')') return null;
    return { href: unescapeText(href), title: unescapeText(title), end: j + 1 };
  }

  /* CommonMark "process emphasis" over a token list: {s} text, {node}, {d} delimiter runs. */
  function emphasis(toks) {
    let k = 0;
    // openers_bottom: below this index no opener can match a closer of this kind, so a
    // closer that found nothing stops the next search of its kind there (linear).
    const bottom = {};
    while (k < toks.length) {
      const closer = toks[k];
      if (!closer.d || !closer.close || closer.cnt === 0) { k++; continue; }
      const kind = closer.d + (closer.open ? 1 : 0) + (closer.orig % 3);
      let oi = -1;
      for (let j = k - 1; j >= (bottom[kind] || 0); j--) {
        const o = toks[j];
        if (!o.d || o.d !== closer.d || !o.open || o.cnt === 0) continue;
        if (closer.d === '~' && o.cnt !== closer.cnt) continue;
        if ((o.close || closer.open) && (o.orig + closer.orig) % 3 === 0 && !(o.orig % 3 === 0 && closer.orig % 3 === 0) && closer.d !== '~') continue;
        oi = j;
        break;
      }
      if (oi < 0) { bottom[kind] = k; if (!closer.open) closer.close = false; k++; continue; }
      for (const b in bottom) if (bottom[b] > oi) bottom[b] = oi;
      const opener = toks[oi];
      const use = closer.d === '~' ? closer.cnt : (closer.cnt >= 2 && opener.cnt >= 2 ? 2 : 1);
      const tag = closer.d === '~' ? 'del' : use === 2 ? 'strong' : 'em';
      opener.cnt -= use;
      closer.cnt -= use;
      const inner = toks.slice(oi + 1, k).map((t) => (t.d ? { s: t.d.repeat(t.cnt) } : t));
      const wrap = { node: el(tag, {}, toNodes(inner)) };
      toks.splice(oi + 1, k - oi - 1, wrap);
      k = oi + 2;
      if (opener.cnt === 0) { toks.splice(oi, 1); k--; }
      if (closer.cnt === 0) { toks.splice(k, 1); }
    }
  }
  function toNodes(toks) {
    const out = [];
    for (const t of toks) {
      const s = t.d ? t.d.repeat(t.cnt) : t.s;
      if (s != null) { if (!s) continue; if (typeof out[out.length - 1] === 'string') out[out.length - 1] += s; else out.push(s); } else out.push(t.node);
    }
    return out;
  }

  // ── links, images, wikilinks ──────────────────────────────────────────
  function off(kids, why) { return el('span', { class: 'md-off', title: why }, kids); }
  function linkNode(href, kids, title, ctx, auto) {
    const raw = href.trim();
    const sc = scheme(raw);
    if (sc === null) return off(kids, 'Link removed: only http, https and mailto links open from the viewer');
    if (sc === 'http' || sc === 'mailto') {
      const h = /^\/\//.test(raw) ? 'https:' + raw : raw;
      const a = { href: h, target: '_blank', rel: 'noopener noreferrer', class: 'md-ext' };
      if (title) a.title = title;
      return el('a', a, kids);
    }
    // relative: a heading in this doc, or another file in the project
    const hashAt = raw.indexOf('#');
    const pathPart = (hashAt >= 0 ? raw.slice(0, hashAt) : raw).replace(/\?.*$/, '');
    const frag = hashAt >= 0 ? safeDecode(raw.slice(hashAt + 1)) : '';
    if (!pathPart) return el('a', { href: ctx.docHref(ctx.path, frag), class: 'md-anchor' }, kids);
    const target = joinPath(dirOf(ctx.path || ''), safeDecode(pathPart));
    if (target == null) return off(kids, 'Outside the project');
    if (DOC_RE.test(target)) {
      const missing = ctx.isFile && !ctx.isFile(target);
      const a = { href: ctx.docHref(target, frag), 'data-doc': target };
      if (missing) a.class = 'md-missing';
      if (title || missing) a.title = missing ? 'Not found in this project: ' + target : title;
      return el('a', a, kids);
    }
    return off(kids, 'Not a document: ' + target);
  }
  function imageNode(src, alt, title, ctx) {
    const raw = src.trim();
    const sc = scheme(raw);
    if (/^data:image\/(png|jpe?g|gif|webp|avif|bmp);base64,[a-z0-9+/=\s]+$/i.test(raw)) return el('img', { src: raw.replace(/\s/g, ''), alt, title: title || null });
    if (sc === 'http') return el('a', { href: raw, target: '_blank', rel: 'noopener noreferrer', class: 'md-ext md-imglink', title: 'Images on the network are not loaded' }, ['Image: ' + (alt || raw)]);
    if (sc !== 'rel') return off([alt || 'image'], 'Image removed');
    const p = joinPath(dirOf(ctx.path || ''), safeDecode(raw.replace(/[?#].*$/, '')));
    if (p == null || !IMG_RE.test(p)) return off([alt || 'image'], 'Not an image in this project');
    return el('img', { 'data-asset': p, alt: alt || '', title: title || null, loading: 'lazy' });
  }
  function wikiNode(inner, embed, ctx) {
    let target = inner, alias = '';
    const bar = inner.indexOf('|');
    if (bar >= 0) { target = inner.slice(0, bar); alias = inner.slice(bar + 1).trim(); }
    let heading = '';
    const hash = target.indexOf('#');
    if (hash >= 0) { heading = target.slice(hash + 1).replace(/^\^/, '').trim(); target = target.slice(0, hash); }
    target = target.trim();
    if (embed && IMG_RE.test(target)) {
      const dir = dirOf(ctx.path || '');
      const p = target.includes('/') ? joinPath('', target) : joinPath(dir, target);
      if (p) return el('img', { 'data-asset': p, 'data-wiki': target, alt: alias && !/^\d+(x\d+)?$/.test(alias) ? alias : target, loading: 'lazy' });
    }
    const label = alias || (target ? target + (heading ? ' > ' + heading : '') : heading);
    if (!target) return el('a', { href: ctx.docHref(ctx.path, slug(heading)), class: 'md-wiki' }, [label]);
    const hit = ctx.wiki ? ctx.wiki(target, heading) : null;
    if (!hit) return el('a', { class: 'md-wiki md-missing', title: 'No note named "' + target + '" in this project' }, [label]);
    return el('a', { href: ctx.docHref(hit.path, heading ? slug(heading) : ''), class: 'md-wiki', 'data-doc': hit.path }, [label]);
  }

  // ── blocks to nodes ────────────────────────────────────────────────────
  function toTree(blocks, ctx, st, tight) {
    const out = [];
    for (const b of blocks) {
      if (b.type === 'para') { const kids = parseInline(b.text, ctx, st); if (tight) out.push(...kids); else out.push(el('p', {}, kids)); continue; }
      if (b.type === 'heading') {
        const kids = parseInline(b.text.trim(), ctx, st);
        const text = plain(kids).trim();
        let base = slug(text) || 'section';
        let id = base, k = 1;
        while (st.ids.has(id)) id = base + '-' + (k++);
        st.ids.add(id);
        st.headings.push({ level: b.level, text, id });
        out.push(el('h' + b.level, { id: 'doc-' + id }, kids));
        continue;
      }
      if (b.type === 'hr') { out.push(el('hr')); continue; }
      if (b.type === 'code') {
        const lang = b.lang.replace(/[^\w+#.-]/g, '').slice(0, 32);
        const code = el('code', lang ? { class: 'lang-' + lang } : {}, [b.text]);
        out.push(el('div', { class: 'md-code' }, [lang ? el('div', { class: 'md-lang' }, [lang]) : null, el('pre', {}, [code])].filter(Boolean)));
        continue;
      }
      if (b.type === 'quote') { out.push(el('blockquote', {}, toTree(b.children, ctx, st, false))); continue; }
      if (b.type === 'list') {
        const items = b.items.map((it) => {
          const kids = toTree(it.children, ctx, st, b.tight);
          if (it.task === null) return el('li', {}, kids);
          const box = el('input', it.task ? { type: 'checkbox', disabled: '', checked: '' } : { type: 'checkbox', disabled: '' });
          return el('li', { class: 'md-task' + (it.task ? ' done' : '') }, [box, ' ', ...kids]);
        });
        const a = {};
        if (b.ordered && b.start !== 1) a.start = String(b.start);
        if (b.items.some((it) => it.task !== null)) a.class = 'md-tasks';
        out.push(el(b.ordered ? 'ol' : 'ul', a, items));
        continue;
      }
      if (b.type === 'table') {
        const cell = (tag, txt, i) => el(tag, b.align[i] ? { class: 'al-' + b.align[i] } : {}, parseInline(txt, ctx, st));
        out.push(el('div', { class: 'md-table' }, [el('table', {}, [
          el('thead', {}, [el('tr', {}, b.head.map((t, i) => cell('th', t, i)))]),
          b.rows.length ? el('tbody', {}, b.rows.map((r) => el('tr', {}, r.map((t, i) => cell('td', t, i))))) : null,
        ].filter(Boolean))]));
      }
    }
    return out;
  }

  function render(src, ctx) {
    ctx = Object.assign({ path: '', docHref: (p, h) => '#' + (h ? 'doc-' + h : ''), wiki: null, isFile: null }, ctx || {});
    let text = String(src == null ? '' : src).replace(/\r\n?/g, '\n').replace(/\u0000/g, '�');
    if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
    let lines = text.split('\n').map((l) => l.replace(/^\t+/, (m) => '    '.repeat(m.length)));
    const st = { refs: {}, ids: new Set(), headings: [] };
    let front = [];
    const fm = frontMatter(lines);
    if (fm) { front = fm.pairs.map(([k, vs]) => [k, parseInline(vs.join(', '), ctx, st)]); lines = lines.slice(fm.end + 1); }
    const blocks = parseBlocks(lines, st, 0);
    const body = toTree(blocks, ctx, st, false);
    return { front, body, headings: st.headings };
  }

  /* A plain-text document: one <pre>. */
  function renderText(src) { return { front: [], body: [el('pre', { class: 'md-plain' }, [String(src || '')])], headings: [] }; }

  (typeof window !== 'undefined' ? window : globalThis).ccMarkdown = { render, renderText, slug, scheme, plain };
})();
