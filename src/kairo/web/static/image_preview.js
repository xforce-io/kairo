(function () {
  function clampOffset(displayW, displayH, vw, vh, ox, oy) {
    var minX = Math.min(0, vw - displayW);
    var minY = Math.min(0, vh - displayH);
    return {
      x: Math.min(0, Math.max(minX, ox)),
      y: Math.min(0, Math.max(minY, oy))
    };
  }

  function apply(root, img) {
    var w = Number(root.dataset.displayWidth);
    var ox = Number(root.dataset.offsetX) || 0;
    var oy = Number(root.dataset.offsetY) || 0;
    img.style.width = w + "px";
    img.style.transform = "translate(" + ox + "px," + oy + "px)";
  }

  function clamp(root, img, vp) {
    var w = Number(root.dataset.displayWidth);
    var h = img.naturalHeight * (w / img.naturalWidth);
    var next = clampOffset(
      w,
      h,
      vp.clientWidth,
      vp.clientHeight,
      Number(root.dataset.offsetX) || 0,
      Number(root.dataset.offsetY) || 0
    );
    root.dataset.offsetX = String(next.x);
    root.dataset.offsetY = String(next.y);
  }

  function ensureLayout(root) {
    var vp = root.querySelector(".img-preview-viewport");
    var img = root.querySelector(".doc-img");
    if (!vp || !img || !img.naturalWidth || !img.naturalHeight) return;
    if (vp.clientWidth < 2 || vp.clientHeight < 2) return;
    if (root.getAttribute("data-img-ready") === "1") return;
    var opened = Math.min(img.naturalWidth, vp.clientWidth);
    root.dataset.openedWidth = String(opened);
    root.dataset.displayWidth = String(opened);
    root.dataset.offsetX = "0";
    root.dataset.offsetY = "0";
    apply(root, img);
    root.setAttribute("data-img-ready", "1");
    root.classList.add("is-ready");
  }

  function bind(root) {
    if (root.getAttribute("data-img-bound") === "1") return;
    root.setAttribute("data-img-bound", "1");
    var vp = root.querySelector(".img-preview-viewport");
    var img = root.querySelector(".doc-img");
    if (!vp || !img) return;

    function kick() {
      ensureLayout(root);
    }
    img.addEventListener("load", kick);
    if (img.complete) kick();
    if (window.ResizeObserver) {
      var obs = new ResizeObserver(kick);
      obs.observe(vp);
    }

    var zoomIn = root.querySelector("[data-img-zoom-in]");
    var zoomOut = root.querySelector("[data-img-zoom-out]");
    var fit = root.querySelector("[data-img-fit]");

    function requireReady() {
      ensureLayout(root);
      return root.getAttribute("data-img-ready") === "1";
    }

    if (zoomIn) {
      zoomIn.addEventListener("click", function () {
        if (!requireReady()) return;
        var opened = Number(root.dataset.openedWidth);
        var current = Number(root.dataset.displayWidth);
        var ratio = Number(root.dataset.zoomRatio);
        var cap = opened * Number(root.dataset.maxZoom);
        var next = Math.min(current * ratio, cap);
        if (current <= opened) next = Math.min(Math.max(next, opened * ratio), cap);
        root.dataset.displayWidth = String(next);
        clamp(root, img, vp);
        apply(root, img);
      });
    }
    if (zoomOut) {
      zoomOut.addEventListener("click", function () {
        if (!requireReady()) return;
        var opened = Number(root.dataset.openedWidth);
        var current = Number(root.dataset.displayWidth);
        var ratio = Number(root.dataset.zoomRatio);
        var next = Math.max(opened, current / ratio);
        root.dataset.displayWidth = String(next);
        if (next <= opened) {
          root.dataset.offsetX = "0";
          root.dataset.offsetY = "0";
        }
        clamp(root, img, vp);
        apply(root, img);
      });
    }
    if (fit) {
      fit.addEventListener("click", function () {
        if (!requireReady()) return;
        root.dataset.displayWidth = root.dataset.openedWidth;
        root.dataset.offsetX = "0";
        root.dataset.offsetY = "0";
        apply(root, img);
      });
    }

    var drag = null;
    vp.addEventListener("pointerdown", function (e) {
      if (!requireReady()) return;
      if (e.button != null && e.button !== 0) return;
      drag = {
        id: e.pointerId,
        x: e.clientX,
        y: e.clientY,
        ox: Number(root.dataset.offsetX) || 0,
        oy: Number(root.dataset.offsetY) || 0
      };
      vp.classList.add("is-dragging");
      if (vp.setPointerCapture) vp.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    vp.addEventListener("pointermove", function (e) {
      if (!drag || e.pointerId !== drag.id) return;
      root.dataset.offsetX = String(drag.ox + (e.clientX - drag.x));
      root.dataset.offsetY = String(drag.oy + (e.clientY - drag.y));
      clamp(root, img, vp);
      apply(root, img);
    });
    function endDrag(e) {
      if (!drag || (e && e.pointerId !== drag.id)) return;
      drag = null;
      vp.classList.remove("is-dragging");
    }
    vp.addEventListener("pointerup", endDrag);
    vp.addEventListener("pointercancel", endDrag);
  }

  function scan(scope) {
    var root = scope && scope.querySelectorAll ? scope : document;
    root.querySelectorAll(".img-preview").forEach(bind);
  }

  function start() {
    scan(document);
    // OOB 换入 #reader 时，afterSwap 的 target 往往不是预览节点。
    function rescan() {
      scan(document);
    }
    document.body.addEventListener("htmx:afterSwap", rescan);
    document.body.addEventListener("htmx:afterSettle", rescan);
    document.body.addEventListener("htmx:oobAfterSwap", rescan);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
