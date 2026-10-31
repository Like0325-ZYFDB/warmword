/* 暖词 · 释义渲染（浮窗 / 查词窗 / 翻译窗共用，程序加载页面时内嵌进来）
 *
 * - 词典原版 HTML 放进 Shadow DOM，套上词典自带样式，和界面样式互不干扰
 * - 点 sound:// 发音 → 程序播放词典 mdd 里的音频；放不了就朗读附近的英文
 * - 没有录音的例句后面加 🔊，点了在线朗读（离线时用系统语音）
 * - 双击释义里的单词直接查；选中一段文字弹出「朗读 / 查词」小工具条
 */
(function () {
  var WW = window.WW = {};
  var cssCache = {};
  var EX_SEL = ".x, .example, .examp, .exa, .eg, .sent, .sentence, .ex, .exm, .liju, " +
               ".EXAMPLE, .Example, .example-sentence, .exam, .lj";
  var CJK = /[　-〿㐀-鿿＀-￯]/;
  var SHADOW_CSS =
    ":host{display:block}" +
    ".ww-root{zoom:var(--ww-scale,1);font-size:14.5px;line-height:1.65;color:#2f2a24;" +
    "font-family:'Segoe UI','Microsoft YaHei',sans-serif;word-break:break-word;overflow-wrap:anywhere}" +
    ".ww-root img{max-width:100%;height:auto;vertical-align:middle}" +
    ".ww-root table{max-width:100%}" +
    ".ww-root [data-sound]{cursor:pointer}" +
    ".ww-icon::before,.ww-audio::before{content:'\\1F50A';font-size:.9em;text-decoration:none}" +
    ".ww-say{cursor:pointer;margin-left:5px;font-size:.82em;opacity:.55;user-select:none;" +
    "text-decoration:none;font-style:normal}" +
    ".ww-say:hover{opacity:1}" +
    ".ww-playing{animation:ww-pulse .5s ease 3}" +
    "@keyframes ww-pulse{50%{opacity:.25}}";

  function api() { return window.pywebview && window.pywebview.api; }

  // ---------- 偏好（字号） ----------
  WW.prefs = { font_scale: 1, render_mode: "rich", accent: "us" };
  WW.setPrefs = function (p) {
    if (!p) return;
    WW.prefs = p;
    document.documentElement.style.setProperty("--ww-scale", p.font_scale || 1);
  };
  window.addEventListener("pywebviewready", function () {
    api().get_prefs().then(WW.setPrefs);
  });

  // ---------- 小提示 ----------
  var toastEl = null, toastT = null;
  WW.toast = function (msg) {
    if (!toastEl) {
      toastEl = document.createElement("div");
      toastEl.className = "ww-toast";
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = msg;
    toastEl.classList.add("on");
    clearTimeout(toastT);
    toastT = setTimeout(function () { toastEl.classList.remove("on"); }, 2600);
  };

  // ---------- 朗读 / 播放 ----------
  function blink(el) {
    if (!el) return;
    el.classList.remove("ww-playing");
    void el.offsetWidth;
    el.classList.add("ww-playing");
  }
  WW.say = function (text, el) {
    text = (text || "").replace(/\s+/g, " ").trim();
    if (!text) return;
    blink(el);
    api().speak_text(text, "").then(function (ok) {
      if (!ok) WW.toast("朗读失败：联网发音和系统语音都用不了");
    });
  };
  function englishPart(t) {
    var cut = t.search(CJK);
    if (cut >= 0) t = t.slice(0, cut);
    return t.replace(/\s+/g, " ").replace(/^[\s\d.)(•·-]+/, "").trim();
  }
  function isSentence(t) { return (t.match(/[A-Za-z]{2,}/g) || []).length >= 3; }
  function textOf(el) {
    var c = el.cloneNode(true);
    c.querySelectorAll(".ww-say,[data-sound],script,style").forEach(function (x) { x.remove(); });
    return englishPart(c.textContent || "");
  }
  function nearbySentence(el) {
    var node = el.parentElement;
    for (var i = 0; node && i < 3; i++, node = node.parentElement) {
      var ex = node.matches && node.matches(EX_SEL) ? node : node.querySelector && node.querySelector(EX_SEL);
      if (ex) { var t = textOf(ex); if (isSentence(t)) return t; }
    }
    return "";
  }
  function playSound(entry, ref, el) {
    blink(el);
    api().play_sound(entry.book, ref).then(function (r) {
      if (r && r.ok) return;
      if (r && r.data) {
        try {
          var a = new Audio("data:" + r.mime + ";base64," + r.data);
          a.play().catch(function () { fallback(); });
          return;
        } catch (e) { /* 落到下面朗读 */ }
      }
      fallback();
      function fallback() {
        // 词典音频放不了：读附近的例句，没有例句就读词头
        var t = nearbySentence(el) || entry.head || "";
        if (t) WW.say(t, el);
        else WW.toast((r && r.why) || "这条发音放不了");
      }
    });
  }

  // ---------- 例句加朗读按钮 ----------
  function markExamples(scope) {
    scope.querySelectorAll(EX_SEL).forEach(function (el) {
      if (el.parentElement && el.parentElement.closest("[data-ww-ex]")) return;
      var t = textOf(el);
      if (!isSentence(t)) return;
      var holder = el.parentElement;
      if (el.querySelector("[data-sound]")) return;
      // 牛津这类词典把例句录音放在例句旁边：同一个容器里只有这一句且有录音，就不重复加
      if (holder && holder !== scope && holder.querySelector("[data-sound]") &&
          holder.querySelectorAll(EX_SEL).length === 1) return;
      el.setAttribute("data-ww-ex", "1");
      var b = document.createElement("span");
      b.className = "ww-say";
      b.title = "朗读例句";
      b.setAttribute("data-say", t);
      b.textContent = "🔊";
      el.appendChild(b);
    });
  }
  // 发音链接是空的、词典样式里也没给图标：补一个喇叭，不然看不到也点不着
  function iconizeSounds(scope) {
    scope.querySelectorAll("[data-sound]").forEach(function (el) {
      if (el.classList.contains("ww-audio")) return;
      var empty = !(el.textContent || "").trim() && !el.querySelector("img");
      // 词典样式已经给了大小（画成按钮/图标）就不再补
      var styled = el.offsetWidth > 3 && el.offsetHeight > 3;
      el.classList.toggle("ww-icon", empty && !styled);
    });
  }

  // ---------- 交互 ----------
  function selectionText(root) {
    var sel = (root && root.getSelection) ? root.getSelection() : window.getSelection();
    return sel ? String(sel).trim() : "";
  }
  var bar = null;
  function hideBar() { if (bar) bar.style.display = "none"; }
  function showBar(x, y, text, opts) {
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "ww-selbar";
      document.body.appendChild(bar);
      document.addEventListener("mousedown", function (e) {
        if (!bar.contains(e.target)) hideBar();
      }, true);
    }
    var single = /^[A-Za-z][A-Za-z'’-]*$/.test(text);
    bar.innerHTML = "";
    function add(label, fn) {
      var b = document.createElement("button");
      b.textContent = label;
      b.addEventListener("mousedown", function (e) { e.preventDefault(); });
      b.addEventListener("click", function () { hideBar(); fn(); });
      bar.appendChild(b);
    }
    add("🔊 朗读", function () { WW.say(englishPart(text) || text); });
    if (text.length <= 40) add("查词", function () { opts.onEntry(text); });
    if (single) add("收藏", function () {
      api().add_vocab(text).then(function (ok) { WW.toast(ok ? "已收藏 " + text : text + " 已在生词本里"); });
    });
    bar.style.display = "flex";
    var w = bar.offsetWidth, h = bar.offsetHeight;
    bar.style.left = Math.max(4, Math.min(x - w / 2, window.innerWidth - w - 4)) + "px";
    bar.style.top = (y - h - 10 < 4 ? y + 14 : y - h - 10) + "px";
  }

  function wire(scope, root, entry, opts) {
    scope.addEventListener("click", function (ev) {
      var t = ev.target.closest && ev.target.closest("[data-sound],[data-say],[data-entry],[data-href],a[href]");
      if (!t) return;
      ev.preventDefault();
      if (t.getAttribute("data-sound")) playSound(entry, t.getAttribute("data-sound"), t);
      else if (t.getAttribute("data-say")) WW.say(t.getAttribute("data-say"), t);
      else if (t.getAttribute("data-entry")) opts.onEntry(t.getAttribute("data-entry"));
      else if ((t.getAttribute("href") || "").charAt(0) === "#" && t.getAttribute("href").length > 1) {
        var id = t.getAttribute("href").slice(1);
        var target = (root.getElementById ? root.getElementById(id) : document.getElementById(id)) ||
                     root.querySelector('[name="' + id.replace(/"/g, "") + '"]');
        if (target) target.scrollIntoView({ block: "start", behavior: "smooth" });
      }
    });
    scope.addEventListener("dblclick", function (ev) {
      var w = selectionText(root);
      if (/^[A-Za-z][A-Za-z'’-]{1,40}$/.test(w)) { hideBar(); opts.onEntry(w); }
    });
    scope.addEventListener("mouseup", function (ev) {
      setTimeout(function () {
        var t = selectionText(root);
        if (t && t.length <= 400 && /[A-Za-z]/.test(t) && !/^[A-Za-z][A-Za-z'’-]*$/.test(t)) {
          showBar(ev.clientX, ev.clientY, t, opts);
        } else if (t && /^[A-Za-z][A-Za-z'’-]*$/.test(t) && ev.detail === 1) {
          showBar(ev.clientX, ev.clientY, t, opts);  // 拖选一个词：也给工具条（双击直接查）
        }
      }, 10);
    });
  }

  function loadCss(entry, shadow, done) {
    if (!entry.css_id) { done(); return; }
    var key = entry.book + "|" + entry.css_id;
    if (!cssCache[key]) cssCache[key] = api().book_css(entry.book);
    cssCache[key].then(function (css) {
      shadow.querySelector(".ww-book-css").textContent = css || "";
      done();
    }, done);
  }

  /* entries: [{dict, book, head, mode: rich|ecdict|simple, html, css_id}]
   * opts.onEntry(word)：点词条链接 / 双击单词时查这个词
   * opts.tabs：每本词典一个标签，只显示当前那本（浮窗用）
   * opts.nav：顶部列出词典名，点了跳过去（查词窗用）
   * opts.onRendered()：内容（含样式）就绪后回调，用来调整窗口高度 */
  WW.render = function (container, entries, opts) {
    opts = opts || {};
    opts.onEntry = opts.onEntry || function (w) { api().open_lookup(w); };
    hideBar();
    container.innerHTML = "";
    var pending = entries.length, boxes = [];
    function finished() { if (--pending <= 0 && opts.onRendered) opts.onRendered(); }
    if (!entries.length && opts.onRendered) opts.onRendered();

    if (entries.length > 1 && (opts.tabs || opts.nav)) {
      var tabs = document.createElement("div");
      tabs.className = "ww-tabs";
      entries.forEach(function (e, i) {
        var b = document.createElement("button");
        b.textContent = e.dict;
        b.className = i === 0 ? "on" : "";
        b.addEventListener("click", function () {
          if (opts.tabs) {
            boxes.forEach(function (x, j) { x.style.display = j === i ? "" : "none"; });
            Array.prototype.forEach.call(tabs.children, function (x, j) { x.className = j === i ? "on" : ""; });
            if (opts.onRendered) opts.onRendered();
          } else {
            boxes[i].scrollIntoView({ block: "start", behavior: "smooth" });
          }
        });
        tabs.appendChild(b);
      });
      container.appendChild(tabs);
    }

    entries.forEach(function (e, i) {
      var box = document.createElement("div");
      box.className = "entry";
      if (opts.tabs && i > 0) box.style.display = "none";
      if (!(opts.tabs && entries.length > 1)) {
        var name = document.createElement("div");
        name.className = "dict-name";
        name.textContent = e.dict;
        box.appendChild(name);
      }
      var body = document.createElement("div");
      body.className = "def-body" + (e.mode === "rich" ? " rich" : "");
      box.appendChild(body);
      container.appendChild(box);
      boxes.push(box);
      if (e.mode === "rich" && body.attachShadow) {
        var shadow = body.attachShadow({ mode: "open" });
        shadow.innerHTML = "<style>" + SHADOW_CSS + "</style><style class=\"ww-book-css\"></style>" +
                           "<div class=\"ww-root\">" + e.html + "</div>";
        var root = shadow.querySelector(".ww-root");
        wire(shadow, shadow, e, opts);
        loadCss(e, shadow, function () {
          // 不用 requestAnimationFrame：窗口在屏幕外待命时它不触发
          setTimeout(function () {
            iconizeSounds(root);
            markExamples(root);
            finished();
          }, 0);
        });
      } else {
        body.innerHTML = e.html;
        wire(body, document, e, opts);
        markExamples(body);
        finished();
      }
    });
  };
})();
