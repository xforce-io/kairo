(function () {
  var MERMAID_SRC = "/static/mermaid.min.js?v=11.4.1";
  var loadPromise = null;
  var renderSeq = 0;
  var hook = document.currentScript;
  var errorText = (hook && hook.getAttribute("data-mermaid-error")) || "This diagram could not be drawn.";

  function fail(el) {
    var err = document.createElement("p");
    err.className = "doc-mermaid-error";
    err.setAttribute("role", "alert");
    err.textContent = errorText;
    el.replaceWith(err);
  }

  function loadMermaid() {
    if (window.mermaid) return Promise.resolve(window.mermaid);
    if (loadPromise) return loadPromise;
    loadPromise = new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = MERMAID_SRC;
      s.onload = function () {
        if (!window.mermaid) {
          reject(new Error("mermaid missing"));
          return;
        }
        window.mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "neutral"
        });
        resolve(window.mermaid);
      };
      s.onerror = function () { reject(new Error("mermaid load failed")); };
      document.head.appendChild(s);
    });
    return loadPromise;
  }

  function svgFrom(svgText) {
    var doc = new DOMParser().parseFromString(svgText, "image/svg+xml");
    var node = doc.documentElement;
    if (!node || node.tagName.toLowerCase() !== "svg" || node.querySelector("parsererror")) {
      throw new Error("invalid svg");
    }
    node.querySelectorAll("script").forEach(function (s) { s.remove(); });
    return document.importNode(node, true);
  }

  function kairoRenderMermaid(root) {
    var scope = root && root.querySelectorAll ? root : document;
    var nodes = scope.querySelectorAll(".doc-mermaid:not([data-done])");
    if (!nodes.length) return;
    loadMermaid().then(function (mermaid) {
      var list = Array.prototype.slice.call(nodes);
      var chain = Promise.resolve();
      list.forEach(function (el) {
        chain = chain.then(function () {
          if (!el.isConnected || el.getAttribute("data-done")) return;
          var srcEl = el.querySelector(".doc-mermaid-src");
          var src = srcEl ? srcEl.textContent : "";
          var id = "kairo-mmd-" + (++renderSeq);
          return mermaid.render(id, src).then(function (out) {
            el.replaceChildren(svgFrom(out.svg));
            el.setAttribute("data-done", "1");
          }).catch(function () { fail(el); });
        });
      });
      return chain;
    }).catch(function () {
      Array.prototype.forEach.call(nodes, function (el) {
        if (el.isConnected && !el.getAttribute("data-done")) fail(el);
      });
    });
  }

  function onSwap(e) {
    kairoRenderMermaid((e.detail && e.detail.target) || document);
  }

  function bind() {
    document.body.addEventListener("htmx:afterSwap", onSwap);
    kairoRenderMermaid(document);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
