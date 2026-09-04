(function (root) {
  function fmt(t) {
    if (!isFinite(t) || t < 0) t = 0;
    var m = Math.floor(t / 60);
    var s = t - m * 60;
    return m + ":" + (s < 10 ? "0" : "") + s.toFixed(1);
  }

  function kairoFilterUnits(units, duration) {
    var out = [];
    units.forEach(function (u) {
      if (u.start == null || u.start === "") {
        out.push({ start: null, end: null, text: u.text || "" });
        return;
      }
      var start = Number(u.start);
      if (start >= duration) {
        if (out.length) out[out.length - 1].text += "\n" + (u.text || "");
        else out.push({ start: null, end: null, text: u.text || "" });
        return;
      }
      out.push({ start: start, end: u.end == null ? null : Number(u.end), text: u.text || "" });
    });
    var timed = [];
    out.forEach(function (u, i) { if (u.start != null) timed.push(i); });
    timed.forEach(function (i, n) {
      out[i].end = n + 1 < timed.length ? out[timed[n + 1]].start : duration;
    });
    return out;
  }

  function kairoStopListenRead(scope) {
    var rootEl = scope && scope.querySelectorAll ? scope : document;
    rootEl.querySelectorAll("audio.lr-audio").forEach(function (a) {
      a.pause();
      a.removeAttribute("src");
      a.load();
    });
  }

  function kairoSearchUnits(units, query) {
    var needle = String(query || "").trim().toLowerCase();
    if (!needle) return [];
    var hits = [];
    units.forEach(function (u, i) {
      var text = u.text || "";
      if (text.toLowerCase().indexOf(needle) === -1) return;
      var start = u.start;
      if (start === "" || start == null) start = null;
      else {
        start = Number(start);
        if (!isFinite(start)) start = null;
      }
      hits.push({ text: text, start: start, index: i });
    });
    return hits;
  }

  function kairoSearchView(hits, opts) {
    opts = opts || {};
    var query = opts.query;
    var empty = opts.empty || "0 results";
    if (query !== undefined && !String(query).trim()) {
      return { count: 0, status: "", hidden: true };
    }
    var count = hits.length;
    return { count: count, status: count === 0 ? empty : String(count), hidden: false };
  }

  function kairoHitIndexes(hits) {
    return hits.map(function (h) { return h.index; });
  }

  function kairoUnitRecord(el) {
    var textEl = el.querySelector ? el.querySelector(".lr-unit-text") : null;
    return {
      text: ((textEl || el).textContent || ""),
      start: el.getAttribute ? el.getAttribute("data-start") : null,
    };
  }

  function kairoApplySearch(box) {
    var q = box.querySelector("[data-lr-q]");
    var hitsEl = box.querySelector("[data-lr-hits]");
    if (!q || !hitsEl) return null;
    var els = Array.prototype.slice.call(box.querySelectorAll(".lr-unit"));
    var found = kairoSearchUnits(els.map(kairoUnitRecord), q.value);
    var view = kairoSearchView(found, {
      empty: hitsEl.getAttribute("data-empty") || "0 results",
      query: q.value,
    });
    var hitIdx = {};
    kairoHitIndexes(found).forEach(function (i) { hitIdx[i] = true; });
    els.forEach(function (el, i) {
      el.classList.toggle("is-hit", !!hitIdx[i]);
    });
    hitsEl.innerHTML = "";
    hitsEl.hidden = view.hidden;
    if (view.hidden) return view;
    var status = document.createElement("div");
    status.className = "lr-search-status";
    status.setAttribute("data-lr-status", "");
    status.textContent = view.status;
    hitsEl.appendChild(status);
    found.forEach(function (h) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "lr-hit";
      if (h.start != null) b.setAttribute("data-start", String(h.start));
      b.setAttribute("data-unit", String(h.index));
      b.textContent = String(h.text || "").trim();
      hitsEl.appendChild(b);
    });
    return view;
  }

  function kairoLocateHit(audio, hitEl, unitEls) {
    var idx = hitEl.getAttribute("data-unit");
    if (idx != null && idx !== "") {
      var el = unitEls[Number(idx)];
      if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
    }
    var start = parseFloat(hitEl.getAttribute("data-start"));
    if (!audio || !isFinite(start)) return start;
    audio.currentTime = start;
    if (audio.play) audio.play();
    return start;
  }

  function readUnits(box) {
    return Array.prototype.slice.call(box.querySelectorAll(".lr-unit")).map(function (el) {
      var start = el.getAttribute("data-start");
      return {
        start: start === null || start === "" ? null : start,
        end: el.getAttribute("data-end"),
        text: ((el.querySelector(".lr-unit-text") || el).textContent || ""),
      };
    });
  }

  function renderUnits(box, units) {
    var host = box.querySelector("[data-lr-units]");
    if (!host) return;
    host.innerHTML = "";
    units.forEach(function (u) {
      if (u.start == null) {
        var div = document.createElement("div");
        div.className = "lr-unit is-untimed";
        div.innerHTML = '<span class="lr-unit-text"></span>';
        div.querySelector(".lr-unit-text").textContent = u.text;
        host.appendChild(div);
        return;
      }
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "lr-unit";
      btn.setAttribute("data-start", String(u.start));
      btn.setAttribute("data-end", String(u.end));
      var time = document.createElement("span");
      time.className = "lr-unit-time";
      time.textContent = fmt(u.start);
      var text = document.createElement("span");
      text.className = "lr-unit-text";
      text.textContent = u.text;
      btn.appendChild(time);
      btn.appendChild(text);
      host.appendChild(btn);
    });
  }

  function bind(box) {
    if (!box || box.dataset.lrBound) return;
    var audio = box.querySelector("[data-lr-audio]");
    var play = box.querySelector("[data-lr-play]");
    var seek = box.querySelector("[data-lr-seek]");
    var timeEl = box.querySelector("[data-lr-time]");
    var q = box.querySelector("[data-lr-q]");
    var hitsEl = box.querySelector("[data-lr-hits]");
    if (!audio) return;
    box.dataset.lrBound = "1";

    function units() {
      return Array.prototype.slice.call(box.querySelectorAll(".lr-unit[data-start]"));
    }

    function unitEls() {
      return Array.prototype.slice.call(box.querySelectorAll(".lr-unit"));
    }

    function highlight(t) {
      units().forEach(function (el) {
        var start = parseFloat(el.getAttribute("data-start"));
        var end = parseFloat(el.getAttribute("data-end"));
        var on = isFinite(start) && isFinite(end) && end > start && t >= start && t < end;
        el.classList.toggle("on", on);
        if (on) el.scrollIntoView({ block: "nearest" });
      });
    }

    function sync() {
      var t = audio.currentTime || 0;
      var d = audio.duration;
      if (timeEl) timeEl.textContent = fmt(t);
      if (seek && isFinite(d) && d > 0) seek.value = String(Math.round((t / d) * 1000));
      highlight(t);
    }

    audio.addEventListener("loadedmetadata", function () {
      if (isFinite(audio.duration) && audio.duration > 0) {
        renderUnits(box, kairoFilterUnits(readUnits(box), audio.duration));
      }
      sync();
    });
    audio.addEventListener("timeupdate", sync);

    if (play) {
      play.addEventListener("click", function () {
        if (audio.paused) audio.play();
        else audio.pause();
      });
      audio.addEventListener("play", function () { play.textContent = play.getAttribute("data-pause") || "Pause"; });
      audio.addEventListener("pause", function () { play.textContent = play.getAttribute("data-play") || "Play"; });
    }

    if (seek) {
      seek.addEventListener("input", function () {
        var d = audio.duration;
        if (!isFinite(d) || d <= 0) return;
        audio.currentTime = (Number(seek.value) / 1000) * d;
        sync();
      });
    }

    box.addEventListener("click", function (e) {
      var hit = e.target.closest(".lr-hit");
      if (hit && box.contains(hit)) {
        kairoLocateHit(audio, hit, unitEls());
        sync();
        return;
      }
      var unit = e.target.closest(".lr-unit[data-start]");
      if (!unit || !box.contains(unit)) return;
      var start = parseFloat(unit.getAttribute("data-start"));
      if (!isFinite(start)) return;
      audio.currentTime = start;
      audio.play();
      sync();
    });

    if (q && hitsEl) {
      q.addEventListener("input", function () { kairoApplySearch(box); });
    }
  }

  function scan(scope) {
    var el = scope && scope.querySelectorAll ? scope : document;
    el.querySelectorAll(".listen-read").forEach(bind);
    if (scope && scope.classList && scope.classList.contains("listen-read")) bind(scope);
  }

  if (typeof document !== "undefined") {
    document.addEventListener("htmx:beforeSwap", function (e) {
      kairoStopListenRead(e.detail && e.detail.target);
    });
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () { scan(document); });
    } else {
      scan(document);
    }
    document.addEventListener("htmx:afterSwap", function (e) { scan(e.detail && e.detail.target); });
    document.addEventListener("htmx:oobAfterSwap", function (e) { scan(e.detail && e.detail.target); });
  }

  root.kairoFilterUnits = kairoFilterUnits;
  root.kairoStopListenRead = kairoStopListenRead;
  root.kairoSearchUnits = kairoSearchUnits;
  root.kairoSearchView = kairoSearchView;
  root.kairoHitIndexes = kairoHitIndexes;
  root.kairoApplySearch = kairoApplySearch;
  root.kairoLocateHit = kairoLocateHit;
  root.kairoListenRead = bind;
})(typeof window !== "undefined" ? window : globalThis);
