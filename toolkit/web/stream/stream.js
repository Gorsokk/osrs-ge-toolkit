/* OSRS Toolkit - shared code for the stream pages.
 *  - polls /api/stream/state (bond, top flip, alerts, Claude + bot messages)
 *  - talks to Meld Studio's local API (ws://127.0.0.1:13376) to switch scenes when asked
 *    and to report the scene list back to the toolkit (for the Claude connector).
 * Every page calls Stream.start({ onState }) and may contain the optional slots
 * #claude-slot (Claude bubble), #toast-slot (short answers / alerts), [data-bond] (bond card).
 */
(function () {
  const params = new URLSearchParams(location.search);
  const T = {
    en: { bond: "Bond goal", has: "{cash} of {price} gp", claude: "Claude · co-host", asks: "{user} asks",
          ask_hint: "Type <code class=cmd>!ask</code> to ask Claude", ge_hint: "<code class=cmd>!ge item</code> for live GE prices",
          flip: "Top flip: <b>{name}</b> buy {buy} → sell {sell} · <span class=up>+{margin} gp</span>",
          queue: "{n} question(s) for Claude", none: "No questions yet: type !ask in chat",
          starting: "Starting soon", brb: "Be right back", ending: "Thanks for watching",
          tools: "Free OSRS Toolkit", support: "Support the tools", live_in: "Live in", now: "Any second now…" },
    fr: { bond: "Objectif bond", has: "{cash} sur {price} gp", claude: "Claude · co-animateur", asks: "{user} demande",
          ask_hint: "Écris <code class=cmd>!ask</code> pour poser une question à Claude", ge_hint: "<code class=cmd>!ge objet</code> pour les prix GE en direct",
          flip: "Meilleur flip : <b>{name}</b> achat {buy} → vente {sell} · <span class=up>+{margin} gp</span>",
          queue: "{n} question(s) pour Claude", none: "Pas encore de questions : écris !ask dans le chat",
          starting: "Ça commence bientôt", brb: "Je reviens tout de suite", ending: "Merci d'avoir regardé",
          tools: "OSRS Toolkit gratuit", support: "Soutenir les outils", live_in: "En direct dans", now: "D'une seconde à l'autre…" },
  };
  let lang = params.get("lang") || "en";
  const t = (k, v = {}) => (T[lang] || T.en)[k].replace(/\{(\w+)\}/g, (_, x) => v[x] ?? "");
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const gp = n => {
    if (n == null) return "?";
    const a = Math.abs(n);
    if (a >= 1e6) return (n / 1e6).toFixed(2) + "M";
    if (a >= 1e4) return (n / 1e3).toFixed(1) + "k";
    return Number(n).toLocaleString("en");
  };
  const CLAUDE_ICON = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 1.5l1.6 7.1 6.2-3.8-3.8 6.2 7.1 1.6-7.1 1.6 3.8 6.2-6.2-3.8L12 22.5l-1.6-7.1-6.2 3.8 3.8-6.2L.9 12l7.1-1.6-3.8-6.2 6.2 3.8z"/></svg>';

  const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Toolkit": "1" },
                                            body: JSON.stringify(body) }).catch(() => {});

  // ---------------------------------------------------------------- Meld ---
  const meld = { api: null, connected: false, lastReport: 0 };
  function meldScenes() {
    const items = (meld.api && meld.api.session && meld.api.session.items) || {};
    return Object.entries(items).filter(([, v]) => v.type === "scene")
      .map(([id, v]) => ({ id, name: v.name, current: !!v.current, index: v.index ?? 0 }))
      .sort((a, b) => a.index - b.index);
  }
  function meldReport(force) {
    if (!force && Date.now() - meld.lastReport < 4000) return;
    meld.lastReport = Date.now();
    const scenes = meldScenes();
    post("/api/stream/meld", { connected: meld.connected, scenes: scenes.map(s => s.name),
                               current: (scenes.find(s => s.current && !/\[vertical\]/i.test(s.name)) || scenes.find(s => s.current) || {}).name || null,
                               streaming: !!(meld.api && meld.api.isStreaming) });
  }
  function meldConnect() {
    if (params.get("meld") === "0") return;
    const go = () => {
      let ws;
      try { ws = new WebSocket("ws://127.0.0.1:13376"); } catch (e) { return setTimeout(go, 5000); }
      ws.onopen = () => {
        new QWebChannel(ws, ch => {
          meld.api = ch.objects.meld; meld.connected = true;
          if (meld.api.sessionChanged) meld.api.sessionChanged.connect(() => meldReport(true));
          meldReport(true);
        });
      };
      ws.onclose = () => { meld.connected = false; meld.api = null; setTimeout(go, 5000); };
      ws.onerror = () => {};
    };
    const s = document.createElement("script");   // Qt's WebChannel client, served by Meld
    s.src = "https://packages.streamwithmeld.com/qt6.8/qwebchannel.min.js";
    s.onload = go;
    s.onerror = () => console.warn("Meld WebChannel client not reachable: scene switching disabled");
    document.head.appendChild(s);
  }
  let lastSceneReq = null;
  const loadedAt = Date.now() / 1000;
  function handleSceneRequest(req) {
    if (!req || req.id === lastSceneReq || !meld.api) return;
    if (req.ts < loadedAt - 2 || Date.now() / 1000 - req.ts > 60) { lastSceneReq = req.id; return; }   // old request: don't replay
    lastSceneReq = req.id;
    const want = String(req.scene).trim().toLowerCase();
    const all = meldScenes(), main = all.filter(s => !/\[vertical\]/i.test(s.name));
    const scene = main.find(s => s.name.toLowerCase() === want) || all.find(s => s.name.toLowerCase() === want) ||
                  main.find(s => s.name.toLowerCase().includes(want));
    if (scene && !scene.current) meld.api.showScene(scene.id);
  }

  // ------------------------------------------------------------ rendering ---
  function renderBond(b) {
    document.querySelectorAll("[data-bond]").forEach(el => {
      if (!b || b.price == null) { el.classList.add("hidden"); return; }
      el.classList.remove("hidden");
      el.innerHTML = `<div class="kicker">${t("bond")}</div>
        <div class="row"><div class="title" style="font-size:${el.dataset.size || 26}px">${esc(b.character || "")}</div>
        <div class="pct">${b.pct == null ? "–" : b.pct + "%"}</div></div>
        <div class="bar"><i style="width:${b.pct || 0}%"></i></div>
        <div class="nums">${b.cash == null ? gp(b.price) + " gp" : t("has", { cash: "<b>" + gp(b.cash) + "</b>", price: gp(b.price) })}</div>`;
    });
  }

  const shown = new Set();
  let firstFeed = true, bubbleTimer = null;
  function renderFeed(feed) {
    const slot = document.getElementById("claude-slot");
    const toasts = document.getElementById("toast-slot");
    for (const m of feed || []) {
      if (shown.has(m.id)) continue;
      shown.add(m.id);
      if (firstFeed && Date.now() / 1000 - m.ts > 20) continue;      // don't replay old messages on reload
      if (m.source === "claude" && slot) {
        const q = m.question;
        const text = m.text.replace(/^🤖\s*/, "").replace(q ? new RegExp("^@" + q.user.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s*") : /^$/, "");
        slot.innerHTML = `<div class="card claude-bubble"><div class="who">${CLAUDE_ICON}${t("claude")}</div>
          ${q ? `<div class="q"><b>${esc(q.user)}</b> · ${esc(q.text)}</div>` : ""}
          <div class="a">${esc(text)}</div></div>`;
        requestAnimationFrame(() => slot.firstChild.classList.add("show"));
        clearTimeout(bubbleTimer);
        bubbleTimer = setTimeout(() => slot.firstChild && slot.firstChild.classList.remove("show"),
                                 Math.min(40000, 9000 + text.length * 70));
      } else if (toasts) {
        const d = document.createElement("div");
        d.className = "toast"; d.textContent = m.text;
        toasts.appendChild(d); setTimeout(() => d.remove(), 12500);
        while (toasts.children.length > 3) toasts.firstChild.remove();
      }
    }
    firstFeed = false;
  }
  const seenAlerts = new Set();
  let firstAlerts = true;
  function renderAlerts(alerts) {
    const toasts = document.getElementById("toast-slot");
    for (const a of alerts || []) {
      const key = a.ts + a.title;
      if (seenAlerts.has(key)) continue;
      seenAlerts.add(key);
      if (firstAlerts || !toasts || params.get("alerts") === "0") continue;
      const d = document.createElement("div");
      d.className = "toast alert" + (a.important ? " important" : "");
      d.innerHTML = `<b>${esc(a.title)}</b><br><span style="font-weight:500">${esc(a.body || "")}</span>`;
      toasts.appendChild(d); setTimeout(() => d.remove(), 12500);
    }
    firstAlerts = false;
  }

  // ------------------------------------------------------------ polling ---
  let state = null, onState = null;
  async function poll() {
    try {
      const r = await fetch("/api/stream/state", { cache: "no-store" });
      state = await r.json();
      if (!params.get("lang") && state.lang && state.lang !== lang) { lang = state.lang; applyT(); }
      renderBond(state.bond);
      renderFeed(state.feed);
      renderAlerts(state.alerts);
      handleSceneRequest(state.scene_request);
      if (onState) onState(state);
    } catch (e) { /* toolkit not running yet: retry */ }
    meldReport(false);
    setTimeout(poll, 2000);
  }

  function applyT() { document.querySelectorAll("[data-t]").forEach(el => { el.innerHTML = t(el.dataset.t); }); }

  window.Stream = {
    t, esc, gp, params, CLAUDE_ICON, get state() { return state; },
    start(opts = {}) {
      onState = opts.onState || null;
      applyT();
      meldConnect();
      poll();
    },
  };
})();
