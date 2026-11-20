// demo_2d_penny: "A million dollars now, or a penny doubled for 30 days?"
// A flat 2D cartoon drawn with canvas calls and ReelForge's rf helpers (docs/2D.md). Everything is timed to the
// voiceover's beats (rf.cue("d10") = when the beat with "id": "d10" starts), so changing the script moves the
// pictures with it. The penny's value on day n is $0.01 x 2^(n-1) (facts.md).

const GOLD = "#FFD60A", INK = "#14121F", CREAM = "#FFF6E0";
const COPPER = "#D9813F", COPPER_DARK = "#8E4A1F", GREEN = "#2DBE6C", GREEN_DARK = "#167A45";
const value = (day) => 0.01 * Math.pow(2, day - 1);

let DAYS = [];       // [[time, day], ...] from the script's beats
let MONEY = [];      // the money counter's own keyframes (day 30's money waits for the reveal)

scene({
  setup(rf) {
    const c = rf.cue;
    const three = rf.lerp(c("d3"), c("d3", "end"), 0.5);      // "Day three" is the second half of that beat
    DAYS = [[0, 0], [c("d1"), 1], [c("d3"), 2], [three, 3], [c("d7"), 7], [c("d10"), 10], [c("d14"), 14],
      [c("d17"), 17], [c("d20"), 20], [c("d22"), 22], [c("d25"), 25], [c("d27"), 27], [c("d28"), 28],
      [c("d29"), 29], [c("d30"), 30]];
    MONEY = DAYS.filter(([, d]) => d < 30).concat([[c("final"), 30]]);
  },

  draw(g, t, rf) {
    const {W, H, cue} = rf;
    const dayF = stepped(t, DAYS, 0.55, rf);
    const moneyDay = stepped(t, MONEY, 0.9, rf);
    const tFinal = cue("final"), tAsk = cue("ask"), tD1 = cue("d1"), tD10 = cue("d10"), tD25 = cue("d25");

    // ---- background: the palette warms as the money grows, and gold rays take over at the end
    const warm = rf.map(t, tD10, tD25, 0, 1, "inOut");
    const top = rf.mix(rf.mix("#1B2A6B", "#3A1C71", warm), "#5B1A4A", rf.map(t, tFinal - 2, tFinal, 0, 1));
    const bottom = rf.mix(rf.mix("#0F7C7A", "#D7426B", warm), "#F59E0B", rf.map(t, tFinal - 2, tFinal, 0, 1));
    rf.bg(g, [top, bottom]);
    rf.rays(g, W / 2, 820, t, {count: 18, color: `rgba(255,255,255,${0.05 + 0.07 * rf.map(t, tFinal - 1, tFinal, 0, 1)})`});
    floatingSigns(g, t, rf);

    // ---- a camera that never stands still, and shakes on the big hits
    const shake = 26 * Math.exp(-Math.max(0, t - cue("d28")) * 3) * (t > cue("d28") ? 1 : 0) +
      34 * Math.exp(-Math.max(0, t - tFinal) * 2.5) * (t > tFinal ? 1 : 0);
    rf.camera(g, {zoom: 1 + 0.025 * Math.sin(t * 0.7), x: W / 2 + 10 * Math.sin(t * 0.4), y: H / 2, shake, t});

    if (t < tD1) hook(g, t, rf);
    else if (t < tD10) pennies(g, t, rf, dayF);
    else if (t < tD25) items(g, t, rf);
    else if (t < tAsk) race(g, t, rf, moneyDay);
    else ending(g, t, rf);
    g.restore();

    // ---- the counter: day and money, from day 1 until the question at the end
    const hudIn = rf.map(t, tD1 - 0.2, tD1 + 0.3, 0, 1, "outBack") * rf.map(t, tAsk - 0.1, tAsk + 0.3, 1, 0);
    if (hudIn > 0.01) {
      g.save();
      g.translate(W / 2, 250); g.scale(hudIn, hudIn); g.translate(-W / 2, -250);
      const d = Math.max(1, Math.round(dayF));
      const bump = 1 + 0.12 * rf.pulse(t, lastKey(t, MONEY), 0.35);
      g.save(); g.translate(W / 2, 270); g.scale(bump, bump); g.translate(-W / 2, -270);
      rf.hud(g, {label: `DAY ${d} OF 30`, value: rf.money(value(Math.max(1, moneyDay))), y: 270,
        color: moneyDay >= 28 ? "#7CFFB2" : GOLD});
      g.restore();
      g.restore();
    }
    rf.burst(g, t, cue("d28"), W / 2, 760, {count: 70, seed: 3, speed: 1700});
    rf.burst(g, t, tFinal, W / 2, 700, {count: 120, seed: 9, speed: 2100, life: 2.2});
    rf.flash(g, 0.7 * rf.pulse(t, cue("d28"), 0.35) + 0.85 * rf.pulse(t, tFinal, 0.45));
    rf.vignette(g, 0.4);
  },
});

// A day number that steps up through its keyframes, counting quickly from one day to the next
function stepped(t, keys, dur, rf) {
  let v = keys[0][1];
  for (let i = 1; i < keys.length; i++) {
    const [k, d] = keys[i];
    if (t >= k) v = rf.lerp(keys[i - 1][1], d, rf.ease.out(rf.inv(k, k + dur, t)));
  }
  return v;
}
const lastKey = (t, keys) => keys.reduce((a, [k]) => (k <= t ? k : a), -10);

function floatingSigns(g, t, rf) {
  const r = rf.rand(5);
  for (let i = 0; i < 16; i++) {
    const x = r() * rf.W, speed = 40 + r() * 60, y = (r() * rf.H - t * speed) % (rf.H + 200);
    rf.text(g, r() < 0.5 ? "$" : "¢", x + 30 * Math.sin(t + i), y < -100 ? y + rf.H + 200 : y,
      {size: 60 + r() * 70, color: "rgba(255,255,255,0.07)"});
  }
}

// ------------------------------------------------------------------ the things on screen

function coin(g, rf, x, y, r, o = {}) {
  g.save(); g.translate(x, y); g.scale(o.squash ?? 1, 1);
  rf.circle(g, 0, r * 0.12, r, {fill: COPPER_DARK});
  rf.circle(g, 0, 0, r, {fill: COPPER, stroke: INK, lw: Math.max(3, r * 0.09)});
  rf.circle(g, 0, 0, r * 0.74, {stroke: "rgba(80,30,10,0.45)", lw: Math.max(2, r * 0.05)});
  rf.text(g, "1¢", 0, r * 0.04, {size: r * 0.72, color: "#7A3A12"});
  g.beginPath(); g.arc(-r * 0.2, -r * 0.25, r * 0.55, Math.PI * 1.1, Math.PI * 1.55);
  g.strokeStyle = "rgba(255,255,255,0.5)"; g.lineWidth = r * 0.08; g.lineCap = "round"; g.stroke();
  g.restore();
}

function bill(g, rf, x, y, w, label, rot = 0) {
  const h = w * 0.45;
  g.save(); g.translate(x, y); g.rotate(rot);
  rf.box(g, 0, 8, w, h, {fill: GREEN_DARK, r: 14});
  rf.box(g, 0, 0, w, h, {fill: GREEN, stroke: INK, lw: 7, r: 14});
  rf.box(g, 0, 0, w - 34, h - 30, {stroke: "rgba(255,255,255,0.45)", lw: 4, r: 8});
  rf.circle(g, 0, 0, h * 0.3, {fill: "#9BE7B5", stroke: GREEN_DARK, lw: 5});
  rf.text(g, label, -w * 0.33, -h * 0.22, {size: h * 0.26, color: CREAM, stroke: GREEN_DARK, lw: 6});
  rf.text(g, label, w * 0.33, h * 0.22, {size: h * 0.26, color: CREAM, stroke: GREEN_DARK, lw: 6});
  g.restore();
}

function cashStack(g, rf, x, y, w, layers) {
  for (let i = 0; i < layers; i++) {
    g.save(); g.translate(x, y - i * 22);
    rf.box(g, 0, 0, w, w * 0.42, {fill: i % 2 ? GREEN : "#33C977", stroke: INK, lw: 6, r: 10});
    rf.box(g, 0, 0, w * 0.18, w * 0.42 + 6, {fill: CREAM, stroke: INK, lw: 4});
    g.restore();
  }
}

function briefcase(g, rf, x, y, s) {
  g.save(); g.translate(x, y); g.scale(s, s);
  rf.box(g, 0, -150, 150, 70, {stroke: INK, lw: 22, r: 24});
  rf.box(g, 0, -150, 150, 70, {stroke: "#6B3B1F", lw: 12, r: 24});
  for (let i = 0; i < 5; i++) bill(g, rf, -120 + i * 60, -95 + (i % 2) * 8, 150, "$", -0.15 + i * 0.08);
  rf.box(g, 0, 0, 380, 230, {fill: "#8B4D24", stroke: INK, lw: 9, r: 28});
  rf.box(g, 0, -40, 380, 22, {fill: "#6B3B1F"});
  rf.box(g, 0, -40, 70, 54, {fill: GOLD, stroke: INK, lw: 6, r: 10});
  g.restore();
}

function candy(g, rf, x, y, s) {
  g.save(); g.translate(x, y); g.scale(s, s); g.rotate(-0.12);
  rf.poly(g, [[-190, -40], [-240, -70], [-240, 70], [-190, 40]], {fill: "#E9E9F2", stroke: INK, lw: 6});
  rf.poly(g, [[190, -40], [240, -70], [240, 70], [190, 40]], {fill: "#E9E9F2", stroke: INK, lw: 6});
  rf.box(g, 0, 0, 390, 110, {fill: "#E63946", stroke: INK, lw: 7, r: 12});
  rf.text(g, "CANDY", 0, 4, {size: 58, color: CREAM, stroke: "#7A1420", lw: 8});
  g.restore();
  rf.box(g, x + 150, y - 110, 170, 74, {fill: CREAM, stroke: INK, lw: 6, r: 14});
  rf.text(g, "$1.50", x + 150, y - 108, {size: 44, color: INK});
}

function plate(g, rf, x, y, s) {
  g.save(); g.translate(x, y); g.scale(s, s);
  rf.ellipse(g, 0, 18, 270, 120, {fill: "rgba(0,0,0,0.25)"});
  rf.ellipse(g, 0, 0, 270, 120, {fill: "#F4F4F8", stroke: INK, lw: 8});
  rf.ellipse(g, 0, -4, 190, 80, {fill: "#E3E3EC"});
  rf.blob(g, -30, -14, 95, {fill: "#8C3B1E", stroke: INK, lw: 7, seed: 4, squash: 0.55, wobble: 0.3});
  rf.blob(g, -40, -22, 60, {fill: "#A8502B", seed: 5, squash: 0.5, wobble: 0.25});
  for (let i = 0; i < 4; i++) rf.circle(g, 80 + (i % 2) * 34, -30 + i * 14, 22, {fill: "#4CAF50", stroke: INK, lw: 4});
  rf.box(g, 330, -40, 22, 220, {fill: "#C9CED6", stroke: INK, lw: 5, r: 8});
  rf.box(g, -330, -40, 22, 220, {fill: "#C9CED6", stroke: INK, lw: 5, r: 8});
  g.restore();
}

function phone(g, rf, x, y, s, t) {
  g.save(); g.translate(x, y); g.scale(s, s); g.rotate(0.08 * Math.sin(t * 2));
  rf.box(g, 0, 0, 300, 580, {fill: "#22222E", stroke: INK, lw: 10, r: 56});
  g.save(); g.beginPath(); g.roundRect(-128, -268, 256, 536, 40); g.clip();
  g.fillStyle = rf.gradient(g, ["#7B5CFF", "#FF5D8F"], 0, -268, 0, 268); g.fillRect(-128, -268, 256, 536);
  for (let i = 0; i < 12; i++) rf.box(g, -78 + (i % 3) * 78, -150 + Math.floor(i / 3) * 90, 56, 56, {fill: "rgba(255,255,255,0.75)", r: 14});
  g.restore();
  rf.box(g, 0, -236, 90, 22, {fill: "#22222E", r: 11});
  g.restore();
}

function car(g, rf, x, y, s, t) {
  g.save(); g.translate(x + 8 * Math.sin(t * 6), y); g.scale(s, s);
  rf.ellipse(g, 0, 120, 330, 30, {fill: "rgba(0,0,0,0.25)"});
  rf.poly(g, [[-320, 60], [-300, -10], [-170, -30], [-90, -120], [120, -120], [210, -30], [320, -10], [330, 60]],
    {fill: "#2F7DF6", stroke: INK, lw: 9});
  rf.poly(g, [[-70, -100], [-140, -30], [40, -30], [40, -100]], {fill: "#BFE3FF", stroke: INK, lw: 6});
  rf.poly(g, [[70, -100], [70, -30], [180, -30], [110, -100]], {fill: "#BFE3FF", stroke: INK, lw: 6});
  rf.box(g, 0, 40, 640, 26, {fill: "#1E5BC6"});
  for (const wx of [-190, 200]) {
    rf.circle(g, wx, 70, 62, {fill: INK});
    rf.circle(g, wx, 70, 30, {fill: "#C9CED6"});
    g.save(); g.translate(wx, 70); g.rotate(t * 8); rf.box(g, 0, 0, 8, 56, {fill: INK}); g.restore();
  }
  rf.circle(g, 300, 10, 16, {fill: GOLD});
  g.restore();
}

// ------------------------------------------------------------------ the scenes

function choiceCard(g, rf, x, y, w, h, t, at, title, draw) {
  const s = rf.pop(t, at, 0.5);
  if (s <= 0) return;
  g.save(); g.translate(x, y); g.scale(s, s);
  rf.box(g, 0, 14, w, h, {fill: "rgba(0,0,0,0.3)", r: 40});
  rf.box(g, 0, 0, w, h, {fill: CREAM, stroke: INK, lw: 10, r: 40});
  draw();
  rf.text(g, title, 0, h / 2 - 80, {size: 64, color: INK, maxWidth: w - 60});
  g.restore();
}

function hook(g, t, rf) {
  const {W} = rf, most = rf.cue("most");
  const lean = rf.map(t, most, most + 0.6, 0, 1, "outBack");
  const lx = W / 2 - 250, rx = W / 2 + 250, y = 760;
  g.save(); g.translate(lx, y); g.scale(1 + 0.12 * lean, 1 + 0.12 * lean); g.rotate(-0.04 * lean); g.translate(-lx, -y);
  choiceCard(g, rf, lx, y, 440, 600, t, -1, "$1,000,000", () => briefcase(g, rf, 0, -20, 0.95));
  g.restore();
  choiceCard(g, rf, rx, y, 440, 600, t, -1, "1¢ × 2 / DAY", () => coin(g, rf, 0, -60, 120 + 6 * Math.sin(t * 5)));
  const orS = rf.pop(t, -1, 0.4);     // all there on the first frame: it doubles as the cover
  if (orS > 0) {
    g.save(); g.translate(W / 2, y); g.scale(orS, orS);
    rf.circle(g, 0, 0, 86, {fill: GOLD, stroke: INK, lw: 9});
    rf.text(g, "OR", 0, 4, {size: 70, color: INK});
    g.restore();
  }
  if (t > most + 0.3) {
    const s = rf.pop(t, most + 0.3, 0.4);
    g.save(); g.translate(lx, y - 380); g.scale(s, s); g.rotate(-0.08);
    rf.box(g, 0, 0, 420, 96, {fill: "#E63946", stroke: INK, lw: 8, r: 20});
    rf.text(g, "MOST PEOPLE", 0, 4, {size: 54, color: CREAM});
    g.restore();
  }
}

function pennies(g, t, rf, dayF) {
  const {W} = rf, d1 = rf.cue("d1"), d7 = rf.cue("d7");
  const n = Math.round(Math.pow(2, Math.max(0, Math.floor(dayF + 0.001)) - 1));
  // a pile: rows of coins, the newest popping in on top
  const r = n <= 2 ? 150 : n <= 4 ? 120 : n <= 8 ? 90 : 46;
  const perRow = Math.max(1, Math.floor(860 / (r * 2.05)));
  const rows = Math.ceil(n / perRow);
  const baseY = n > 8 ? 1090 : 860;
  const shown = Math.max(1, n);
  const stepT = lastStepTime(t);
  for (let i = 0; i < shown; i++) {
    const row = Math.floor(i / perRow), col = i % perRow, inRow = Math.min(perRow, shown - row * perRow);
    const x = W / 2 + (col - (inRow - 1) / 2) * r * 2.05;
    const y = baseY - row * r * 1.25 - (rows > 1 ? 0 : 0);
    const newcomer = i >= shown / 2 && t - stepT < 1.0;
    const s = newcomer ? rf.pop(t, stepT + (i - shown / 2) * (0.5 / Math.max(1, shown / 2)), 0.35) : 1;
    if (s <= 0) continue;
    coin(g, rf, x, y - (newcomer ? 60 * (1 - s) : 0), r * s, {squash: 1 + 0.06 * Math.sin(t * 4 + i)});
  }
  const intro = rf.map(t, d1, d1 + 0.5, 0, 1, "outBack");
  if (t < d7) {
    rf.text(g, n === 1 ? "1 PENNY" : `${n} PENNIES`, W / 2, 560, {size: 88 * intro, color: CREAM, stroke: INK, lw: 14});
  } else {
    const s = rf.pop(t, d7 + 1.2, 0.5);
    if (s > 0) {
      g.save(); g.translate(W / 2, 500); g.scale(s, s); candy(g, rf, 0, 0, 0.9);
      if (t > d7 + 2.0) rf.line(g, -260, -90, 260, 90, {stroke: "#E63946", lw: 22});
      g.restore();
    }
  }
}

function lastStepTime(t) {
  let k = -10;
  for (const [tt, d] of DAYS) if (tt <= t && d > 0) k = tt;
  return k;
}

function items(g, t, rf) {
  const {W, cue} = rf;
  const list = [
    ["d10", (s) => { bill(g, rf, 0, 0, 560 * s, "$5", -0.06); sweat(g, rf, t, 300 * s, -170 * s); }, "$5.12"],
    ["d14", (s) => plate(g, rf, 0, 30, 1.0 * s), "A NICE DINNER"],
    ["d17", (s) => phone(g, rf, 0, 0, 0.9 * s, t), "A NEW PHONE"],
    ["d20", (s) => cashStack(g, rf, 0, 120, 520 * s, Math.round(10 * s)), "$5,242"],
    ["d22", (s) => car(g, rf, 0, 40, 1.15 * s, t), "A CAR"],
  ];
  let cur = 0;
  list.forEach(([id], i) => { if (t >= cue(id)) cur = i; });
  const [id, draw, tag] = list[cur];
  const at = cue(id);
  const next = cur + 1 < list.length ? cue(list[cur + 1][0]) : cue("d25");
  const inS = rf.ease.outBack(rf.inv(at, at + 0.45, t), 1.8);
  const out = rf.map(t, next - 0.25, next, 0, 1, "in");
  g.save(); g.translate(W / 2 - out * 900, 800); g.rotate(-0.15 * out);
  draw(Math.max(0.01, inS));
  g.restore();
  const ts = rf.pop(t, at + 0.35, 0.4) * (1 - out);
  if (ts > 0) {
    g.save(); g.translate(W / 2, 1060); g.scale(ts, ts); g.rotate(-0.03);
    const w = rf.measure(g, tag, {size: 70}) + 80;
    rf.box(g, 0, 0, w, 110, {fill: GOLD, stroke: INK, lw: 9, r: 26});
    rf.text(g, tag, 0, 4, {size: 70, color: INK});
    g.restore();
  }
}

function sweat(g, rf, t, x, y) {
  const k = (t * 1.2) % 1;
  g.save(); g.translate(x, y + k * 60); g.globalAlpha = 1 - k;
  rf.poly(g, [[0, -40], [24, 6], [0, 30], [-24, 6]], {fill: "#7FD3FF", stroke: INK, lw: 5});
  g.restore();
}

// Two bars: the million (fixed) against the penny (growing). The scale follows the bigger one, so once the penny
// passes the million, the million's bar shrinks away.
function race(g, t, rf, moneyDay) {
  const {W, cue} = rf;
  const penny = value(Math.max(1, moneyDay));
  const scale = Math.max(1e6, penny) * 1.12;
  const appear = rf.map(t, cue("d25"), cue("d25") + 0.6, 0, 1, "outBack");
  const fin = cue("final");
  const gone = rf.map(t, fin, fin + 0.5, 0, 1, "in");      // the bars make way for the final number
  const top = 540, h = 540, base = top + h;
  g.save(); g.globalAlpha = 1 - gone; g.translate(0, 300 * gone);
  const bars = [["THE MILLION", 1e6, "#9AA4B2", W / 2 - 200], ["YOUR PENNY", penny, GOLD, W / 2 + 200]];
  rf.line(g, 120, base, W - 120, base, {stroke: CREAM, lw: 10});
  bars.forEach(([name, v, col, x], i) => {
    const bh = Math.max(14, (h * v) / scale) * appear;
    rf.rect(g, x - 130, base - bh, 260, bh, {fill: col, stroke: INK, lw: 9, r: 18});
    rf.text(g, rf.money(v, 0), x, base - bh - 60, {size: 54, color: CREAM, stroke: INK, lw: 11, maxWidth: 420});
    rf.text(g, name, x, base + 60, {size: 44, color: CREAM, stroke: INK, lw: 10});
    if (i === 1) coin(g, rf, x, base - bh + 4, 56);
  });
  g.restore();
  const passed = cue("d28");
  if (t > passed && gone < 1) {
    const s = rf.pop(t, passed + 0.15, 0.4);
    g.save(); g.globalAlpha = 1 - gone; g.translate(W / 2, 450); g.scale(s, s); g.rotate(-0.06);
    rf.box(g, 0, 0, 500, 120, {fill: "#2DBE6C", stroke: INK, lw: 10, r: 26});
    rf.text(g, "PASSED IT!", 0, 6, {size: 76, color: CREAM, stroke: INK, lw: 10});
    g.restore();
  }
  if (t > fin) {
    moneyRain(g, rf, t - fin);
    const s = rf.pop(t, fin + 0.1, 0.6);
    g.save(); g.translate(W / 2, 860); g.scale(s, s); g.rotate(-0.04);
    rf.text(g, "$5,368,709", 0, 0, {size: 150, color: GOLD, stroke: INK, lw: 22, shadow: {blur: 40, y: 12}, maxWidth: 1000});
    g.restore();
  }
}

function moneyRain(g, rf, age) {
  const r = rf.rand(11);
  for (let i = 0; i < 20; i++) {
    const x = r() * rf.W, sp = 500 + r() * 500, y = -200 + age * sp - r() * 900;
    if (y > -200 && y < rf.H + 200) bill(g, rf, x, y, 170, "$", Math.sin(age * 3 + i) * 0.6);
  }
}

function ending(g, t, rf) {
  const {W} = rf, at = rf.cue("ask");
  choiceCard(g, rf, W / 2 - 250, 780, 440, 560, t, at + 0.05, "$1,000,000", () => briefcase(g, rf, 0, -10, 0.85));
  choiceCard(g, rf, W / 2 + 250, 780, 440, 560, t, at + 0.3, "$5,368,709", () => {
    for (let i = 0; i < 6; i++) coin(g, rf, -90 + (i % 3) * 90, -70 + Math.floor(i / 3) * 70, 52);
  });
  const s = rf.pop(t, at + 0.6, 0.4);
  if (s > 0) {
    g.save(); g.translate(W / 2, 780); g.scale(s, s);
    rf.circle(g, 0, 0, 90, {fill: GOLD, stroke: INK, lw: 9});
    rf.text(g, "?", 0, 6, {size: 110, color: INK});
    g.restore();
  }
  rf.text(g, "WHICH ONE?", W / 2, 360, {size: 110 * rf.pop(t, at + 0.2, 0.4), color: CREAM, stroke: INK, lw: 18});
}
