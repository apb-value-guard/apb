/* paymentsbenchmark.com · progressive enhancement only.
   Every word and number is already in the HTML. This file adds motion, the replay feed,
   the view and case toggles, chart tooltips, the clock and copy buttons. */
(function () {
  "use strict";
  var doc = document;
  var root = doc.documentElement;
  var each = function (sel, fn, scope) { Array.prototype.forEach.call((scope || doc).querySelectorAll(sel), fn); };
  var reduce = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  var canObserve = "IntersectionObserver" in window;

  // ---- 1. Clock (UTC) ------------------------------------------------------
  each("[data-clock]", function (el) {
    var tick = function () { el.textContent = new Date().toUTCString().slice(17, 25) + " UTC"; };
    tick();
    setInterval(tick, 1000);
  });

  // ---- 2. Segmented controls with a sliding indicator ---------------------
  var main = doc.getElementById("content");
  function placeIndicator(seg) {
    var ind = seg.querySelector(".seg-ind");
    var on = seg.querySelector("button[aria-pressed=true]");
    if (!ind || !on) return;
    ind.style.width = on.offsetWidth + "px";
    ind.style.transform = "translateX(" + on.offsetLeft + "px)";
  }
  each("[data-seg]", function (seg) {
    var name = seg.getAttribute("data-seg");
    seg.addEventListener("click", function (e) {
      var btn = e.target.closest("button");
      if (!btn) return;
      each("button", function (b) { b.setAttribute("aria-pressed", b === btn ? "true" : "false"); }, seg);
      placeIndicator(seg);
      if (main) main.setAttribute("data-" + name, btn.getAttribute("data-val"));
    });
    placeIndicator(seg);
  });
  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { each("[data-seg]", placeIndicator); }, 120);
  });
  if (doc.fonts && doc.fonts.ready) doc.fonts.ready.then(function () { each("[data-seg]", placeIndicator); });

  // ---- 3. Copy buttons ------------------------------------------------------
  each(".code", function (block) {
    var btn = block.querySelector(".copy");
    var pre = block.querySelector("pre");
    if (!btn || !pre) return;
    btn.hidden = false;
    var label = btn.querySelector("span");
    var say = function (t) {
      if (label) label.textContent = t;
      setTimeout(function () { if (label) label.textContent = "Copy"; }, 1600);
    };
    var selectIt = function () {
      var r = doc.createRange(); r.selectNodeContents(pre);
      var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
      say("Selected");
    };
    btn.addEventListener("click", function () {
      var text = pre.innerText.replace(/\n+$/, "");
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { say("Copied"); }, selectIt);
      } else { selectIt(); }
    });
  });

  // ---- 4. Chart tooltips -----------------------------------------------------
  each("[data-tipchart]", function (svg) {
    var wrap = svg.parentNode;
    var tip = wrap.querySelector(".tip");
    if (!tip) return;
    var rows = [["naive", "naive_agent", "var(--chart-5)"], ["llm", "llm_agent · observed", "var(--chart-2)"],
                ["guarded", "guarded_agent", "var(--chart-1)"]];
    each(".grp", function (g) {
      var show = function () {
        var html = "<b>" + g.getAttribute("data-name") + "</b>";
        rows.forEach(function (r) {
          html += '<span><i class="sw" style="background:' + r[2] + '"></i>' + r[1] + "<em>" + g.getAttribute("data-" + r[0]) + "</em></span>";
        });
        tip.innerHTML = html;
        var gb = g.getBoundingClientRect(), wb = wrap.getBoundingClientRect();
        var x = gb.left - wb.left + wrap.scrollLeft + gb.width + 8;
        if (x + 190 > wrap.scrollLeft + wb.width) x = gb.left - wb.left + wrap.scrollLeft - 198;
        tip.style.left = Math.max(4, x) + "px";
        tip.style.top = "28px";
        tip.classList.add("on");
      };
      g.addEventListener("mouseenter", show);
      g.addEventListener("mouseleave", function () { tip.classList.remove("on"); });
    }, svg);
  });

  // ---- 5. Table of contents highlight (method page) -------------------------
  var tocLinks = doc.querySelectorAll(".toc a[href*='#']");
  if (tocLinks.length && canObserve) {
    var byId = {};
    Array.prototype.forEach.call(tocLinks, function (a) { byId[a.getAttribute("href").split("#")[1]] = a; });
    var tocIo = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting && byId[e.target.id]) {
          Array.prototype.forEach.call(tocLinks, function (a) { a.classList.remove("on"); });
          byId[e.target.id].classList.add("on");
        }
      });
    }, { rootMargin: "-20% 0px -70% 0px" });
    Object.keys(byId).forEach(function (id) { var t = doc.getElementById(id); if (t) tocIo.observe(t); });
  }

  // ---- Motion below this line ------------------------------------------------
  if (reduce) {
    each("svg.flow", function (s) { if (s.pauseAnimations) s.pauseAnimations(); });
    return;
  }
  if (!canObserve) return;
  root.classList.add("anim");

  // ---- 6. Count-up numbers (1.6 s ease-out, once in view) -------------------
  function countUp(el) {
    var to = parseFloat(el.getAttribute("data-count"));
    var dec = parseInt(el.getAttribute("data-dec") || "0", 10);
    var pre = el.getAttribute("data-pre") || "";
    var fmt = function (v) {
      return pre + v.toLocaleString("en-GB", { minimumFractionDigits: dec, maximumFractionDigits: dec });
    };
    var t0 = null, dur = 1600;
    var step = function (ts) {
      if (t0 === null) t0 = ts;
      var p = Math.min(1, (ts - t0) / dur);
      var eased = 1 - Math.pow(1 - p, 3);
      el.textContent = fmt(to * eased);
      if (p < 1) requestAnimationFrame(step); else el.textContent = fmt(to);
    };
    el.textContent = fmt(0);
    requestAnimationFrame(step);
  }

  // ---- 7. In-view triggers (bars, donut, radar, ticks, fills, counters) -----
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (!e.isIntersecting) return;
      e.target.classList.add("is-in");
      if (e.target.hasAttribute("data-count")) countUp(e.target);
      io.unobserve(e.target);
    });
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0 });
  each("[data-inview],[data-count]", function (el) {
    if (el.hasAttribute("data-count")) {
      var r = el.getBoundingClientRect();
      if (r.top > (window.innerHeight || root.clientHeight)) el.textContent = el.textContent.replace(/[0-9][0-9,.]*/, "0");
    }
    io.observe(el);
  });

  // ---- 8. Replay feed of observed runs --------------------------------------
  each("[data-feed]", function (feed) {
    var src = doc.getElementById("feed-data");
    if (!src) return;
    var items;
    try { items = JSON.parse(src.textContent); } catch (err) { return; }
    var idx = 5, visible = true, timer = null;
    var esc = function (s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
    var make = function (x) {
      var d = doc.createElement("div");
      d.className = "feed-row new";
      d.innerHTML = '<span class="f-run">' + esc(x.run) + '</span><div class="f-mid"><div class="f-test">' + esc(x.test) +
        '</div><div class="f-line">' + esc(x.line) + '</div></div><span class="f-res ' + esc(x.state) + '">' + esc(x.res) + "</span>";
      return d;
    };
    var push = function () {
      if (!visible || doc.hidden) return;
      var rows = feed.querySelectorAll(".feed-row");
      var before = Array.prototype.map.call(rows, function (r) { return r.getBoundingClientRect().top; });
      var row = make(items[idx % items.length]);
      idx += 1;
      feed.insertBefore(row, feed.firstChild);
      // FLIP: slide the existing rows down to their new places
      Array.prototype.forEach.call(rows, function (r, i) {
        var dy = before[i] - r.getBoundingClientRect().top;
        if (!dy) return;
        r.style.transition = "none";
        r.style.transform = "translateY(" + dy + "px)";
        requestAnimationFrame(function () {
          r.style.transition = "transform .5s cubic-bezier(.22,.7,.2,1)";
          r.style.transform = "";
        });
      });
      var all = feed.querySelectorAll(".feed-row");
      for (var k = 5; k < all.length; k++) all[k].remove();
    };
    new IntersectionObserver(function (en) { visible = en[0].isIntersecting; }).observe(feed);
    timer = setInterval(push, 1800);
  });
})();

// ---- APB v3.1: pilot request. No server: it opens the visitor's own mail app. ----
(function () {
  var form = document.getElementById("pilot-form");
  if (!form) return;
  var status = document.getElementById("pilot-status");
  var to = form.getAttribute("action").replace(/^mailto:/, "");
  var labels = { name: "Name", email: "Work email", organization: "Organization", role: "Role", workflow: "Workflow type",
    current_level: "Current agent authority level", target_stage: "Target deployment stage", data: "Data availability",
    timing: "Preferred contact timing" };
  function text() {
    var lines = ["APB workflow assessment request", ""];
    Object.keys(labels).forEach(function (k) { lines.push(labels[k] + ": " + (form.elements[k].value || "").trim()); });
    lines.push("", "Consent to be contacted: yes");
    return lines.join("\n");
  }
  function valid() {
    var ok = true;
    ["name", "email", "organization"].forEach(function (k) {
      var el = form.elements[k];
      var v = el.value.trim();
      var bad = !v || (k === "email" && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v));
      el.setAttribute("aria-invalid", bad ? "true" : "false");
      if (bad) ok = false;
    });
    if (!form.elements.consent.checked) ok = false;
    if (!ok) status.textContent = "Please add your name, a valid work email and your organization, and tick the consent box.";
    return ok;
  }
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (!valid()) return;
    window.location.href = "mailto:" + to + "?subject=" + encodeURIComponent("APB workflow assessment request") +
      "&body=" + encodeURIComponent(text());
    status.textContent = "Your mail app should open with the request. If it does not, use Copy request text and email " + to + ".";
  });
  document.getElementById("pilot-copy").addEventListener("click", function () {
    if (!valid()) return;
    var t = text();
    var done = function () { status.textContent = "Copied. Paste it into an email to " + to + "."; };
    var fail = function () { status.textContent = "Copy failed. Select and copy the fields manually."; };
    if (navigator.clipboard) navigator.clipboard.writeText(t).then(done, fail); else fail();
  });
})();
