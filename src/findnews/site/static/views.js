// 自治体ビュー・全体ビュー: 計算方法の切り替えとグラフの描画(DESIGN.md 16 節)。外部依存・外部通信なし。
// データは各ページに埋め込まれた <script type="application/json" id="view-data">。
// 挿入する文字はすべて textContent / createElementNS で作る(innerHTML は使わない)。
// ---------------------------------------------------------------- 計算(DOM に依存しない部分。tests/test_views_js.py が node で検査する)
var FNCalc = (function () {
  "use strict";
  function byYear(points) { var m = {}; (points || []).forEach(function (p) { m[p.y] = p; }); return m; }

  // 1 系列(自治体)を方法で変換する。total は同じ指標の全体系列(points)。戻り値: [{y, d, x(raw), v(方法の値), dirv(方向判定に使う値), dirKind}]
  function compute(method, points, totalPoints, baseYear) {
    var pts = (points || []).slice().sort(function (a, b) { return a.y - b.y; });
    var T = byYear(totalPoints);
    var base = null;
    pts.forEach(function (p) { if (p.y === baseYear) base = p; });
    var out = [];
    for (var i = 0; i < pts.length; i++) {
      var p = pts[i], prev = i > 0 ? pts[i - 1] : null, prev2 = i > 1 ? pts[i - 2] : null;
      var x = p.v, xp = prev ? prev.v : null, xpp = prev2 ? prev2.v : null;
      var t = T[p.y] ? T[p.y].v : null, tp = prev && T[prev.y] ? T[prev.y].v : null;
      var pct = (x != null && xp != null && xp !== 0 && prev && prev.y === p.y - 1) ? (x - xp) / xp : null;
      var tpct = (t != null && tp != null && tp !== 0) ? (t - tp) / tp : null;
      var share = (x != null && t) ? x / t * 100 : null;
      var sharePrev = (xp != null && tp) ? xp / tp * 100 : null;
      var v = null, dirv = pct, kind = "pct", tv = null;
      switch (method) {
        case "raw": v = x; break;
        case "diff": v = (x != null && xp != null) ? x - xp : null; break;
        case "pct": v = pct == null ? null : pct * 100; tv = tpct == null ? null : tpct * 100; break;
        case "total_pct": v = tpct == null ? null : tpct * 100; dirv = tpct; break;
        case "rel_pct": v = (pct != null && tpct != null) ? ((1 + pct) / (1 + tpct) - 1) * 100 : null;
          dirv = v == null ? null : v / 100; kind = "rel_pct"; break;
        case "share": v = share; dirv = (share != null && sharePrev != null) ? share - sharePrev : null; kind = "share_diff"; break;
        case "share_diff": v = (share != null && sharePrev != null) ? share - sharePrev : null; dirv = v; kind = "share_diff"; break;
        case "index": v = (x != null && base && base.v) ? x / base.v * 100 : null;
          var tb = T[baseYear] ? T[baseYear].v : null; tv = (t != null && tb) ? t / tb * 100 : null; break;
        case "cum_diff": v = (x != null && base && base.v != null) ? x - base.v : null; break;
        case "diff2": v = (x != null && xp != null && xpp != null) ? (x - xp) - (xp - xpp) : null;
          dirv = (v != null && xp) ? v / Math.abs(xp) : null; break;
      }
      out.push({ y: p.y, d: p.d, x: x, v: v, tv: tv, t: t, dirv: dirv, kind: kind, reason: p.r, pct: pct });
    }
    return out;
  }
  // th: {pct, rel_pct, share_diff}(config/settings.yaml の方法別閾値)
  function direction(r, TH) {
    if (r.dirv == null || isNaN(r.dirv)) return "未取得";
    var th = r.kind === "share_diff" ? TH.share_diff : (r.kind === "rel_pct" ? TH.rel_pct : TH.pct);
    if (r.dirv >= th) return "増加";
    if (r.dirv <= -th) return "減少";
    return "横ばい";
  }
  function dirClass(d) { return d === "増加" ? "up" : d === "減少" ? "down" : "flat"; }
  var MINUS = "−";
  function fmt(v, unit) {
    if (v == null || isNaN(v)) return "—";
    var a = Math.abs(v);
    // 0.01 未満の割合差なども 0.00 と丸めず有効数字 2 桁で示す
    var digits = (unit === "%" || unit === "pt") ? (a < 10 ? (a > 0 && a < 0.01 ? Math.min(6, 1 - Math.floor(Math.log10(a))) : 2) : 1) : (unit === "指数" ? 1 : 0);
    var s = Math.abs(v).toLocaleString("ja-JP", { minimumFractionDigits: digits, maximumFractionDigits: digits });
    return (v < 0 ? MINUS : (unit === "%" || unit === "pt") && v > 0 ? "+" : "") + s;
  }

  return { compute: compute, direction: direction, dirClass: dirClass, fmt: fmt };
})();
if (typeof module !== "undefined" && module.exports) module.exports = FNCalc;

(function () {
  "use strict";
  if (typeof document === "undefined") return;
  var el = document.getElementById("view-data");
  if (!el) return;
  var D = JSON.parse(el.textContent);
  var SVGNS = "http://www.w3.org/2000/svg";
  var TH = D.thresholds;            // {pct, rel_pct, share_diff}
  var METHODS = D.methods;          // {id: {name, unit, needsTotal, signed}}
  var compute = FNCalc.compute, dirClass = FNCalc.dirClass, fmt = FNCalc.fmt;
  function direction(r) { return FNCalc.direction(r, TH); }

  // ---------------------------------------------------------------- SVG
  function S(tag, attrs, parent) {
    var e = document.createElementNS(SVGNS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function T_(tag, text, attrs, parent) { var e = S(tag, attrs, parent); e.textContent = text; return e; }
  function days(d) { return Date.parse(d + "T00:00:00Z") / 86400000; }
  function niceTicks(lo, hi, n) {
    if (lo === hi) { hi = lo + 1; }
    var span = hi - lo, raw = span / (n || 4), mag = Math.pow(10, Math.floor(Math.log10(raw)));
    var step = [1, 2, 2.5, 5, 10].map(function (m) { return m * mag; }).find(function (s) { return s >= raw; });
    var t0 = Math.floor(lo / step) * step, out = [];
    for (var t = t0; t <= hi + step * 0.001; t += step) out.push(+t.toFixed(10));
    return out;
  }

  var tip = document.createElement("div"); tip.className = "tooltip"; tip.hidden = true; document.body.appendChild(tip);
  function show(evt, lines) {
    while (tip.firstChild) tip.removeChild(tip.firstChild);
    lines.forEach(function (l, i) { var d = document.createElement("div"); if (i === 0) { var b = document.createElement("strong"); b.textContent = l; d.appendChild(b); } else d.textContent = l; tip.appendChild(d); });
    tip.hidden = false; tip.style.left = (evt.pageX + 12) + "px"; tip.style.top = (evt.pageY + 12) + "px";
  }
  function hide() { tip.hidden = true; }

  // spec: {title, rows(compute 結果), method, unit, elections[{date,label,text}], lanes[{name, marks:[{date, kind, text}]}], overlayLabel}
  function chart(container, spec) {
    var rows = spec.rows.filter(function (r) { return r.d; });
    var vals = rows.map(function (r) { return r.v; }).concat(rows.map(function (r) { return r.tv; })).filter(function (v) { return v != null && !isNaN(v); });
    var fig = document.createElement("figure"); fig.className = "chart"; container.appendChild(fig);
    if (!vals.length) { var p = document.createElement("p"); p.className = "flat small"; p.textContent = "この方法では値を計算できない(未取得、または全体の値・基準年の値がない)。"; fig.appendChild(p); return; }
    var lanes = spec.lanes || [];
    var W = 880, L = 88, R = 64, T = 26, PH = 250, B = 40, LANE = 14;
    var H = PH + (lanes.length ? 18 + lanes.length * LANE : 0);
    var xs = rows.map(function (r) { return days(r.d); });
    lanes.forEach(function (ln) { ln.marks.forEach(function (m) { xs.push(days(m.date)); }); });
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs), pad = Math.max(30, (x1 - x0) * 0.04);
    var dx0 = x0 - pad, dx1 = x1 + pad;
    var lo = Math.min.apply(null, vals.concat(spec.signed ? [0] : [0])), hi = Math.max.apply(null, vals.concat([0]));
    var ticks = niceTicks(lo, hi, 4), ylo = Math.min(ticks[0], lo), yhi = Math.max(ticks[ticks.length - 1], hi);
    var base = PH - B;
    function X(d) { return L + (W - L - R) * (days(d) - dx0) / (dx1 - dx0); }
    function Y(v) { return T + (base - T) * (1 - (v - ylo) / ((yhi - ylo) || 1)); }
    var svg = S("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": spec.title, "class": "chart" }, null);
    fig.appendChild(svg);
    ticks.forEach(function (t) {
      S("line", { "class": t === 0 ? "zero" : "grid", x1: L, x2: W - R, y1: Y(t), y2: Y(t) }, svg);
      T_("text", fmt(t, spec.unit), { x: L - 6, y: Y(t) + 4, "text-anchor": "end", "class": "axis-t" }, svg);
    });
    var y0 = new Date(dx0 * 86400000).getUTCFullYear(), y1 = new Date(dx1 * 86400000).getUTCFullYear();
    for (var y = y0; y <= y1; y++) {
      var d = y + "-01-01"; if (days(d) < dx0 || days(d) > dx1) continue;
      S("line", { "class": "grid", x1: X(d), x2: X(d), y1: base, y2: base + 4 }, svg);
      T_("text", String(y), { x: X(d), y: base + 16, "text-anchor": "middle", "class": "axis-t" }, svg);
    }
    T_("text", spec.unitLabel, { x: L - 6, y: T - 10, "text-anchor": "end", "class": "axis-t" }, svg);
    var plot = S("rect", { x: L, y: T, width: W - L - R, height: base - T, fill: "transparent" }, svg);
    // 国政選挙の縦線
    (spec.elections || []).forEach(function (e, i) {
      if (days(e.date) < dx0 || days(e.date) > dx1) return;
      var x = X(e.date);
      S("line", { "class": "event", x1: x, x2: x, y1: T - 4, y2: H - 4 }, svg);
      T_("text", "E" + (i + 1), { x: x + 2, y: T - 6 - (i % 2) * 10, "class": "event-label" }, svg);
      var hit = S("line", { "class": "event-hit", x1: x, x2: x, y1: T - 4, y2: base, tabindex: 0 }, svg);
      T_("title", "E" + (i + 1) + ". " + e.text, {}, hit);
      hit.addEventListener("pointermove", function (evt) { show(evt, ["E" + (i + 1) + ". " + e.date, e.text]); });
      hit.addEventListener("pointerleave", hide);
    });
    // 全体の重ね表示(黒の破線)
    if (spec.overlay) {
      var pts = rows.filter(function (r) { return r.tv != null; });
      if (pts.length > 1) S("polyline", { "class": "overlay", points: pts.map(function (r) { return X(r.d) + "," + Y(r.tv); }).join(" ") }, svg);
      pts.forEach(function (r) { S("circle", { "class": "overlay-dot", cx: X(r.d), cy: Y(r.tv), r: 3 }, svg); });
    }
    // 値の線分と点(色は方法の値に閾値を当てた方向)
    for (var k = 1; k < rows.length; k++) {
      var a = rows[k - 1], b = rows[k];
      if (a.v == null || b.v == null) continue;
      S("line", { "class": "seg " + dirClass(direction(b)), x1: X(a.d), y1: Y(a.v), x2: X(b.d), y2: Y(b.v) }, svg);
    }
    var hover = [];
    rows.forEach(function (r, i) {
      if (r.v == null) return;
      var dr = i === 0 && r.dirv == null ? "未取得" : direction(r);
      S("circle", { "class": "dot " + dirClass(dr), cx: X(r.d), cy: Y(r.v), r: 4 }, svg);
      hover.push({ x: X(r.d), r: r, dir: dr });
    });
    var last = rows.filter(function (r) { return r.v != null; }).pop();
    if (last) T_("text", fmt(last.v, spec.unit), { x: X(last.d) + 8, y: Y(last.v) + 4, "class": "axis-t" }, svg);
    // 申請・要望・交付決定のレーン
    if (lanes.length) {
      var top = PH + 6;
      T_("text", spec.laneTitle || "申請・要望", { x: L - 6, y: top + 6, "text-anchor": "end", "class": "axis-t" }, svg);
      lanes.forEach(function (ln, k) {
        var yy = top + 10 + (k + 1) * LANE - LANE / 2;
        S("line", { "class": "grid", x1: L, x2: W - R, y1: yy, y2: yy }, svg);
        T_("text", ln.label, { x: L - 6, y: yy + 4, "text-anchor": "end", "class": "axis-t" }, svg);
        var ms = ln.marks.slice().sort(function (a, b) { return a.date < b.date ? -1 : 1; });
        if (ln.connect) for (var j = 1; j < ms.length; j++) S("line", { "class": "req-link", x1: X(ms[j - 1].date), x2: X(ms[j].date), y1: yy, y2: yy }, svg);
        ms.forEach(function (m) {
          var x = X(m.date), shape;
          if (m.kind === "apply") shape = S("rect", { "class": "req-apply", x: x - 4, y: yy - 4, width: 8, height: 8 }, svg);
          else if (m.kind === "result") shape = S("path", { "class": "req-result", d: "M" + x + "," + (yy - 5) + " L" + (x + 5) + "," + yy + " L" + x + "," + (yy + 5) + " L" + (x - 5) + "," + yy + " Z" }, svg);
          else if (m.kind === "minus") shape = S("path", { "class": "kofu-minus", d: "M" + (x - 4) + "," + (yy - 4) + " L" + (x + 4) + "," + (yy + 4) + " M" + (x + 4) + "," + (yy - 4) + " L" + (x - 4) + "," + (yy + 4) }, svg);
          else shape = S("circle", { "class": "kofu-plus", cx: x, cy: yy, r: 2.5 }, svg);
          var hit = S("circle", { cx: x, cy: yy, r: 9, fill: "transparent", tabindex: 0 }, svg);
          T_("title", m.text, {}, hit);
          hit.addEventListener("pointermove", function (evt) { show(evt, m.text.split(" / ")); });
          hit.addEventListener("pointerleave", hide);
        });
      });
    }
    var cross = S("line", { "class": "crosshair", x1: 0, x2: 0, y1: T, y2: base, visibility: "hidden" }, svg);
    plot.addEventListener("pointermove", function (evt) {
      var pt = svg.createSVGPoint(); pt.x = evt.clientX; pt.y = evt.clientY;
      var p = pt.matrixTransform(svg.getScreenCTM().inverse()), best = null;
      hover.forEach(function (h) { if (!best || Math.abs(h.x - p.x) < Math.abs(best.x - p.x)) best = h; });
      if (!best) return;
      cross.setAttribute("x1", best.x); cross.setAttribute("x2", best.x); cross.removeAttribute("visibility");
      var r = best.r, lines = [fmt(r.v, spec.unit) + " " + spec.unitLabel, r.y + "年度分 decided " + r.d, "方向: " + best.dir,
        "実額 " + fmt(r.x, "千円") + " 千円" + (r.t != null ? " / 全体 " + fmt(r.t, "千円") + " 千円" : "")];
      if (r.tv != null) lines.push((spec.overlayLabel || "全体") + " " + fmt(r.tv, spec.unit));
      show(evt, lines);
    });
    plot.addEventListener("pointerleave", function () { cross.setAttribute("visibility", "hidden"); hide(); });
    var cap = document.createElement("figcaption"); cap.textContent = spec.caption; fig.appendChild(cap);
  }

  // 方法の値の表(折りたたみの中)
  function table(container, spec) {
    var t = document.createElement("table"), cap = document.createElement("caption");
    cap.textContent = spec.title + "(" + spec.methodName + ")"; t.appendChild(cap);
    var head = ["対象年度", "decided_date", "実額(千円)", "全体(千円)", spec.methodName + "(" + spec.unitLabel + ")", "方向"];
    if (spec.overlay) head.splice(5, 0, (spec.overlayLabel || "全体") + "(" + spec.unitLabel + ")");
    var thead = document.createElement("thead"), tr = document.createElement("tr");
    head.forEach(function (h) { var th = document.createElement("th"); th.scope = "col"; th.textContent = h; tr.appendChild(th); });
    thead.appendChild(tr); t.appendChild(thead);
    var tb = document.createElement("tbody");
    spec.rows.forEach(function (r, i) {
      var row = document.createElement("tr");
      var cells = [r.y + "年度", r.d || "—", fmt(r.x, "千円") + (r.x == null && r.reason ? "(" + r.reason + ")" : ""), fmt(r.t, "千円"), fmt(r.v, spec.unit)];
      if (spec.overlay) cells.push(fmt(r.tv, spec.unit));
      var dr = (i === 0 && r.dirv == null) ? "—" : direction(r);
      cells.push(dr);
      cells.forEach(function (c, j) {
        var td = document.createElement(j === 0 ? "th" : "td"); if (j === 0) td.scope = "row";
        td.textContent = c; if (j >= 2 && j < cells.length - 1) td.className = "num";
        if (j === cells.length - 1) td.className = "dir " + dirClass(dr);
        row.appendChild(td);
      });
      tb.appendChild(row);
    });
    t.appendChild(tb);
    var wrap = document.createElement("div"); wrap.className = "table-wrap"; wrap.appendChild(t); container.appendChild(wrap);
  }

  // ---------------------------------------------------------------- 画面の状態
  var state = { method: "raw", overlay: false, level: D.defaultLevel, base: D.defaultBase, muni: null };
  var methodSel = document.getElementById("method"), otherSel = document.getElementById("method-other"),
    overlayChk = document.getElementById("overlay"), levelSel = document.getElementById("level"), baseSel = document.getElementById("base-year");

  function unitOf(m) { return METHODS[m].unit; }
  function totalsFor(ind) { var t = D.totals[ind]; return t ? t[state.level] : null; }

  function renderMuni(code) {
    var M = D.munis[code]; if (!M) return;
    var host = document.getElementById("panel-" + code); if (!host) return;
    var charts = host.querySelector(".charts");
    while (charts.firstChild) charts.removeChild(charts.firstChild);
    D.order.forEach(function (ind) {
      var pts = M.series[ind]; if (!pts || !pts.length) return;
      var meta = D.indicators[ind], tot = totalsFor(ind);
      var sec = document.createElement("section"); sec.className = "ind";
      var h = document.createElement("h3"); h.textContent = "指標 " + meta.no + " " + meta.label + "(" + meta.layer + ")"; sec.appendChild(h);
      var m = state.method;
      var needsT = METHODS[m].needsTotal;
      var note = document.createElement("p"); note.className = "small";
      note.textContent = "全体: " + (tot ? tot.series + (tot.note ? "。注: " + tot.note : "") : "未取得(この指標の全体系列はない)") + (needsT && !tot ? "。この方法は全体を使うため計算できない" : "");
      sec.appendChild(note);
      var rows = compute(m, pts, tot ? tot.points : null, state.base);
      var overlay = (m === "pct" && state.overlay) || m === "index";
      var lanes = [];
      (M.lanes[ind] || []).forEach(function (ln) { lanes.push(ln); });
      chart(sec, { title: M.name + " 指標 " + meta.no + " " + meta.label, rows: rows, unit: unitOf(m), unitLabel: METHODS[m].unitLabel,
        signed: METHODS[m].signed, overlay: overlay, overlayLabel: m === "index" ? "全体の指数" : "全体の前年比(total_pct)",
        elections: M.elections, lanes: lanes, laneTitle: ind.indexOf("kofu") === 0 ? "交付決定" : "申請・要望",
        caption: "図: " + M.name + " 指標 " + meta.no + " " + meta.label + "。方法: " + METHODS[m].name + "。横軸は decided_date。点・線の色は方法の値に閾値を当てた方向(増加=青、減少=赤、横ばい・未取得=灰)。" +
          (overlay ? "黒の破線は全体(" + (tot ? tot.series : "未取得") + ")。" : "") + "灰色の縦線 E は国政選挙の投票日。" });
      var det = document.createElement("details"), sum = document.createElement("summary");
      sum.textContent = "表と出典を表示"; det.appendChild(sum);
      table(det, { title: M.name + " 指標 " + meta.no, rows: rows, methodName: METHODS[m].name, unit: unitOf(m), unitLabel: METHODS[m].unitLabel, overlay: overlay, overlayLabel: "全体" });
      var src = document.createElement("div"); src.className = "source-note";
      src.textContent = "出典: " + (M.sources[ind] || []).join(" ") + (tot && tot.url ? " / 全体: " + tot.url : "") + (meta.basis ? "。decided_date の根拠: " + meta.basis : "");
      det.appendChild(src);
      sec.appendChild(det);
      charts.appendChild(sec);
    });
  }

  function renderTotals() {
    var host = document.getElementById("totals-charts"); if (!host) return;
    while (host.firstChild) host.removeChild(host.firstChild);
    var m = state.method;
    if (METHODS[m].needsShare) {
      var p = document.createElement("p"); p.className = "notice"; p.textContent = "「" + METHODS[m].name + "」は自治体と全体の比較の方法のため、全体ビューでは使わない。実額・前年比・指数などを選ぶ。"; host.appendChild(p); return;
    }
    D.order.forEach(function (ind) {
      var t = D.totals[ind]; if (!t) return;
      var meta = D.indicators[ind];
      var sec = document.createElement("section"); sec.className = "ind";
      var h = document.createElement("h3"); h.textContent = "指標 " + meta.no + " " + meta.label; sec.appendChild(h);
      ["national", "prefecture"].forEach(function (lv) {
        var s = t[lv]; if (!s) return;
        var mm = m === "total_pct" ? "pct" : m;
        var rows = compute(mm, s.points, null, state.base);
        chart(sec, { title: s.series, rows: rows, unit: unitOf(mm), unitLabel: METHODS[mm].unitLabel, signed: METHODS[mm].signed,
          elections: D.prefElections, lanes: [], caption: "図: " + s.series + "。方法: " + METHODS[mm].name + "。灰色の縦線 E は国政選挙の投票日。" });
        var det = document.createElement("details"), sum = document.createElement("summary"); sum.textContent = "表と出典を表示"; det.appendChild(sum);
        table(det, { title: s.series, rows: rows, methodName: METHODS[mm].name, unit: unitOf(mm), unitLabel: METHODS[mm].unitLabel });
        var src = document.createElement("div"); src.className = "source-note"; src.textContent = "出典: " + (s.url || "—") + (s.note ? "。注: " + s.note : ""); det.appendChild(src);
        sec.appendChild(det);
      });
      host.appendChild(sec);
    });
  }

  function rerender() {
    if (document.getElementById("totals-charts")) renderTotals();
    if (state.muni) renderMuni(state.muni);
    var lab = document.getElementById("method-label"); if (lab) lab.textContent = METHODS[state.method].name + "(" + METHODS[state.method].def + ")";
  }
  function setMethod(m) { state.method = m; rerender(); }
  if (methodSel) methodSel.addEventListener("change", function () { if (otherSel) otherSel.value = ""; setMethod(methodSel.value); });
  if (otherSel) otherSel.addEventListener("change", function () { if (otherSel.value) setMethod(otherSel.value); });
  if (overlayChk) overlayChk.addEventListener("change", function () { state.overlay = overlayChk.checked; rerender(); });
  if (levelSel) levelSel.addEventListener("change", function () { state.level = levelSel.value; rerender(); });
  if (baseSel) baseSel.addEventListener("change", function () { state.base = parseInt(baseSel.value, 10); rerender(); });

  // タブ(自治体ビュー)
  var muniSel = document.getElementById("muni-select");
  function selectMuni(code, push) {
    if (!D.munis[code]) return;
    state.muni = code;
    document.querySelectorAll("[role=tab]").forEach(function (t) { var on = t.getAttribute("data-code") === code; t.setAttribute("aria-selected", on ? "true" : "false"); t.tabIndex = on ? 0 : -1; });
    document.querySelectorAll("[role=tabpanel]").forEach(function (p) { p.hidden = p.id !== "panel-" + code; });
    if (muniSel) muniSel.value = code;
    if (push && history.replaceState) history.replaceState(null, "", "#" + code);
    renderMuni(code);
  }
  document.querySelectorAll("[role=tab]").forEach(function (t) {
    t.addEventListener("click", function () { selectMuni(t.getAttribute("data-code"), true); });
  });
  if (muniSel) muniSel.addEventListener("change", function () { selectMuni(muniSel.value, true); });
  if (D.munis && Object.keys(D.munis).length) {
    var start = (location.hash || "").replace("#", "");
    selectMuni(D.munis[start] ? start : D.firstMuni, false);
  }
  rerender();
})();
