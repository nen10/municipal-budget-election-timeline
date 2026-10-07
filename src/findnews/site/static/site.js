// 表のソート・絞り込みと、グラフのホバー表示のみ。外部依存なし。挿入する文字は textContent を使う。
(function () {
  "use strict";
  function cellKey(td) {
    var v = td.getAttribute("data-value");
    if (v !== null && v !== "") { var n = parseFloat(v); if (!isNaN(n)) return { n: n }; }
    return { s: (td.textContent || "").trim() };
  }
  document.querySelectorAll("table").forEach(function (table) {
    table.querySelectorAll("thead th[data-sort]").forEach(function (th) {
      th.tabIndex = 0;
      function sort() {
        var idx = Array.prototype.indexOf.call(th.parentNode.children, th);
        var dir = th.getAttribute("aria-sort") === "ascending" ? "descending" : "ascending";
        th.parentNode.querySelectorAll("th").forEach(function (o) { o.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", dir);
        var body = table.tBodies[0];
        var rows = Array.prototype.slice.call(body.rows);
        rows.sort(function (a, b) {
          var x = cellKey(a.cells[idx]), y = cellKey(b.cells[idx]);
          var r;
          if (x.n !== undefined && y.n !== undefined) r = x.n - y.n;
          else if (x.n !== undefined) r = -1;
          else if (y.n !== undefined) r = 1;
          else r = x.s.localeCompare(y.s, "ja");
          return dir === "ascending" ? r : -r;
        });
        rows.forEach(function (r) { body.appendChild(r); });
      }
      th.addEventListener("click", sort);
      th.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); } });
    });
  });

  // 絞り込み: <div class="filters" data-target="tableId"> 内の select[data-col] / input[data-col]
  document.querySelectorAll(".filters[data-target]").forEach(function (box) {
    var table = document.getElementById(box.getAttribute("data-target"));
    if (!table) return;
    var controls = box.querySelectorAll("[data-col]");
    var counter = box.querySelector("[data-count]");
    function apply() {
      var shown = 0;
      Array.prototype.forEach.call(table.tBodies[0].rows, function (tr) {
        var ok = true;
        controls.forEach(function (c) {
          var v = (c.value || "").trim();
          if (!v) return;
          var cell = tr.cells[parseInt(c.getAttribute("data-col"), 10)];
          var t = (cell ? cell.textContent : "");
          if (c.tagName === "SELECT" ? t.trim() !== v : t.indexOf(v) < 0) ok = false;
        });
        tr.hidden = !ok;
        if (ok) shown++;
      });
      if (counter) counter.textContent = shown + " 件";
    }
    controls.forEach(function (c) { c.addEventListener("input", apply); c.addEventListener("change", apply); });
    apply();
  });

  // グラフのホバー: 観測点に吸着する縦線と、イベント線の説明。値は表にもある(ホバーは補助)。
  var tip = document.createElement("div");
  tip.className = "tooltip"; tip.hidden = true; document.body.appendChild(tip);
  function show(evt, lines) {
    while (tip.firstChild) tip.removeChild(tip.firstChild);
    lines.forEach(function (l, i) {
      var div = document.createElement("div");
      if (i === 0) { var s = document.createElement("strong"); s.textContent = l; div.appendChild(s); }
      else div.textContent = l;
      tip.appendChild(div);
    });
    tip.hidden = false;
    tip.style.left = (evt.pageX + 12) + "px";
    tip.style.top = (evt.pageY + 12) + "px";
  }
  function hide() { tip.hidden = true; }
  document.querySelectorAll("svg[data-points]").forEach(function (svg) {
    var pts = JSON.parse(svg.getAttribute("data-points"));
    var cross = svg.querySelector(".crosshair");
    var plot = svg.querySelector(".plot-area");
    function nearest(evt) {
      var pt = svg.createSVGPoint(); pt.x = evt.clientX; pt.y = evt.clientY;
      var p = pt.matrixTransform(svg.getScreenCTM().inverse());
      var best = null;
      pts.forEach(function (d) { if (best === null || Math.abs(d.x - p.x) < Math.abs(best.x - p.x)) best = d; });
      return best;
    }
    if (plot) {
      plot.addEventListener("pointermove", function (evt) {
        var d = nearest(evt); if (!d) return;
        cross.setAttribute("x1", d.x); cross.setAttribute("x2", d.x); cross.removeAttribute("visibility");
        show(evt, d.lines);
      });
      plot.addEventListener("pointerleave", function () { cross.setAttribute("visibility", "hidden"); hide(); });
    }
    svg.querySelectorAll(".event-hit").forEach(function (ln) {
      function on(evt) { show(evt, JSON.parse(ln.getAttribute("data-lines"))); }
      ln.addEventListener("pointermove", on);
      ln.addEventListener("focus", function () {
        var r = ln.getBoundingClientRect();
        on({ pageX: r.left + window.scrollX, pageY: r.top + window.scrollY });
      });
      ln.addEventListener("pointerleave", hide);
      ln.addEventListener("blur", hide);
    });
  });
})();
