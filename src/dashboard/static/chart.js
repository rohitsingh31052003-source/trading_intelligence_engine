/*
 * Lightweight candlestick chart for the trading-intelligence dashboard.
 *
 * The chart renders ONLY backend-authored data (candles + overlaid
 * levels). It NEVER invents technical levels, supports, resistances or
 * trade geometry. If a level is null it is simply not drawn.
 */
(function () {
  "use strict";

  function init() {
    var host = document.getElementById("chart-container");
    if (!host) return;
    var payload;
    try { payload = JSON.parse(host.getAttribute("data-payload") || "null"); }
    catch (e) { payload = null; }
    if (!payload || !payload.candles || !payload.candles.length) {
      host.innerHTML = '<p style="color:var(--muted)">No chart data available for the selected instrument / timeframe.</p>';
      return;
    }
    render(host, payload);
  }

  function render(host, payload) {
    var candles = payload.candles;
    var W = Math.max(host.clientWidth, 600);
    var H = 360;
    var padL = 64, padR = 12, padT = 12, padB = 40;
    var plotW = W - padL - padR;
    var plotH = H - padT - padB;

    var lows = candles.map(function (c) { return c.l; });
    var highs = candles.map(function (c) { return c.h; });
    var min = Math.min.apply(null, lows);
    var max = Math.max.apply(null, highs);

    // Include overlaid levels in the price range so they are visible.
    ["entry", "stop", "target_1", "support", "resistance", "invalidation_level"].forEach(function (k) {
      var v = payload[k];
      if (v != null) { min = Math.min(min, v); max = Math.max(max, v); }
    });
    if (min === max) { max = min + 1; }
    var pad = (max - min) * 0.05;
    min -= pad; max += pad;

    var n = candles.length;
    var cw = plotW / n;
    var bodyW = Math.max(2, cw * 0.6);

    var canvas = document.createElement("canvas");
    canvas.width = W; canvas.height = H;
    var ctx = canvas.getContext("2d");

    function y(price) {
      return padT + plotH * (1 - (price - min) / (max - min));
    }

    // Grid + price axis.
    ctx.strokeStyle = "#2a3138"; ctx.fillStyle = "#8b949e";
    ctx.font = "10px sans-serif"; ctx.lineWidth = 1;
    var ticks = 5;
    for (var i = 0; i <= ticks; i++) {
      var price = min + (max - min) * (i / ticks);
      var yy = y(price);
      ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(W - padR, yy); ctx.stroke();
      ctx.fillText(price.toFixed(2), 6, yy + 3);
    }

    // Candles.
    candles.forEach(function (c, idx) {
      var x = padL + idx * cw + cw / 2;
      var up = c.c >= c.o;
      ctx.strokeStyle = up ? "#3fb950" : "#f85149";
      ctx.fillStyle = up ? "#3fb950" : "#f85149";
      ctx.beginPath(); ctx.moveTo(x, y(c.h)); ctx.lineTo(x, y(c.l)); ctx.stroke();
      var yo = y(c.o), yc = y(c.c);
      var top = Math.min(yo, yc), ht = Math.max(1, Math.abs(yc - yo));
      ctx.fillRect(x - bodyW / 2, top, bodyW, ht);
    });

    // Overlaid levels (backend-authored only).
    function levelLine(value, color, label) {
      if (value == null) return;
      var yy = y(value);
      ctx.strokeStyle = color; ctx.fillStyle = color;
      ctx.setLineDash([4, 3]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(W - padR, yy); ctx.stroke();
      ctx.setLineDash([]); ctx.lineWidth = 1;
      ctx.fillText(label + " " + value.toFixed(2), W - padR - 90, yy - 4);
    }
    levelLine(payload.support, "#8b949e", "S");
    levelLine(payload.resistance, "#8b949e", "R");
    levelLine(payload.target_1, "#3fb950", "T1");
    levelLine(payload.entry, "#58a6ff", "E");
    levelLine(payload.invalidation_level, "#f85149", "INV");
    levelLine(payload.stop, "#f85149", "SL");

    host.innerHTML = "";
    host.appendChild(canvas);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
