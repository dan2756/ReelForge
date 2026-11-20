// ReelForge 2D runtime: the helpers a 2D episode's scene.js draws with, and the word-by-word captions drawn on top.
//
// pipeline/twod/render.py opens build/2d/index.html in a headless browser, which loads this file, the episode's
// timeline (window.RF_DATA: the voiceover's beats and every word's time) and the episode's scene.js. For each frame
// it calls window.__rf_frame(t): the scene draws the frame at time t (seconds) onto a 1080x1920 canvas, then the
// captions go on top. A scene is a pure function of t: the same t always gives the same picture, so frames can be
// rendered in any order, resumed, and previewed as stills.
//
//   scene({
//     assets: {coin: "assets/coin.png"},       // optional images (paths from the episode folder), loaded first
//     setup(rf) { ... },                       // optional, once, after the assets have loaded
//     draw(g, t, rf) { ... },                  // every frame: g is the canvas's 2D context
//   });
//
// docs/2D.md lists every helper on rf. Draw with plain canvas calls too: rf only saves typing.

(function () {
  "use strict";
  const W = 1080, H = 1920;
  const DATA = window.RF_DATA || {beats: [], words: [], duration: 60, fps: 30, captions: {}};

  // ------------------------------------------------------------------ maths
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const lerp = (a, b, p) => a + (b - a) * p;
  const inv = (a, b, x) => (b === a ? 0 : (x - a) / (b - a));
  const smooth = (x) => { x = clamp(x); return x * x * (3 - 2 * x); };
  const ease = {
    linear: (x) => clamp(x),
    in: (x) => clamp(x) ** 2,
    out: (x) => 1 - (1 - clamp(x)) ** 2,
    inOut: (x) => smooth(x),
    in3: (x) => clamp(x) ** 3,
    out3: (x) => 1 - (1 - clamp(x)) ** 3,
    inOut3: (x) => { x = clamp(x); return x < 0.5 ? 4 * x ** 3 : 1 - (-2 * x + 2) ** 3 / 2; },
    outExpo: (x) => (x >= 1 ? 1 : 1 - 2 ** (-10 * clamp(x))),
    inExpo: (x) => (x <= 0 ? 0 : 2 ** (10 * clamp(x) - 10)),
    outBack: (x, s = 1.70158) => { x = clamp(x) - 1; return 1 + (s + 1) * x ** 3 + s * x ** 2; },
    outElastic: (x) => {
      x = clamp(x);
      if (x === 0 || x === 1) return x;
      return 2 ** (-10 * x) * Math.sin((x * 10 - 0.75) * (2 * Math.PI / 3)) + 1;
    },
    outBounce: (x) => {
      x = clamp(x);
      const n = 7.5625, d = 2.75;
      if (x < 1 / d) return n * x * x;
      if (x < 2 / d) return n * (x -= 1.5 / d) * x + 0.75;
      if (x < 2.5 / d) return n * (x -= 2.25 / d) * x + 0.9375;
      return n * (x -= 2.625 / d) * x + 0.984375;
    },
  };
  const easeFn = (e) => (typeof e === "function" ? e : ease[e || "inOut"] || ease.inOut);

  // map(t, t0, t1, a, b, ease): a before t0, b after t1, eased in between
  const map = (t, t0, t1, a = 0, b = 1, e = "inOut") => lerp(a, b, easeFn(e)(inv(t0, t1, t)));
  // tween(t, [[t0, v0], [t1, v1], ...], ease): a value through keyframes (numbers, or arrays of numbers)
  function tween(t, keys, e = "inOut") {
    if (!keys.length) return 0;
    if (t <= keys[0][0]) return keys[0][1];
    for (let i = 1; i < keys.length; i++) {
      const [t1, v1, e1] = keys[i];
      if (t <= t1) {
        const [t0, v0] = keys[i - 1];
        const p = easeFn(e1 || e)(inv(t0, t1, t));
        return Array.isArray(v0) ? v0.map((v, k) => lerp(v, v1[k], p)) : lerp(v0, v1, p);
      }
    }
    return keys[keys.length - 1][1];
  }
  // pulse(t, at, len): 0 -> 1 -> 0 around a moment (a hit, a flash)
  const pulse = (t, at, len = 0.4) => { const x = (t - at) / len; return x < 0 || x > 1 ? 0 : Math.sin(Math.PI * x); };
  // pop(t, at): a scale that overshoots and settles, for things appearing
  const pop = (t, at, len = 0.45) => (t < at ? 0 : ease.outBack(inv(at, at + len, t), 2.2));
  const wave = (t, hz = 1, amp = 1, phase = 0) => Math.sin((t * hz + phase) * 2 * Math.PI) * amp;

  // A seeded random number generator: rand(seed)() -> [0, 1), the same numbers every frame
  function rand(seed = 1) {
    let a = (seed * 2654435761) >>> 0 || 1;
    return function () {
      a |= 0; a = (a + 0x6d2b79f5) | 0;
      let r = Math.imul(a ^ (a >>> 15), 1 | a);
      r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
      return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
    };
  }
  // Smooth 1D noise in [-1, 1], for wobble and camera drift
  function noise(x, seed = 0) {
    const h = (n) => { const s = Math.sin(n * 127.1 + seed * 311.7) * 43758.5453; return s - Math.floor(s); };
    const i = Math.floor(x), f = x - i, u = f * f * (3 - 2 * f);
    return (lerp(h(i), h(i + 1), u)) * 2 - 1;
  }

  // ------------------------------------------------------------------ colour
  function hex(c) {
    if (Array.isArray(c)) return c;
    const m = /rgba?\(([^)]+)\)/.exec(c);
    if (m) return m[1].split(",").slice(0, 3).map((v) => parseFloat(v));
    c = c.replace("#", "");
    if (c.length === 3) c = c.split("").map((x) => x + x).join("");
    return [parseInt(c.slice(0, 2), 16), parseInt(c.slice(2, 4), 16), parseInt(c.slice(4, 6), 16)];
  }
  const rgba = (c, a = 1) => { const [r, g, b] = Array.isArray(c) ? c : hex(c); return `rgba(${r | 0},${g | 0},${b | 0},${a})`; };
  // mix("#ff0000", "#0000ff", 0.5) -> a colour between two
  const mix = (a, b, p) => { const x = hex(a), y = hex(b); return rgba(x.map((v, i) => lerp(v, y[i], clamp(p)))); };
  const shade = (c, amount) => (amount >= 0 ? mix(c, "#ffffff", amount) : mix(c, "#000000", -amount));

  // ------------------------------------------------------------------ drawing
  function paint(g, o) {
    if (o.shadow) {
      g.shadowColor = o.shadow.color || "rgba(0,0,0,0.35)";
      g.shadowBlur = o.shadow.blur ?? 24;
      g.shadowOffsetX = o.shadow.x ?? 0;
      g.shadowOffsetY = o.shadow.y ?? 10;
    }
    if (o.fill) { g.fillStyle = o.fill; g.fill(); }
    g.shadowColor = "transparent";
    if (o.stroke) {
      g.lineWidth = o.lw ?? 8;
      g.strokeStyle = o.stroke;
      g.lineJoin = "round";
      g.lineCap = "round";
      g.stroke();
    }
  }
  function circle(g, x, y, r, o = {}) { g.beginPath(); g.arc(x, y, Math.max(0, r), 0, Math.PI * 2); paint(g, o); }
  function ellipse(g, x, y, rx, ry, o = {}) {
    g.beginPath(); g.ellipse(x, y, Math.max(0, rx), Math.max(0, ry), o.rot || 0, 0, Math.PI * 2); paint(g, o);
  }
  function rect(g, x, y, w, h, o = {}) {
    g.beginPath();
    if (o.r) g.roundRect(x, y, w, h, o.r); else g.rect(x, y, w, h);
    paint(g, o);
  }
  // A rectangle by its centre, handy with transforms
  function box(g, cx, cy, w, h, o = {}) { rect(g, cx - w / 2, cy - h / 2, w, h, o); }
  function poly(g, pts, o = {}) {
    g.beginPath();
    pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
    if (o.close !== false) g.closePath();
    paint(g, o);
  }
  function line(g, x1, y1, x2, y2, o = {}) {
    g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); paint(g, {stroke: "#fff", ...o, fill: null});
  }
  function star(g, x, y, r, o = {}) {
    const n = o.points || 5, inner = r * (o.inner ?? 0.48), pts = [];
    for (let i = 0; i < n * 2; i++) {
      const a = -Math.PI / 2 + (i * Math.PI) / n + (o.rot || 0);
      const rr = i % 2 ? inner : r;
      pts.push([x + Math.cos(a) * rr, y + Math.sin(a) * rr]);
    }
    poly(g, pts, o);
  }
  // A wobbly organic blob (clouds, splats, bushes)
  function blob(g, x, y, r, o = {}) {
    const n = o.points || 9, rnd = rand(o.seed || 3), pts = [];
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2;
      const rr = r * (1 + (rnd() - 0.5) * (o.wobble ?? 0.35));
      pts.push([x + Math.cos(a) * rr, y + Math.sin(a) * rr * (o.squash ?? 1)]);
    }
    g.beginPath();
    for (let i = 0; i < n; i++) {
      const p0 = pts[i], p1 = pts[(i + 1) % n];
      const mx = (p0[0] + p1[0]) / 2, my = (p0[1] + p1[1]) / 2;
      if (i === 0) g.moveTo(mx, my); else g.quadraticCurveTo(p0[0], p0[1], mx, my);
    }
    const p0 = pts[0], p1 = pts[1];
    g.quadraticCurveTo(p0[0], p0[1], (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2);
    g.closePath();
    paint(g, o);
  }
  function arrow(g, x1, y1, x2, y2, o = {}) {
    const a = Math.atan2(y2 - y1, x2 - x1), hl = o.head ?? 36;
    line(g, x1, y1, x2 - Math.cos(a) * hl * 0.6, y2 - Math.sin(a) * hl * 0.6, o);
    poly(g, [[x2, y2], [x2 - Math.cos(a - 0.5) * hl, y2 - Math.sin(a - 0.5) * hl],
      [x2 - Math.cos(a + 0.5) * hl, y2 - Math.sin(a + 0.5) * hl]], {fill: o.stroke || "#fff"});
  }

  // Fonts that are on every Windows, macOS and Linux PC (the heavy one is the captions' font)
  const FONTS = {
    heavy: '"Segoe UI Black", "Arial Black", "Helvetica Neue", Arial, "DejaVu Sans", sans-serif',
    sans: '"Segoe UI", "Helvetica Neue", Arial, "DejaVu Sans", sans-serif',
    serif: 'Georgia, "Times New Roman", "DejaVu Serif", serif',
    mono: 'Consolas, "SF Mono", Menlo, "DejaVu Sans Mono", monospace',
    emoji: '"Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", sans-serif',
  };
  const fontOf = (o) => `${o.italic ? "italic " : ""}${o.weight ?? 900} ${o.size ?? 72}px ${FONTS[o.font || "heavy"] || o.font}`;

  // text(g, "Hello", x, y, {size, weight, font, color, stroke, lw, align, baseline, maxWidth, shadow, spacing})
  // A thick outline (stroke + lw) keeps text readable over anything. maxWidth shrinks the font to fit.
  function text(g, str, x, y, o = {}) {
    str = String(str);
    let size = o.size ?? 72;
    g.font = fontOf({...o, size});
    if (o.maxWidth) {
      const w = g.measureText(str).width;
      if (w > o.maxWidth) { size = Math.max(10, size * o.maxWidth / w); g.font = fontOf({...o, size}); }
    }
    g.textAlign = o.align || "center";
    g.textBaseline = o.baseline || "middle";
    if (o.spacing) g.letterSpacing = `${o.spacing}px`;
    if (o.shadow) {
      g.shadowColor = o.shadow.color || "rgba(0,0,0,0.45)";
      g.shadowBlur = o.shadow.blur ?? 18;
      g.shadowOffsetY = o.shadow.y ?? 8;
    }
    if (o.stroke) {
      g.lineJoin = "round";
      g.miterLimit = 2;
      g.lineWidth = o.lw ?? Math.max(4, size * 0.16);
      g.strokeStyle = o.stroke;
      g.strokeText(str, x, y);
      g.shadowColor = "transparent";
    }
    g.fillStyle = o.color || "#fff";
    g.fillText(str, x, y);
    g.shadowColor = "transparent";
    g.letterSpacing = "0px";
    return size;
  }
  const measure = (g, str, o = {}) => { g.font = fontOf(o); return g.measureText(String(str)).width; };
  // Emoji are quick stand-ins, but they look different on every system: draw the things that matter yourself.
  function emoji(g, ch, x, y, size = 120) {
    g.font = `${size}px ${FONTS.emoji}`; g.textAlign = "center"; g.textBaseline = "middle"; g.fillStyle = "#000";
    g.fillText(ch, x, y);
  }

  // ------------------------------------------------------------------ backgrounds and effects
  function gradient(g, stops, x0 = 0, y0 = 0, x1 = 0, y1 = H) {
    const gr = g.createLinearGradient(x0, y0, x1, y1);
    stops.forEach((c, i) => (Array.isArray(c) ? gr.addColorStop(c[0], c[1]) : gr.addColorStop(i / Math.max(1, stops.length - 1), c)));
    return gr;
  }
  function radial(g, x, y, r, stops) {
    const gr = g.createRadialGradient(x, y, 0, x, y, r);
    stops.forEach((c, i) => (Array.isArray(c) ? gr.addColorStop(c[0], c[1]) : gr.addColorStop(i / Math.max(1, stops.length - 1), c)));
    return gr;
  }
  // fill the whole frame: a colour, or a list of colours for a top-to-bottom gradient
  function bg(g, c) {
    g.save(); g.setTransform(1, 0, 0, 1, 0, 0);
    g.fillStyle = Array.isArray(c) ? gradient(g, c) : c;
    g.fillRect(0, 0, W, H); g.restore();
  }
  // sunburst rays turning slowly behind a subject
  function rays(g, x, y, t, o = {}) {
    const n = o.count || 16, r = o.radius || 1600, spin = (o.speed ?? 0.05) * t * Math.PI * 2;
    g.save(); g.translate(x, y); g.rotate(spin);
    g.fillStyle = o.color || "rgba(255,255,255,0.08)";
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2, w = Math.PI / n;
      g.beginPath(); g.moveTo(0, 0); g.arc(0, 0, r, a, a + w); g.closePath(); g.fill();
    }
    g.restore();
  }
  function vignette(g, amount = 0.45) {
    g.save(); g.setTransform(1, 0, 0, 1, 0, 0);
    g.fillStyle = radial(g, W / 2, H / 2, H * 0.75, [[0.45, "rgba(0,0,0,0)"], [1, `rgba(0,0,0,${amount})`]]);
    g.fillRect(0, 0, W, H); g.restore();
  }
  function flash(g, amount, color = "#ffffff") {
    if (amount <= 0) return;
    g.save(); g.setTransform(1, 0, 0, 1, 0, 0); g.globalAlpha = clamp(amount); g.fillStyle = color;
    g.fillRect(0, 0, W, H); g.restore();
  }
  // Particles that burst from (x, y) at time `at`: confetti, coins, sparks, debris. Same every frame for a seed.
  function burst(g, t, at, x, y, o = {}) {
    const life = o.life ?? 1.4, age = t - at;
    if (age < 0 || age > life) return;
    const rnd = rand(o.seed ?? 7), n = o.count ?? 40, grav = o.gravity ?? 1800;
    const colors = o.colors || ["#FFD60A", "#FF5D73", "#4CC9F0", "#80ED99", "#ffffff"];
    for (let i = 0; i < n; i++) {
      const a = (o.angle ?? -Math.PI / 2) + (rnd() - 0.5) * (o.spread ?? Math.PI * 1.6);
      const v = (o.speed ?? 1400) * (0.35 + rnd() * 0.65);
      const px = x + Math.cos(a) * v * age, py = y + Math.sin(a) * v * age + 0.5 * grav * age * age;
      const s = (o.size ?? 18) * (0.5 + rnd()) * (1 - age / life * 0.5);
      g.save(); g.globalAlpha = 1 - (age / life) ** 2; g.translate(px, py); g.rotate(rnd() * 6 + age * (rnd() * 12 - 6));
      g.fillStyle = colors[i % colors.length];
      if (o.draw) o.draw(g, s, i); else g.fillRect(-s / 2, -s / 3, s, s * 0.66);
      g.restore();
    }
  }
  // camera(g, {x, y, zoom, rot, shake, t}): look at (x, y) in a world drawn at full size; call g.restore() after
  function camera(g, o = {}) {
    g.save();
    const z = o.zoom ?? 1, sh = o.shake || 0, t = o.t || 0;
    g.translate(W / 2 + noise(t * 13, 1) * sh, H / 2 + noise(t * 13, 2) * sh);
    if (o.rot) g.rotate(o.rot);
    g.scale(z, z);
    g.translate(-(o.x ?? W / 2), -(o.y ?? H / 2));
  }
  function image(g, img, x, y, w, h, o = {}) {
    if (!img) return;
    h = h ?? (w * img.height) / img.width;
    g.save(); if (o.alpha !== undefined) g.globalAlpha = o.alpha;
    g.drawImage(img, x - w / 2, y - h / 2, w, h); g.restore();
  }

  // Numbers the way a counter shows them: 1234567 -> "1,234,567"; money(5368709.12) -> "$5,368,709.12"
  const num = (v, d = 0) => Number(v).toLocaleString("en-US", {minimumFractionDigits: d, maximumFractionDigits: d});
  const money = (v, d = 2) => "$" + num(v, d);
  const short = (v) => {
    const a = Math.abs(v);
    for (const [n, s] of [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "K"]]) if (a >= n) return (v / n).toFixed(a >= n * 100 ? 0 : 1) + s;
    return num(v);
  };

  // A big game-style counter at the top (no box: a thick outline keeps it readable)
  function hud(g, o = {}) {
    const y = o.y ?? 250;
    if (o.label) text(g, o.label, W / 2, y - 92, {size: 46, font: "mono", weight: 700, color: o.labelColor || "#fff", stroke: "#000", lw: 10, spacing: 4});
    text(g, o.value ?? "", W / 2, y, {size: o.size ?? 132, font: "mono", weight: 800, color: o.color || "#FFD60A", stroke: "#000", lw: 18, maxWidth: W - 120, shadow: {blur: 20, y: 8}});
    if (o.sub) text(g, o.sub, W / 2, y + 96, {size: 40, font: "mono", weight: 700, color: "#fff", stroke: "#000", lw: 9});
  }

  // ------------------------------------------------------------------ the voiceover's timeline
  const beats = DATA.beats || [];
  const words = DATA.words || [];
  const byId = {};
  beats.forEach((b) => { if (b.id) byId[b.id] = b; });
  // beat(t): the beat being spoken at t (or the last one started), with its local time and progress
  function beat(t) {
    let cur = null;
    for (const b of beats) if (b.start <= t) cur = b;
    if (!cur) return {i: -1, id: "", t: t, p: 0, start: 0, end: 0, say: ""};
    return {...cur, t: t - cur.start, p: clamp(inv(cur.start, cur.end, t))};
  }
  // cue("house") -> when the beat with "id": "house" starts (or its end: cue("house", "end"))
  function cue(id, which = "start") {
    const b = byId[id];
    if (!b) throw new Error(`no beat with "id": "${id}" in the script`);
    return b[which];
  }
  // word("million") -> when that word is first spoken (or the n-th time: word("day", 3)); null if it isn't
  function word(w, n = 1) {
    const want = w.toLowerCase();
    let k = 0;
    for (const x of words) if (x.clean === want && ++k === n) return x.start;
    return null;
  }

  // ------------------------------------------------------------------ captions
  // Word-by-word captions in the TikTok style: a few words at a time, the one being spoken highlighted.
  // Turned off with "captions": {"show": false} in episode.json, or moved with "y". Style keys: size, color,
  // highlight, stroke, max_words, max_chars, upper.
  const CAP = Object.assign({show: true, y: 1240, size: 92, color: "#ffffff", highlight: "#FFD60A", stroke: "#000000",
    max_words: 3, max_chars: 18, upper: true, pop: true}, DATA.captions || {});
  const pages = [];
  (function paginate() {
    let cur = [];
    const flush = () => { if (cur.length) pages.push(cur); cur = []; };
    words.forEach((w, i) => {
      const prev = words[i - 1];
      const brk = prev && (prev.beat !== w.beat || /[.!?…]$/.test(prev.text) || w.start - prev.end > 0.45 ||
        (/[,;:]$/.test(prev.text) && cur.length >= 2));
      if (brk) flush();
      const len = cur.reduce((s, x) => s + x.text.length + 1, 0) + w.text.length;
      if (cur.length && (cur.length >= CAP.max_words || len > CAP.max_chars)) flush();
      cur.push(w);
    });
    flush();
    pages.forEach((p, i) => {
      p.start = p[0].start - 0.05;
      const next = pages[i + 1];
      p.end = next ? Math.min(next[0].start - 0.05, p[p.length - 1].end + 0.6) : p[p.length - 1].end + 0.6;
    });
  })();
  function captions(g, t) {
    if (!CAP.show || rf.captions === false) return;
    const page = pages.find((p) => p.start <= t && t < p.end);
    if (!page) return;
    g.save(); g.setTransform(1, 0, 0, 1, 0, 0);
    const size = CAP.size, o = {size, font: "heavy", weight: 900};
    const show = (w) => (CAP.upper ? w.text.toUpperCase() : w.text);
    const space = Math.max(measure(g, " ", o), size * 0.3);
    const widths = page.map((w) => measure(g, show(w), o));
    // one or two rows, each at most 900 px wide
    let rows = [page.map((_, i) => i)];
    const total = widths.reduce((a, b) => a + b, 0) + space * (page.length - 1);
    if (total > 900 && page.length > 1) {
      let best = 1, diff = 1e9;
      for (let k = 1; k < page.length; k++) {
        const a = widths.slice(0, k).reduce((x, y) => x + y, 0), b = widths.slice(k).reduce((x, y) => x + y, 0);
        if (Math.abs(a - b) < diff) { diff = Math.abs(a - b); best = k; }
      }
      rows = [page.map((_, i) => i).slice(0, best), page.map((_, i) => i).slice(best)];
    }
    const lh = size * 1.12, y0 = -((rows.length - 1) * lh) / 2;
    // the whole caption pops in as one block, so its words never run into each other
    const appear = CAP.pop ? ease.outBack(inv(page.start, page.start + 0.18, t), 1.6) : 1;
    g.translate(W / 2, CAP.y);
    g.scale(Math.max(appear, 0.01), Math.max(appear, 0.01));
    rows.forEach((row, r) => {
      const rw = row.reduce((s, i) => s + widths[i], 0) + space * (row.length - 1);
      let x = -rw / 2;
      row.forEach((i) => {
        const w = page[i], on = w.start <= t, now = on && t < (page[i + 1] ? page[i + 1].start : w.end + 0.6);
        const cx = x + widths[i] / 2, cy = y0 + r * lh;
        const lift = now && CAP.pop ? 6 * ease.outBack(inv(w.start, w.start + 0.15, t)) : 0;
        text(g, show(w), cx, cy - lift, {size, font: "heavy", weight: 900, color: now ? CAP.highlight : CAP.color,
          stroke: CAP.stroke, lw: size * 0.2, shadow: {color: "rgba(0,0,0,0.5)", blur: 16, y: 6}});
        x += widths[i] + space;
      });
    });
    g.restore();
  }

  // ------------------------------------------------------------------ the frame loop
  const canvas = document.getElementById("c");
  const g = canvas.getContext("2d", {alpha: false});
  const rf = {
    W, H, fps: DATA.fps, duration: DATA.duration, title: DATA.title || "", beats, words, img: {}, captions: true,
    clamp, lerp, inv, smooth, ease, map, tween, pulse, pop, wave, rand, noise,
    hex, rgba, mix, shade, gradient, radial,
    circle, ellipse, rect, box, poly, line, star, blob, arrow, text, measure, emoji, image,
    bg, rays, vignette, flash, burst, camera, hud, num, money, short, beat, cue, word, FONTS,
  };
  window.rf = rf;
  let SCENE = null;
  window.scene = function (s) { SCENE = s; };

  async function ready() {
    if (!SCENE) throw new Error("scene.js didn't call scene({...}): see docs/2D.md");
    if (typeof SCENE.draw !== "function") throw new Error("scene.js: scene({...}) needs a draw(g, t, rf) function");
    const assets = SCENE.assets || {};
    await Promise.all(Object.entries(assets).map(([k, src]) => new Promise((ok, bad) => {
      const im = new Image();
      im.onload = () => { rf.img[k] = im; ok(); };
      im.onerror = () => bad(new Error(`couldn't load the image ${src} (assets in scene.js are paths from the episode folder)`));
      im.src = (DATA.base || "") + src;
    })));
    try { await document.fonts.load(fontOf({size: 90})); } catch (e) { /* the fallback font is fine */ }
    if (SCENE.setup) await SCENE.setup(rf);
  }

  window.__rf_frame = function (t, opts = {}) {
    rf.captions = opts.captions !== false;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.globalAlpha = 1;
    g.fillStyle = "#000"; g.fillRect(0, 0, W, H);
    g.save();
    SCENE.draw(g, t, rf);
    g.restore();
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.globalAlpha = 1;
    captions(g, t);
    return canvas.toDataURL("image/jpeg", opts.quality ?? 0.95);
  };
  window.__rf_ready = false;
  window.__rf_error = "";
  window.addEventListener("load", () => {
    ready().then(() => { window.__rf_ready = true; })
      .catch((e) => { window.__rf_error = String(e && e.stack || e); });
  });
})();
