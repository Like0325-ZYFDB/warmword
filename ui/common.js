/* 一瞥 · 页面共用脚本
 *
 * - WW.boot(名字)：页面加载完通知程序，取回积攒的推送并执行
 * - WW.card(...)：单词卡片（浮窗、查词窗共用）
 * - WW.rich(...)：词典原版排版，放进 Shadow DOM，和界面样式互不干扰
 * - WW.say(...)：发音（在线 → 系统语音兜底）；词典录音直接播放
 */
(function () {
  "use strict";
  var WW = window.WW = window.WW || {};
  var parts = location.pathname.split("/");
  WW.base = location.origin + "/" + parts[1];
  WW.prefs = { font_scale: 1, accent: "us", theme: "system", hotkeys: {} };

  WW.api = function () { return window.pywebview && window.pywebview.api; };

  WW.boot = function (name, after) {
    WW.page = name;
    function go() {
      WW.api().ready(name).then(function (r) {
        r = r || {};
        if (r.prefs) WW.setPrefs(r.prefs);
        (r.calls || []).forEach(function (c) { if (WW[c[0]]) WW[c[0]](c[1]); });
        if (after) after();
      });
    }
    if (WW.api()) go(); else window.addEventListener("pywebviewready", go);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !e.defaultPrevented && WW.api()) WW.api().close(name);
    });
  };

  WW.setPrefs = function (p) {
    if (!p) return;
    WW.prefs = p;
    var root = document.documentElement;
    if (p.theme === "light" || p.theme === "dark") root.setAttribute("data-theme", p.theme);
    else root.removeAttribute("data-theme");
    root.style.setProperty("--scale", p.font_scale || 1);
    document.querySelectorAll("[data-hotkey]").forEach(function (el) {
      el.textContent = (p.hotkeys || {})[el.getAttribute("data-hotkey")] || "";
    });
    if (WW.onPrefs) WW.onPrefs(p);
  };

  // ---------- 小工具 ----------
  WW.esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };
  var ICONS = {
    volume: '<path d="M11 5 6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/>',
    star: '<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>',
    pin: '<path d="M9 3.5h6"/><path d="M10 3.5v5.2L7 12.5h10l-3-3.8V3.5"/><path d="M12 12.5V20"/>',
    open: '<path d="M14 4h6v6"/><path d="M20 4l-8.5 8.5"/><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5.5A1.5 1.5 0 0 1 6.5 4H15"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12.5a1 1 0 0 0 1 .9h8a1 1 0 0 0 1-.9L18 7M9 7V4.5h6V7"/>',
    download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    folder: '<path d="M3.5 7.5A2 2 0 0 1 5.5 5.5H9l2 2h7.5a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    book: '<path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v15H6.5A1.5 1.5 0 0 0 5 19.5z"/><path d="M5 19.5A1.5 1.5 0 0 0 6.5 21H19"/>',
    settings: '<path d="M4 6.5h9M17 6.5h3M4 12h3M11 12h9M4 17.5h11M19 17.5h1"/><circle cx="15" cy="6.5" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="17.5" r="2"/>',
    translate: '<path d="M4 5.5h9M8.5 3.5v2M6.5 5.5c.5 3.5 2.8 6 6 7.5M11 5.5c-.8 3.8-3.4 6.4-6.5 7.8"/><path d="m13 20.5 3.8-9 3.8 9M14.3 17.5h5"/>',
    clock: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4.2l2.8 1.8"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    minus: '<path d="M6 12h12"/>', plus: '<path d="M12 6v12M6 12h12"/>'
  };
  WW.icon = function (name, filled) {
    return '<svg class="i" viewBox="0 0 24 24" fill="' + (filled ? "currentColor" : "none") +
      '" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' +
      (ICONS[name] || "") + "</svg>";
  };

  var toastEl, toastT;
  WW.toast = function (msg) {
    if (!toastEl) { toastEl = document.createElement("div"); toastEl.className = "toast"; document.body.appendChild(toastEl); }
    toastEl.textContent = msg;
    toastEl.classList.add("on");
    clearTimeout(toastT);
    toastT = setTimeout(function () { toastEl.classList.remove("on"); }, 2200);
  };

  // ---------- 发音 ----------
  var current = null;
  function blink(el) { if (!el) return; el.classList.remove("playing"); void el.offsetWidth; el.classList.add("playing"); }
  WW.playUrl = function (url, fallback, el) {
    blink(el);
    try { if (current) current.pause(); } catch (e) { /* 忽略 */ }
    var a = new Audio(url);
    current = a;
    var failed = false;
    function fail() { if (failed) return; failed = true; if (fallback) fallback(); }
    a.onerror = fail;
    var p = a.play();
    if (p && p.catch) p.catch(fail);
  };
  WW.say = function (text, accent, el) {
    text = String(text || "").replace(/\s+/g, " ").trim().slice(0, 300);
    if (!text) return;
    accent = accent || WW.prefs.accent || "us";
    WW.playUrl(WW.base + "/v?a=" + accent + "&t=" + encodeURIComponent(text), function () {
      if (WW.api()) WW.api().speak_offline(text, accent).then(function (ok) {
        if (!ok) WW.toast("发音不可用：没联网，系统里也没有英语语音");
      });
    }, el);
  };

  /* 读单词：优先放词典自带的真人录音（牛津等），没有或放不了再用在线发音 / 系统语音 */
  WW.sayWord = function (card, accent, el) {
    accent = accent || WW.prefs.accent || "us";
    var word = (card && card.word) || "";
    var url = card && card.audio && (card.audio[accent] || card.audio.uk || card.audio.us);
    if (url) WW.playUrl(url, function () { WW.say(word, accent, el); }, el);
    else WW.say(word, accent, el);
  };

  // ---------- 单词卡片 ----------
  function phonRow(card) {
    var p = card.phonetic || {}, out = [];
    function one(lab, ipa, accent) {
      return '<span class="ph">' + (lab ? '<span class="lab">' + lab + "</span>" : "") +
        (ipa ? '<span class="ipa">' + WW.esc(ipa) + "</span>" : "") +
        '<button class="say" data-act="say" data-accent="' + accent + '" title="' + (accent === "uk" ? "英式发音" : "美式发音") + '">' +
        WW.icon("volume") + "</button></span>";
    }
    if (p.uk || p.us) {
      out.push(one("英", p.uk || p.us, "uk"));
      out.push(one("美", p.us || p.uk, "us"));
    } else {
      if (p.plain) out.push('<span class="ph"><span class="ipa">' + WW.esc(p.plain) + "</span></span>");
      out.push(one("英", "", "uk"));
      out.push(one("美", "", "us"));
    }
    return '<div class="wc-phon">' + out.join("") + "</div>";
  }

  /* card: 程序给的卡片；opt: {actions, saved, pinned, compact} */
  WW.card = function (card, opt) {
    opt = opt || {};
    var h = [];
    h.push('<div class="wc-head"><div class="wc-word">' + WW.esc(card.word) + "</div>");
    if (opt.actions) {
      h.push('<div class="wc-actions">' + opt.actions + "</div>");
    }
    h.push("</div>");
    if (!card.found) return h.join("");
    h.push(phonRow(card));
    if (card.lemma) {
      h.push('<div class="wc-lemma">' + WW.esc(card.lemma.from) + " 是 " +
        '<a data-act="word" data-word="' + WW.esc(card.lemma.word) + '">' + WW.esc(card.lemma.word) + "</a> 的" +
        WW.esc(card.lemma.relation) + "</div>");
    }
    var tags = (card.tags || []).map(function (t) { return '<span class="tag">' + WW.esc(t) + "</span>"; });
    if (card.collins) tags.push('<span class="tag gold" title="柯林斯星级：越多越常用">柯林斯 ' + "★★★★★".slice(0, card.collins) + "</span>");
    if (card.oxford) tags.push('<span class="tag gold" title="牛津 3000 核心词">牛津 3000</span>');
    if (tags.length) h.push('<div class="wc-tags">' + tags.join("") + "</div>");
    var senses = card.senses || [];
    if (opt.compact) senses = senses.slice(0, 6);
    if (senses.length) {
      h.push('<div class="wc-senses">' + senses.map(function (s) {
        return '<div class="pos">' + WW.esc(s.pos) + '</div><div class="txt">' + WW.esc(s.text) + "</div>";
      }).join("") + "</div>");
    }
    if ((card.forms || []).length) {
      h.push('<div class="wc-forms">' + card.forms.map(function (f) {
        return WW.esc(f.label) + ' <a data-act="word" data-word="' + WW.esc(f.word) + '">' + WW.esc(f.word) + "</a>";
      }).join('<span class="sep">·</span>') + "</div>");
    }
    if (opt.english && (card.en || []).length) {
      h.push('<div class="wc-en">' + card.en.map(function (s) {
        return "<div>" + (s.pos ? '<span class="pos">' + WW.esc(s.pos) + "</span>" : "") + WW.esc(s.text) + "</div>";
      }).join("") + "</div>");
    }
    return h.join("");
  };

  // ---------- 词典原版排版 ----------
  var EX_SEL = ".ln.ex, .x, .example, .examp, .exa, .eg, .sent, .sentence, .ex, .exm, .liju, .EXAMPLE, .Example, .example-sentence, .exam, .lj";
  var SHADOW_CSS =
    ":host{display:block}" +
    ".ww-root{zoom:var(--scale,1);font-size:14px;line-height:1.65;color:var(--text);" +
    "font-family:var(--font);word-break:break-word;overflow-wrap:anywhere;user-select:text}" +
    ".ww-root img{max-width:100%;height:auto;vertical-align:middle}" +
    ".ww-root table{max-width:100%}" +
    ".ww-root [data-sound]{cursor:pointer}" +
    ".ww-root a{color:var(--accent-text)}" +
    ".ww-audio::before{content:'';display:inline-block;width:14px;height:14px;vertical-align:-2px;" +
    "background:currentColor;-webkit-mask:url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M11 5 6 9H3v6h3l5 4z'/%3E%3Cpath d='M15.5 8.5a5 5 0 0 1 0 7'/%3E%3C/svg%3E\") center/contain no-repeat}" +
    ".ww-say{cursor:pointer;margin-left:4px;display:inline-block;width:14px;height:14px;vertical-align:-2px;opacity:.5;" +
    "background:var(--accent);-webkit-mask:url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M11 5 6 9H3v6h3l5 4z'/%3E%3Cpath d='M15.5 8.5a5 5 0 0 1 0 7'/%3E%3C/svg%3E\") center/contain no-repeat}" +
    ".ww-say:hover{opacity:1}.playing{animation:p .5s ease 2}@keyframes p{50%{opacity:.3}}";
  // 清晰排版：统一的层次——中文释义是重点，例句缩进弱化，编号、词性做成小标记
  var CLEAR_CSS =
    ".cl{zoom:var(--scale,1);font-size:14.5px;line-height:1.7;color:var(--text);font-family:var(--font);" +
    "word-break:break-word;overflow-wrap:anywhere;user-select:text}" +
    ".cl .ln{margin:3px 0}" +
    ".cl .ln.meta{color:var(--text-3);font-size:13px;margin-bottom:8px}" +
    ".cl .ln.def{margin-top:12px}.cl .ln.def:first-child,.cl .ln.meta+.ln.def{margin-top:0}" +
    ".cl .cn{color:var(--accent-text);font-weight:600}" +
    ".cl .ln.ex{margin:3px 0 3px 2px;padding-left:12px;border-left:2px solid var(--border);color:var(--text-2);font-size:13.5px}" +
    ".cl .ln.ex .cn{color:var(--text-3);font-weight:400}" +
    ".cl .num{display:inline-grid;place-items:center;min-width:19px;height:19px;padding:0 5px;margin-right:6px;" +
    "border-radius:10px;background:var(--accent-soft);color:var(--accent-text);font-size:11.5px;font-weight:600;" +
    "line-height:1;vertical-align:1px;font-style:normal}" +
    ".cl .pos{font-style:italic;color:var(--text-3);margin-right:6px}" +
    ".cl .phon{font-family:var(--font-phon);color:var(--text-2)}" +
    ".cl .ln.sub{margin-top:16px;padding-top:10px;border-top:1px dashed var(--border);font-weight:600;color:var(--text)}" +
    ".cl b{font-weight:600}";
  var CJK = /[　-〿㐀-鿿＀-￯]/;

  function englishPart(el) {
    var t = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
    var m = t.split(CJK)[0];
    return (m || t).trim();
  }

  /* 按设置显示一本词典的释义：清晰排版（默认）或词典原版排版 */
  WW.entry = function (host, entry, on) {
    var original = WW.prefs.entry_style === "original" || !entry.clear;
    return WW.rich(host, entry, on, !original);
  };

  /* host：放内容的元素；entry：{id, html, css, clear}；on：{word(w)}；clear：用清晰排版 */
  WW.rich = function (host, entry, on, clear) {
    on = on || {};
    var root = host.shadowRoot || host.attachShadow({ mode: "open" });
    if (clear) {
      root.innerHTML = "<style>" + SHADOW_CSS + CLEAR_CSS + '</style><div class="ww-root cl">' + entry.clear + "</div>";
    } else {
      var links = (entry.css || []).map(function (u) { return '<link rel="stylesheet" href="' + WW.esc(u) + '">'; }).join("");
      root.innerHTML = "<style>" + SHADOW_CSS + "</style>" + links + '<div class="ww-root">' + entry.html + "</div>";
    }
    var box = root.querySelector(".ww-root");
    // 没有录音的例句：后面加朗读按钮
    box.querySelectorAll(EX_SEL).forEach(function (el) {
      if (el.querySelector("[data-sound], .ww-say") || el.closest("[data-sound]")) return;
      var t = englishPart(el);
      if ((t.match(/[A-Za-z]{2,}/g) || []).length < 3) return;
      var b = document.createElement("span");
      b.className = "ww-say"; b.title = "朗读例句"; b.setAttribute("data-say", t);
      el.appendChild(b);
    });
    box.addEventListener("click", function (e) {
      var t = e.target.closest("[data-sound],[data-entry],[data-href],[data-say],a");
      if (!t) return;
      if (t.hasAttribute("data-say")) { e.preventDefault(); WW.say(t.getAttribute("data-say"), null, t); return; }
      if (t.hasAttribute("data-sound")) {
        e.preventDefault();
        var ref = t.getAttribute("data-sound").replace(/\\/g, "/").replace(/^\/+/, "");
        var near = t.closest(EX_SEL);
        var accent = /(^|[\/_\-.])(us|nam|ame|american)([\/_\-.]|$)/i.test(ref) ? "us" :
          /(^|[\/_\-.])(uk|gb|bre|british)([\/_\-.]|$)/i.test(ref) ? "uk" : null;
        WW.playUrl(WW.base + "/r/" + entry.id + "/" + ref.split("/").map(encodeURIComponent).join("/"), function () {
          WW.say(near ? englishPart(near) : (on.headword || ""), accent);
        }, t);
        return;
      }
      if (t.hasAttribute("data-entry")) { e.preventDefault(); if (on.word) on.word(t.getAttribute("data-entry")); return; }
      if (t.getAttribute("href") && t.getAttribute("href").charAt(0) === "#" && t.getAttribute("href").length > 1) {
        e.preventDefault();
        var target = root.getElementById(t.getAttribute("href").slice(1)) ||
          root.querySelector('[name="' + t.getAttribute("href").slice(1) + '"]');
        if (target) target.scrollIntoView({ block: "start", behavior: "smooth" });
        return;
      }
      e.preventDefault();
    });
    box.addEventListener("dblclick", function (e) {
      var sel = root.getSelection ? root.getSelection() : window.getSelection();
      var w = String(sel || "").trim();
      if (/^[A-Za-z][A-Za-z'\-]{0,39}$/.test(w) && on.word) on.word(w);
    });
    return root;
  };

  // ---------- 选中文字的小工具条（查词窗、翻译窗用） ----------
  WW.selectionTool = function (onWord) {
    var bar = document.createElement("div");
    bar.className = "seltool";
    bar.innerHTML = '<button data-x="say">朗读</button><button data-x="look">查词</button><button data-x="tr">翻译</button><button data-x="copy">复制</button>';
    document.body.appendChild(bar);
    var text = "";
    function deepSelection() {
      var s = window.getSelection(), t = String(s || "").trim();
      if (t) return { t: t, r: s.rangeCount ? s.getRangeAt(0).getBoundingClientRect() : null };
      var hosts = document.querySelectorAll(".rich-host");
      for (var i = 0; i < hosts.length; i++) {
        var sr = hosts[i].shadowRoot;
        if (sr && sr.getSelection) {
          var ss = sr.getSelection(), tt = String(ss || "").trim();
          if (tt) return { t: tt, r: ss.rangeCount ? ss.getRangeAt(0).getBoundingClientRect() : null };
        }
      }
      return null;
    }
    document.addEventListener("mouseup", function (e) {
      if (bar.contains(e.target)) return;
      setTimeout(function () {
        var s = deepSelection();
        if (!s || !s.r || s.t.length > 3000) { bar.classList.remove("on"); return; }
        text = s.t;
        bar.classList.add("on");
        var x = Math.min(window.innerWidth - bar.offsetWidth - 8, Math.max(8, s.r.left));
        var y = s.r.top - bar.offsetHeight - 6;
        if (y < 6) y = s.r.bottom + 6;
        bar.style.left = x + "px"; bar.style.top = y + "px";
      }, 10);
    });
    document.addEventListener("mousedown", function (e) { if (!bar.contains(e.target)) bar.classList.remove("on"); });
    bar.addEventListener("click", function (e) {
      var b = e.target.closest("button"); if (!b) return;
      var x = b.getAttribute("data-x");
      if (x === "say") WW.say(text);
      else if (x === "look") onWord(text);
      else if (x === "tr") WW.api().translate_text(text);
      else if (x === "copy") { WW.api().copy(text); WW.toast("已复制"); }
      bar.classList.remove("on");
    });
  };
})();
