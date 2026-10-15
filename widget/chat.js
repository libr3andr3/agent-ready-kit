/* agent-ready-kit · site assistant widget (no dependencies). Talks to POST /api/chat.
   Add it to any page:
     <link rel="stylesheet" href="/agent-kit/chat.css">
     <script src="/agent-kit/chat.js" defer></script>
   If the server lives on another origin, point the script at it (and list your site in CHAT_ALLOWED_ORIGINS):
     <script src="https://agent.example.com/agent-kit/chat.js" data-endpoint="https://agent.example.com" defer></script>
   Texts come from GET /api/widget-config (the "widget" block of site.json). Language: <html data-lang> or <html lang>.
   Any element with data-chat opens the chat; data-chat-<lang>="..." (or data-chat="...") sends that message. */
(function () {
  "use strict";
  var script = document.currentScript;
  var BASE = ((script && script.getAttribute("data-endpoint")) || "").replace(/\/$/, "");
  var DEFAULTS = {
    en: { bubbleTitle: "Hi!", bubble: "Tell me if you need help with anything.", status: "AI assistant",
          greeting: "Hi! How can I help?", placeholder: "Type your message…", legal: "AI assistant. It can make mistakes.",
          privacy: "Privacy", privacy_url: "", readiness: "Agent-readiness check", outOf: "out of 100",
          error: "Sorry, I couldn't answer just now. Please try again.", rate: "Too many messages in a row. Please try again in a few minutes.",
          close: "Close", open: "Open chat", send: "Send", chips: [] },
    es: { bubbleTitle: "¡Hola!", bubble: "Cuéntame si necesitas ayuda con algo.", status: "Asistente con IA",
          greeting: "¡Hola! ¿En qué te ayudo?", placeholder: "Escribe tu mensaje…", legal: "Asistente con IA. Puede equivocarse.",
          privacy: "Privacidad", privacy_url: "", readiness: "Revisión agent-ready", outOf: "de 100",
          error: "Perdón, no pude responder ahora. Inténtalo de nuevo.", rate: "Muchos mensajes seguidos. Inténtalo en unos minutos.",
          close: "Cerrar", open: "Abrir chat", send: "Enviar", chips: [] }
  };
  var CONF = { languages: ["en"], icon: BASE + "/agent-kit/icon.svg", texts: {} };
  var history = [], busy = false, opened = false;

  function lang() {
    var h = document.documentElement, v = (h.getAttribute("data-lang") || h.getAttribute("lang") || "").slice(0, 2).toLowerCase();
    return CONF.languages.indexOf(v) >= 0 ? v : CONF.languages[0];
  }
  function t(k) {
    var l = lang(), own = (CONF.texts[l] || {})[k];
    if (own !== undefined && own !== "") return own;
    return (DEFAULTS[l] || DEFAULTS.en)[k];
  }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function store(k, v) { try { if (v === undefined) return sessionStorage.getItem(k); sessionStorage.setItem(k, v); } catch (e) { return null; } }
  function abs(u) { return /^https?:\/\//.test(u) ? u : BASE + u; }

  var root = el("div", "yc-root");
  var launch = el("div", "yc-launch");
  var bubble = el("div", "yc-bubble"); bubble.setAttribute("role", "status");
  var bTitle = el("b"), bText = el("span"), bX = el("button", "yc-bubble-x", "×");
  bX.type = "button"; bubble.append(bTitle, bText, bX);
  var fab = el("button", "yc-fab"); fab.type = "button";
  var fabImg = el("img"); fabImg.alt = ""; fab.appendChild(fabImg);
  launch.append(bubble, fab);

  var panel = el("div", "yc-panel"); panel.setAttribute("role", "dialog"); panel.setAttribute("aria-modal", "false");
  var head = el("header", "yc-head");
  var av = el("div", "yc-avatar"); var avImg = el("img"); avImg.alt = ""; av.appendChild(avImg);
  var title = el("div", "yc-title"), tName = el("b"), tStatus = el("span"); title.append(tName, tStatus);
  var close = el("button", "yc-close", "×"); close.type = "button";
  head.append(av, title, close);
  var body = el("div", "yc-body"); body.setAttribute("aria-live", "polite");
  var foot = el("div", "yc-foot");
  var form = el("form", "yc-form");
  var input = el("textarea", "yc-input"); input.rows = 1;
  var send = el("button", "yc-send"); send.type = "submit";
  send.innerHTML = '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 13V3M3.5 7.5L8 3l4.5 4.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  form.append(input, send);
  var legal = el("p", "yc-legal"); var legalText = el("span"); var legalSep = document.createTextNode(" · "); var legalLink = el("a");
  legal.append(legalText, legalSep, legalLink);
  foot.append(form, legal);
  panel.append(head, body, foot);
  root.append(launch, panel);

  function paintStatic() {
    var icon = abs(CONF.icon || "/agent-kit/icon.svg");
    fabImg.src = icon; avImg.src = icon;
    bTitle.textContent = t("bubbleTitle"); bText.textContent = t("bubble");
    tName.textContent = t("assistant") || CONF.name || ""; tStatus.textContent = t("status");
    input.placeholder = t("placeholder"); legalText.textContent = t("legal");
    var pu = t("privacy_url");
    legalLink.textContent = pu ? t("privacy") : ""; legalSep.textContent = pu ? " · " : "";
    if (pu) legalLink.href = pu; else legalLink.removeAttribute("href");
    fab.setAttribute("aria-label", t("open")); close.setAttribute("aria-label", t("close")); send.setAttribute("aria-label", t("send"));
    bX.setAttribute("aria-label", t("close"));
  }

  /* safe text rendering: **bold**, links, "- " lists (no innerHTML for model output) */
  function inline(node, text) {
    var re = /(\*\*[^*]+\*\*|https?:\/\/[^\s)]+[^\s).,;:!?])/g, last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) node.appendChild(document.createTextNode(text.slice(last, m.index)));
      if (m[0].slice(0, 2) === "**") node.appendChild(el("b", null, m[0].slice(2, -2)));
      else { var a = el("a", null, m[0].replace(/^https?:\/\//, "")); a.href = m[0]; a.target = "_blank"; a.rel = "noopener nofollow"; node.appendChild(a); }
      last = m.index + m[0].length;
    }
    if (last < text.length) node.appendChild(document.createTextNode(text.slice(last)));
  }
  function rich(text) {
    var box = el("div", "yc-msg"), lines = String(text).split("\n"), ul = null, para = [];
    function flush() { if (para.length) { var p = el("div"); inline(p, para.join("\n")); box.appendChild(p); para = []; } }
    lines.forEach(function (ln) {
      var mm = ln.match(/^\s*(?:[-•*]|\d+[.)])\s+(.*)$/);
      if (mm) { flush(); if (!ul) { ul = el("ul"); box.appendChild(ul); } var li = el("li"); inline(li, mm[1]); ul.appendChild(li); }
      else { ul = null; para.push(ln); }
    });
    flush();
    return box;
  }
  function row(who, node) {
    var r = el("div", "yc-row " + who);
    if (who === "bot") { var mini = el("div", "yc-mini"); var im = el("img"); im.src = avImg.src; im.alt = ""; mini.appendChild(im); r.appendChild(mini); }
    r.appendChild(node); body.appendChild(r); body.scrollTop = body.scrollHeight; return r;
  }
  function say(who, text) { return row(who, who === "me" ? Object.assign(el("div", "yc-msg"), { textContent: text }) : rich(text)); }

  function readinessCard(r) {
    var c = el("div", "yc-card"), h = el("div", "yc-card-h");
    h.append(el("b", null, t("readiness")), el("span", null, ""));
    c.appendChild(h);
    var sc = el("div", "yc-score"), ring = el("div", "yc-ring");
    ring.style.setProperty("--p", r.score);
    ring.style.setProperty("--yc-ring", r.score >= 70 ? "#248a3d" : r.score >= 40 ? "#c93400" : "#d70015");
    ring.appendChild(el("span", null, String(r.score)));
    var st = el("div", "yc-score-t"); st.append(el("b", null, (r.site || "").replace(/^https?:\/\//, "")), document.createTextNode(r.score + " " + t("outOf")));
    sc.append(ring, st); c.appendChild(sc);
    (r.checks || []).forEach(function (k) {
      var d = el("div", "yc-check"), i = el("i", k.ok ? "ok" : "no", k.ok ? "✓" : "✕"), txt = el("div");
      txt.append(document.createTextNode(k.label), el("small", null, k.detail || ""));
      d.append(i, txt); c.appendChild(d);
    });
    if (r.note) c.appendChild(el("div", "yc-notes", r.note));
    return c;
  }
  function card(node) { body.appendChild(node); body.scrollTop = body.scrollHeight; }

  function chips() {
    var list = t("chips") || [];
    if (!list.length) return;
    var box = el("div", "yc-chips");
    list.forEach(function (c) {
      var b = el("button", "yc-chip", c[0]); b.type = "button";
      b.addEventListener("click", function () { box.remove(); ask(c[1]); });
      box.appendChild(b);
    });
    body.appendChild(box);
  }

  function ask(text) {
    text = String(text || "").trim();
    if (!text || busy) return;
    var c = body.querySelector(".yc-chips"); if (c) c.remove();
    say("me", text);
    history.push({ role: "user", content: text });
    busy = true; send.disabled = true;
    var typing = row("bot", (function () { var d = el("div", "yc-msg yc-typing"); d.append(el("i"), el("i"), el("i")); return d; })());
    var ctl = window.AbortController ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctl) ctl.abort(); }, 90000);
    fetch(BASE + "/api/chat", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history.slice(-24), lang: lang() }), signal: ctl && ctl.signal })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, status: r.status, j: j }; }); })
      .then(function (res) {
        typing.remove();
        var j = res.j || {}, mark = body.lastElementChild;
        (j.cards || []).forEach(function (cd) { if (cd.type === "readiness") card(readinessCard(cd.data)); });
        var reply = j.reply || (res.status === 429 ? t("rate") : !res.ok ? t("error") : "");
        if (reply) { say("bot", reply); if (res.ok) history.push({ role: "assistant", content: reply }); }
        var first = mark ? mark.nextElementSibling : body.firstElementChild;
        if (first) body.scrollTop = Math.max(0, first.offsetTop - body.offsetTop - 12);
      })
      .catch(function () { typing.remove(); say("bot", t("error")); })
      .then(function () { clearTimeout(timer); busy = false; send.disabled = !input.value.trim(); input.focus(); });
  }

  function open(prompt) {
    panel.classList.add("on"); bubble.classList.remove("on"); launch.style.display = "none";
    store("yc-seen", "1");
    if (!opened) { opened = true; say("bot", t("greeting")); chips(); }
    setTimeout(function () { input.focus(); }, 250);
    if (prompt) ask(prompt);
  }
  function shut() { panel.classList.remove("on"); launch.style.display = ""; fab.focus(); }

  fab.addEventListener("click", function () { open(); });
  bubble.addEventListener("click", function (e) { if (e.target !== bX) open(); });
  bX.addEventListener("click", function (e) { e.stopPropagation(); bubble.classList.remove("on"); store("yc-seen", "1"); });
  close.addEventListener("click", shut);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && panel.classList.contains("on")) shut(); });
  form.addEventListener("submit", function (e) { e.preventDefault(); var v = input.value; input.value = ""; input.style.height = ""; ask(v); });
  input.addEventListener("input", function () { send.disabled = busy || !input.value.trim(); input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 120) + "px"; });
  input.addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event("submit")); } });
  document.addEventListener("click", function (e) {
    var trg = e.target.closest && e.target.closest("[data-chat]");
    if (!trg) return;
    e.preventDefault();
    var p = trg.getAttribute("data-chat-" + lang()) || trg.getAttribute("data-chat");
    open(p && p !== "open" ? p : null);
  });
  new MutationObserver(paintStatic).observe(document.documentElement, { attributes: true, attributeFilter: ["data-lang", "lang"] });

  function boot() {
    paintStatic(); send.disabled = true;
    document.body.appendChild(root);
    if (location.hash === "#chat") { open(); return; }
    if (!store("yc-seen")) setTimeout(function () { if (!panel.classList.contains("on")) bubble.classList.add("on"); }, 1400);
  }
  function start() {
    fetch(BASE + "/api/widget-config").then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
      .then(function (c) { if (c) { CONF = c; } boot(); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
  window.AgentKitChat = { open: open };
})();
