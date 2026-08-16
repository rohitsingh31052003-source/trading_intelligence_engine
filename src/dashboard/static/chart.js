/*
 * Lightweight candlestick chart for the trading-intelligence dashboard.
 *
 * The chart renders ONLY backend-authored data (candles + overlaid
 * levels). It NEVER invents technical levels, supports, resistances or
 * trade geometry. If a level is null it is simply not drawn. It NEVER
 * recomputes risk/reward — the R:R shading uses the backend-supplied
 * entry / stop / target values verbatim.
 *
 * Trade-review features:
 *   - the latest COMPLETED analysis candle is highlighted;
 *   - entry / stop / target lines are colour-coded and labelled;
 *   - when entry + stop + target are all present, a translucent R:R
 *     zone is drawn (risk band = entry..stop, reward band = entry..target);
 *   - when geometry is unavailable, a "TRADE GEOMETRY UNAVAILABLE"
 *     overlay is drawn over the plot (no invented levels);
 *   - the canvas is sized responsively to its container width.
 */
(function () {
  "use strict";

  function init() {
    var host = document.getElementById("chart-container");
    if (!host) return;
    var payload;
    try { payload = JSON.parse(host.getAttribute("data-payload") || "null"); }
    catch (e) { payload = null; }
    var geomAvailable = host.getAttribute("data-geom-available") === "true";
    if (!payload || !payload.candles || !payload.candles.length) {
      host.innerHTML = '<p style="color:var(--muted)">No chart data available for the selected instrument / timeframe.</p>';
      return;
    }
    render(host, payload, geomAvailable);
  }

  function render(host, payload, geomAvailable) {
    var candles = payload.candles;
    var W = Math.max(host.clientWidth || 600, 320);
    var H = 380;
    var padL = 64, padR = 14, padT = 14, padB = 42;
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
    canvas.style.maxWidth = "100%";
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

    // R:R shading (backend-authored levels only) — drawn beneath candles.
    var entry = payload.entry, stop = payload.stop, target1 = payload.target_1;
    var rrDrawable = geomAvailable && entry != null && stop != null && target1 != null;
    if (rrDrawable) {
      // Risk band (entry -> stop).
      ctx.fillStyle = "rgba(248,81,73,0.10)";
      ctx.fillRect(padL, Math.min(y(entry), y(stop)), plotW, Math.abs(y(stop) - y(entry)));
      // Reward band (entry -> target_1).
      ctx.fillStyle = "rgba(63,185,80,0.10)";
      ctx.fillRect(padL, Math.min(y(entry), y(target1)), plotW, Math.abs(y(target1) - y(entry)));
    }

    // Candles.
    var lastIdx = candles.length - 1;
    candles.forEach(function (c, idx) {
      var x = padL + idx * cw + cw / 2;
      var up = c.c >= c.o;
      ctx.strokeStyle = up ? "#3fb950" : "#f85149";
      ctx.fillStyle = up ? "#3fb950" : "#f85149";
      ctx.beginPath(); ctx.moveTo(x, y(c.h)); ctx.lineTo(x, y(c.l)); ctx.stroke();
      var yo = y(c.o), yc = y(c.c);
      var top = Math.min(yo, yc), ht = Math.max(1, Math.abs(yc - yo));
      ctx.fillRect(x - bodyW / 2, top, bodyW, ht);
      // Highlight the latest completed analysis candle.
      if (idx === lastIdx) {
        ctx.save();
        ctx.strokeStyle = "#58a6ff"; ctx.lineWidth = 1.5; ctx.setLineDash([3, 3]);
        ctx.strokeRect(x - cw / 2, padT, cw, plotH);
        ctx.restore();
        // Marker label.
        ctx.fillStyle = "#58a6ff";
        ctx.fillText("latest", x - 14, padT + 9);
      }
    });

    // Overlaid levels (backend-authored only) — visually distinct.
    function levelLine(value, color, label, dash) {
      if (value == null) return;
      var yy = y(value);
      ctx.strokeStyle = color; ctx.fillStyle = color;
      ctx.setLineDash(dash || [4, 3]); ctx.lineWidth = 1.4;
      ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(W - padR, yy); ctx.stroke();
      ctx.setLineDash([]); ctx.lineWidth = 1;
      // Label pill on the right.
      var txt = label + " " + value.toFixed(2);
      ctx.font = "bold 10px sans-serif";
      var tw = ctx.measureText(txt).width + 8;
      ctx.fillStyle = color;
      ctx.fillRect(W - padR - tw - 2, yy - 7, tw, 13);
      ctx.fillStyle = "#0f1419";
      ctx.fillText(txt, W - padR - tw + 2, yy + 3);
      ctx.font = "10px sans-serif";
      ctx.fillStyle = color;
    }
    // Order: structural first, then trade geometry on top.
    levelLine(payload.support, "#8b949e", "S");
    levelLine(payload.resistance, "#8b949e", "R");
    levelLine(payload.target_1, "#3fb950", "T1", [6, 3]);
    levelLine(payload.entry, "#58a6ff", "E", [6, 3]);
    levelLine(payload.invalidation_level, "#f85149", "INV", [2, 2]);
    levelLine(payload.stop, "#f85149", "SL", [2, 2]);

    // Geometry-unavailable overlay — no invented levels.
    if (!geomAvailable) {
      ctx.save();
      ctx.fillStyle = "rgba(15,20,25,0.55)";
      ctx.fillRect(padL, padT, plotW, plotH);
      ctx.fillStyle = "#f85149";
      ctx.font = "bold 13px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("TRADE GEOMETRY UNAVAILABLE", padL + plotW / 2, padT + plotH / 2);
      ctx.fillStyle = "#8b949e";
      ctx.font = "10px sans-serif";
      ctx.fillText("entry / stop / target not all produced; no level fabricated",
        padL + plotW / 2, padT + plotH / 2 + 16);
      ctx.textAlign = "left";
      ctx.restore();
    }

    host.innerHTML = "";
    host.appendChild(canvas);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
