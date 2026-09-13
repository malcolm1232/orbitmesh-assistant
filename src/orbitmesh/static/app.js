/* OrbitMesh Support Assistant - web UI. Vanilla JS, no build step, no external resources. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const fmtTime = (t) => t ? new Date(t * 1000).toLocaleString() : "-";
  const fmtAgo = (s) => s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s/60)}m` : `${Math.floor(s/3600)}h ${Math.floor(s%3600/60)}m`;

  let toastTimer;
  function toast(msg, bad = false) {
    const t = $("#toast"); t.textContent = msg; t.className = "toast" + (bad ? " bad" : ""); t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), bad ? 6000 : 3000);
  }
  async function api(path, opts = {}) {
    const r = await fetch(path, opts);
    let body = null;
    try { body = await r.json(); } catch (_) { /* no body */ }
    if (!r.ok) throw new Error((body && body.detail) || `${r.status} ${r.statusText}`);
    return body;
  }

  // ---------------------------------------------------------------- navigation
  function show(view) {
    $$(".nav-item").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
    $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
    $(".content").classList.toggle("bleed", view === "connectors");
    if (view === "connectors") loadConnectors();
    if (view === "dashboard") loadDashboard();
  }
  $$(".nav-item").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); location.hash = a.dataset.view; show(a.dataset.view); }));
  window.addEventListener("hashchange", () => show(location.hash.slice(1) || "ask"));

  async function refreshHealth() {
    try {
      const h = await api("/health");
      const pill = $("#index-pill");
      pill.textContent = `index: ${h.index_chunks} chunks`;
      pill.className = "pill " + (h.index_current ? "ok" : "warn");
      pill.title = h.index_current ? "index matches the enabled connectors" : "index needs a sync";
      $("#foot-model").textContent = `${h.llm} · ${h.model}`;
    } catch (_) { /* offline */ }
  }

  // ---------------------------------------------------------------- ask
  let sessionId = newSession();
  function newSession() { return "web-" + Math.random().toString(36).slice(2, 10); }
  function resetChat() {
    sessionId = newSession(); $("#session-id").textContent = sessionId;
    $("#chat-log").innerHTML = `<div class="empty">Describe the problem, e.g. <em>“My node keeps disconnecting”</em>.</div>`;
    $("#facts").innerHTML = `<dd class="muted">nothing yet</dd>`; $("#turn-meta").textContent = "-"; $("#evidence").innerHTML = `<li class="muted">-</li>`;
  }
  $("#new-conv").addEventListener("click", resetChat);
  $("#session-id").textContent = sessionId;

  function addMsg(cls, text, metaHtml) {
    const log = $("#chat-log"); const empty = $(".empty", log); if (empty) empty.remove();
    const d = document.createElement("div"); d.className = `msg ${cls}`; d.textContent = text;
    if (metaHtml) { const m = document.createElement("div"); m.className = "meta"; m.innerHTML = metaHtml; d.appendChild(m); }
    log.appendChild(d); log.scrollTop = 1e9; return d;
  }
  $("#chat-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("#chat-input"); const text = input.value.trim(); if (!text) return;
    input.value = ""; addMsg("you", text);
    const pending = addMsg("bot", "…"); $("#chat-send").disabled = true;
    try {
      const j = await api("/chat", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ session_id: sessionId, message: text }) });
      pending.remove();
      const cites = (j.citations || []).map((c) => {
        const ev = (j.evidence || []).find((e) => e.source_id === c.source_id && e.locator === c.locator);
        return `<span class="cite">${esc(ev ? ev.connector_id + " / " : "")}${esc(c.source_id)} › ${esc(c.locator)}</span>`;
      }).join("");
      addMsg("bot", j.response, `<span class="chip ${esc(j.action)}">${esc(j.action)}</span>${cites}`);
      renderFacts(j.facts || {}); renderEvidence(j.evidence || []);
      const g = j.guardrails || {}; const flags = [...Object.keys(g.input || {}), ...(g.output || [])];
      $("#turn-meta").innerHTML = `turn ${j.turn} · ${j.latency_ms} ms` + (flags.length ? `<br>guardrails: ${esc(flags.join("; "))}` : "");
    } catch (err) { pending.textContent = "Error: " + err.message; pending.classList.add("error"); }
    finally { $("#chat-send").disabled = false; input.focus(); refreshHealth(); }
  });
  function renderFacts(facts) {
    const keys = Object.keys(facts); const dl = $("#facts");
    dl.innerHTML = keys.length ? keys.map((k) => `<dt>${esc(k)}</dt><dd>${esc(String(facts[k]))}</dd>`).join("") : `<dd class="muted">nothing yet</dd>`;
  }
  function renderEvidence(ev) {
    $("#evidence").innerHTML = ev.length ? ev.map((e) => `<li><b>${esc(e.source_id)}</b> › ${esc(e.locator)}${e.subsection ? " › " + esc(e.subsection) : ""}<br><span class="tag">${esc(e.connector_id)} · ${esc(e.product_line)}${e.archived ? ' · <span class="arch">ARCHIVED</span>' : ""}</span></li>`).join("") : `<li class="muted">-</li>`;
  }

  // ---------------------------------------------------------------- connectors: node canvas
  // DBSearch.AI's Connectors canvas, cut down to what this assistant has: a rail of node kinds,
  // a pan/zoom canvas where every node wires into ONE knowledge-base hub, and an inspector.
  // Adding a node drops a DRAFT on the canvas (nothing exists server-side yet); the inspector
  // turns it into a real connector. Node positions are a per-browser convenience (localStorage).
  const KINDS = {
    corpus:     { label: "OrbitMesh corpus", mono: "OM", cap: "supplied · read-only", color: "#2457c5" },
    upload:     { label: "Upload files", mono: "UP", cap: ".md documents", color: "#7c4dff",
                  note: "Drop Markdown files on this node. Uploading a file with the same name again replaces its chunks." },
    gdrive:     { label: "Google Drive", mono: "GD", cap: "public link", color: "#1f8a4c", ph: "https://drive.google.com/file/d/…",
                  note: "A public file or Google Doc link (Share → Anyone with the link). Folder links need GOOGLE_API_KEY on the server." },
    sharepoint: { label: "SharePoint", mono: "SP", cap: "anyone-with-link", color: "#0f8b8d", ph: "https://<tenant>.sharepoint.com/:f:/…",
                  note: "An “Anyone with the link” file or folder link. No Microsoft sign-in; folders are crawled for .md files." },
  };
  const ADDABLE = ["upload", "gdrive", "sharepoint"];
  const HUB = { x: 1000, y: 650 }, HUB_W = 168, NODE_W = 204;
  const POS_KEY = "orbitmesh.canvas.positions";
  const canvasEl = $("#cv-canvas"), world = $("#cv-world"), edgesEl = $("#cv-edges"), panel = $("#cv-panel");
  let nodes = [];                 // {uid, kind, c: connector|null, draft: {name, source, error, busy}|null, x, y}
  let selected = null, indexCurrent = true, draftSeq = 0;
  let view = { x: 0, y: 0, k: 1 }, fitted = false;

  const loadPos = () => { try { return JSON.parse(localStorage.getItem(POS_KEY) || "{}"); } catch (_) { return {}; } };
  function savePos(only) {
    try { const p = loadPos(); only.filter((n) => n.c).forEach((n) => (p[n.c.id] = [Math.round(n.x), Math.round(n.y)])); localStorage.setItem(POS_KEY, JSON.stringify(p)); } catch (_) { /* storage blocked */ }
  }
  // Slots around the hub, right side first (the supplied corpus sits on the left), then outer rings.
  const SLOT_ANGLES = [0, -40, 40, -140, 140, -90, 90, -65, 65, -115, 115];
  function freeSlot() {
    const taken = nodes.map((n) => [n.x + NODE_W / 2, n.y + 45]);
    for (let ring = 0; ring < 3; ring++) {
      for (const deg of SLOT_ANGLES) {
        const a = deg * Math.PI / 180;
        const cx = HUB.x + Math.cos(a) * (290 + ring * 240), cy = HUB.y + Math.sin(a) * (240 + ring * 150);
        if (taken.every(([x, y]) => Math.abs(x - cx) > NODE_W + 16 || Math.abs(y - cy) > 120)) return [Math.round(cx - NODE_W / 2), Math.round(cy - 45)];
      }
    }
    return [HUB.x - NODE_W / 2 + nodes.length * 24, HUB.y + 240 + nodes.length * 24];
  }

  function kindTiles() {
    $("#cv-kinds").innerHTML = ADDABLE.map((k) => { const d = KINDS[k];
      return `<button class="kind-tile" style="--k:${d.color}" data-add="${k}" title="Add a node: ${esc(d.label)}">
        <span class="mono-chip" style="--k:${d.color}">${d.mono}</span><span class="tn"><b>${esc(d.label)}</b><span>${esc(d.cap)}</span></span><span class="plus">+</span></button>`; }).join("");
    $$("[data-add]").forEach((b) => b.addEventListener("click", () => addNode(b.dataset.add)));
  }
  function addNode(kind) {
    const n = nodes.filter((x) => x.kind === kind).length + 1;
    const [x, y] = freeSlot();
    const node = { uid: `draft-${++draftSeq}`, kind, c: null, draft: { name: `${KINDS[kind].label} ${n}`, source: "", error: "", busy: false }, x, y };
    nodes.push(node); selected = node.uid; render();
    revealNode(node);
    const f = $("#p-name"); if (f) { f.focus(); f.select(); }
  }

  async function loadConnectors() {
    try {
      const j = await api("/api/connectors");
      indexCurrent = j.index_current;
      const pos = loadPos();
      const keep = nodes.filter((n) => !n.c);                       // drafts survive a reload
      const prev = nodes;
      nodes = [...keep];                                             // freeSlot() avoids everything placed so far
      j.connectors.forEach((c) => {
        const old = prev.find((n) => n.c && n.c.id === c.id);
        if (old) { old.c = c; nodes.push(old); return; }
        const node = { uid: `c-${c.id}`, kind: c.kind, c, draft: null, x: 0, y: 0 };
        if (pos[c.id]) [node.x, node.y] = pos[c.id];
        else if (c.kind === "corpus") [node.x, node.y] = [HUB.x - 290 - NODE_W / 2, HUB.y - 45];
        else [node.x, node.y] = freeSlot();
        nodes.push(node);
      });
      nodes = [...nodes.filter((n) => n.c), ...keep];
      if (selected && !nodes.some((n) => n.uid === selected)) selected = null;
      render();
      if (!fitted) requestAnimationFrame(() => { if (canvasEl.clientWidth) { fit(false); fitted = true; } });
      refreshHealth();
    } catch (err) { panel.innerHTML = `<p class="error">${esc(err.message)}</p>`; }
  }

  function nodeState(n) {
    if (!n.c) return ["draft", "draft - not connected yet"];
    if (!n.c.enabled) return ["off", "disabled - excluded from retrieval"];
    if (n.c.last_error) return ["error", "last fetch failed"];
    if (!n.c.documents.length) return ["empty", "no documents yet"];
    if (n.c.chunks > 0 && indexCurrent) return ["indexed", "indexed and searchable"];
    return ["pending", "waiting for a sync"];
  }
  function render() {
    world.querySelectorAll(".node").forEach((el) => el.remove());
    nodes.forEach((n) => {
      const d = KINDS[n.kind] || { label: n.kind, mono: "?", color: "#8a94a6" };
      const [st, why] = nodeState(n);
      const el = document.createElement("div");
      el.className = `node${selected === n.uid ? " sel" : ""}${n.c ? "" : " draft"}${st === "off" ? " off" : ""}`;
      el.style.cssText = `--k:${d.color};left:${n.x}px;top:${n.y}px`;
      el.dataset.uid = n.uid;
      const pills = n.c
        ? `<span class="npill">${n.c.documents.length} doc${n.c.documents.length === 1 ? "" : "s"}</span><span class="npill">${n.c.chunks} chunks</span>${n.c.read_only ? `<span class="npill">read-only</span>` : ""}${st === "off" ? `<span class="npill warn">off</span>` : ""}`
        : `<span class="npill warn">draft</span>`;
      el.innerHTML = `<div class="nhead"><span class="mono-chip" style="--k:${d.color}">${d.mono}</span><div class="nt"><div class="nname" title="${esc(n.c ? n.c.name : n.draft.name)}">${esc(n.c ? n.c.name : n.draft.name)}</div><div class="nkind">${esc(n.kind)}</div></div><span class="status ${st}" title="${esc(why)}"></span></div>
        <div class="nbody">${pills}</div>${n.c && n.c.last_error ? `<div class="nreason" title="${esc(n.c.last_error)}">${esc(n.c.last_error)}</div>` : ""}`;
      world.appendChild(el);
    });
    const total = nodes.reduce((a, n) => a + (n.c && n.c.enabled ? n.c.chunks : 0), 0);
    $("#hub-sub").textContent = `${total} chunks · one index`;
    const hub = $("#cv-hub"); hub.style.left = `${HUB.x - HUB_W / 2}px`; hub.style.top = `${HUB.y - hub.offsetHeight / 2}px`;
    drawEdges(); renderPanel(); renderStatus();
  }
  function drawEdges() {
    const hub = $("#cv-hub"), hubH = hub.offsetHeight || 96;
    edgesEl.innerHTML = nodes.map((n) => {
      const el = world.querySelector(`.node[data-uid="${n.uid}"]`); const h = el ? el.offsetHeight : 90;
      const ncx = n.x + NODE_W / 2, ncy = n.y + h / 2;
      let d;
      if (Math.abs(ncx - HUB.x) < (NODE_W + HUB_W) / 2 + 24) {
        // stacked above or below the hub: leave through the facing edge and enter vertically
        const below = ncy > HUB.y;
        const y1 = below ? n.y : n.y + h, y2 = below ? HUB.y + hubH / 2 : HUB.y - hubH / 2;
        const dy = Math.max(30, Math.abs(y2 - y1) / 2) * (below ? -1 : 1);
        d = `M${ncx},${y1} C${ncx},${y1 + dy} ${HUB.x},${y2 - dy} ${HUB.x},${y2}`;
      } else {
        const left = ncx < HUB.x;
        const x1 = left ? n.x + NODE_W : n.x, x2 = left ? HUB.x - HUB_W / 2 : HUB.x + HUB_W / 2;
        const dx = Math.max(40, Math.abs(x2 - x1) / 2) * (left ? 1 : -1);
        d = `M${x1},${ncy} C${x1 + dx},${ncy} ${x2 - dx},${HUB.y} ${x2},${HUB.y}`;
      }
      const live = n.c && n.c.enabled && n.c.chunks > 0;
      return `<path class="${live ? "flow" : "idle"}" style="--k:${(KINDS[n.kind] || {}).color || "#8a94a6"}" d="${d}"/>`;
    }).join("");
  }
  function renderStatus() {
    const real = nodes.filter((n) => n.c), on = real.filter((n) => n.c.enabled);
    const chunks = on.reduce((a, n) => a + n.c.chunks, 0), docs = on.reduce((a, n) => a + n.c.documents.length, 0);
    const drafts = nodes.length - real.length;
    $("#cv-status").innerHTML = `<span><span class="dot ${indexCurrent ? "" : "warn"}"></span>index ${indexCurrent ? "current" : "needs a sync"}</span>
      <span>${real.length} node${real.length === 1 ? "" : "s"} · ${on.length} enabled</span><span>${docs} documents · ${chunks} chunks</span>${drafts ? `<span>${drafts} draft${drafts === 1 ? "" : "s"}</span>` : ""}`;
  }

  // ---- inspector
  function renderPanel() {
    const n = nodes.find((x) => x.uid === selected);
    if (!n) {
      panel.innerHTML = `<div class="p-empty"><div class="eyebrow">Inspector</div>Select a node to configure it.
        <ol><li>Pick a kind in <b>Add a node</b> on the left.</li><li>Name it and paste a link, or drop <code>.md</code> files.</li><li>Its documents are chunked into the shared index and cited as <code>node / document / section</code> in Ask.</li></ol></div>`;
      return;
    }
    const d = KINDS[n.kind] || { label: n.kind, mono: "?", color: "#8a94a6" };
    const head = `<div class="p-head"><span class="mono-chip" style="--k:${d.color}">${d.mono}</span><div class="pt"><b>${esc(n.c ? n.c.name : n.draft.name)}</b><span>${esc(n.kind)}${n.c ? " · " + esc(n.c.id) : " · draft"}</span></div><button class="btn tiny ghost" id="p-close" aria-label="Close inspector">✕</button></div>`;
    if (!n.c) {
      const link = n.kind === "upload" ? "" : `<label class="p-field">Link<input id="p-source" value="${esc(n.draft.source)}" placeholder="${esc(d.ph)}" spellcheck="false"></label>`;
      panel.innerHTML = `${head}<label class="p-field">Name<input id="p-name" value="${esc(n.draft.name)}" maxlength="80"></label>${link}
        <p class="p-note">${esc(d.note)}</p>
        ${n.kind === "upload" ? `<div class="dropzone" id="p-drop">Drop .md files here or click to choose<br><span class="small">creates the node and indexes the files</span><input type="file" accept=".md,.markdown,.txt" multiple hidden></div>` : ""}
        ${n.draft.error ? `<p class="error">${esc(n.draft.error)}</p>` : ""}
        <div class="p-actions"><button class="btn primary" id="p-create" ${n.draft.busy ? "disabled" : ""}>${n.draft.busy ? "Connecting…" : n.kind === "upload" ? "Create empty node" : "Connect &amp; index"}</button><button class="btn ghost" id="p-discard">Discard</button></div>`;
      $("#p-name").addEventListener("input", (e) => { n.draft.name = e.target.value; const nm = world.querySelector(`.node[data-uid="${n.uid}"] .nname`); if (nm) nm.textContent = e.target.value; $(".p-head b", panel).textContent = e.target.value; });
      const src = $("#p-source"); if (src) { src.addEventListener("input", (e) => (n.draft.source = e.target.value)); src.addEventListener("keydown", (e) => { if (e.key === "Enter") createFromDraft(n); }); }
      $("#p-create").addEventListener("click", () => createFromDraft(n));
      $("#p-discard").addEventListener("click", () => { nodes = nodes.filter((x) => x !== n); selected = null; render(); });
      wireDrop($("#p-drop"), async (files) => { const c = await createFromDraft(n); if (c) await uploadFiles(c.id, files); });
    } else {
      const c = n.c, st = nodeState(n);
      const docs = c.documents.map((doc) => `<li><div class="dt"><b>${esc(doc.title)}</b><div class="source"><span class="nw">${esc(doc.filename)}</span> · <span class="nw">${doc.version ? "v" + esc(doc.version) : "no version"}</span> · <span class="nw">${(doc.bytes / 1024).toFixed(1)} KB</span></div></div>${c.read_only ? "" : `<button class="btn tiny danger" data-del-doc="${esc(doc.doc_id)}">remove</button>`}</li>`).join("");
      const source = c.kind === "corpus" ? "corpus/manifest.json - supplied with the assignment" : c.kind === "upload" ? "documents uploaded through this page" : c.source;
      panel.innerHTML = `${head}
        <div class="p-row"><div><b>${c.enabled ? "Enabled" : "Disabled"}</b><div class="muted">${esc(st[1])}</div></div><button class="switch ${c.enabled ? "on" : ""}" id="p-toggle" role="switch" aria-checked="${c.enabled}" aria-label="Include this node in retrieval"></button></div>
        <div class="p-stats"><div class="p-stat"><div class="k">documents</div><div class="v">${c.documents.length}</div></div><div class="p-stat"><div class="k">chunks</div><div class="v">${c.chunks}</div></div><div class="p-stat"><div class="k">updated</div><div class="v">${c.documents.length ? esc(new Date(Math.max(...c.documents.map((x) => x.fetched_at || 0)) * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" })) : "-"}</div></div></div>
        <div class="source">${esc(source)}</div>
        ${c.last_error ? `<p class="error">Last sync error: ${esc(c.last_error)}</p>` : ""}
        <div class="p-actions">${c.kind === "gdrive" || c.kind === "sharepoint" ? `<button class="btn tiny" id="p-refetch">Re-fetch &amp; sync</button>` : ""}${c.read_only ? "" : `<button class="btn tiny danger" id="p-delete">Delete node</button>`}</div>
        ${c.kind === "upload" ? `<div class="dropzone" id="p-drop">Drop .md files here or click to choose<input type="file" accept=".md,.markdown,.txt" multiple hidden></div>` : ""}
        <div class="eyebrow" style="margin-top:18px">Documents</div>
        <ul class="p-docs">${docs || `<li class="muted">no documents yet</li>`}</ul>`;
      $("#p-toggle").addEventListener("click", async (e) => {
        const on = !c.enabled; e.currentTarget.disabled = true;
        try { await api(`/api/connectors/${c.id}/enabled`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ enabled: on }) }); toast(on ? "Node enabled and indexed" : "Node disabled; its chunks were removed"); }
        catch (err) { toast(err.message, true); }
        loadConnectors();
      });
      const rf = $("#p-refetch"); if (rf) rf.addEventListener("click", async () => {
        rf.disabled = true; rf.textContent = "Fetching…";
        try { const j = await api(`/api/connectors/${c.id}/sync`, { method: "POST" }); toast(`Fetched ${j.fetched} document(s); index now ${j.sync.total} chunks`); } catch (err) { toast(err.message, true); }
        loadConnectors();
      });
      const del = $("#p-delete"); if (del) del.addEventListener("click", async () => {
        if (!del.dataset.armed) { del.dataset.armed = "1"; del.textContent = "Click again to delete"; setTimeout(() => { if (del.isConnected) { delete del.dataset.armed; del.textContent = "Delete node"; } }, 3500); return; }
        del.disabled = true;
        try { await api(`/api/connectors/${c.id}`, { method: "DELETE" }); toast("Node deleted and its chunks removed"); selected = null; } catch (err) { toast(err.message, true); }
        loadConnectors();
      });
      $$("[data-del-doc]", panel).forEach((b) => b.addEventListener("click", async () => {
        b.disabled = true;
        try { await api(`/api/connectors/${c.id}/documents/${b.dataset.delDoc}`, { method: "DELETE" }); toast("Document removed and index synced"); } catch (err) { toast(err.message, true); }
        loadConnectors();
      }));
      wireDrop($("#p-drop"), (files) => uploadFiles(c.id, files).then(loadConnectors));
    }
    $("#p-close").addEventListener("click", () => { selected = null; render(); });
  }
  function wireDrop(dz, onFiles) {
    if (!dz) return;
    const input = $("input[type=file]", dz);
    dz.addEventListener("click", () => input.click());
    input.addEventListener("change", () => input.files.length && onFiles(input.files));
    dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
    dz.addEventListener("dragleave", () => dz.classList.remove("over"));
    dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("over"); if (e.dataTransfer.files.length) onFiles(e.dataTransfer.files); });
  }
  async function createFromDraft(n) {
    if (n.draft.busy) return null;
    const body = { name: n.draft.name.trim(), kind: n.kind, source: (n.draft.source || "").trim() };
    if (!body.name) { n.draft.error = "Give the node a name."; renderPanel(); return null; }
    if (n.kind !== "upload" && !body.source) { n.draft.error = "Paste a public link first."; renderPanel(); return null; }
    n.draft.busy = true; n.draft.error = ""; renderPanel();
    try {
      const j = await api("/api/connectors", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      const c = j.connector;
      // The draft BECOMES the node: same place on the canvas, now backed by a real connector.
      Object.assign(n, { uid: `c-${c.id}`, c, draft: null }); selected = n.uid; savePos([n]);
      if (j.error) toast(`Node created, but the fetch failed: ${j.error}`, true);
      else if (j.fetched) toast(`Fetched ${j.fetched} document(s) and indexed`);
      else toast("Node created - now add documents");
      await loadConnectors();
      return c;
    } catch (err) { n.draft.busy = false; n.draft.error = err.message; renderPanel(); return null; }
  }
  async function uploadFiles(cid, files) {
    const fd = new FormData(); Array.from(files).forEach((f) => fd.append("files", f, f.name));
    try { const j = await api(`/api/connectors/${cid}/upload`, { method: "POST", body: fd }); toast(`Added ${j.added.length} document(s); index now ${j.sync.total} chunks (${j.sync.deleted} stale removed)`); }
    catch (err) { toast(err.message, true); }
    await loadConnectors();
  }
  $("#sync-all").addEventListener("click", async (e) => {
    const b = e.currentTarget; b.disabled = true;
    try { const j = await api("/api/ingest", { method: "POST" }); toast(`Synced: ${j.sync.total} chunks, ${j.sync.written} written, ${j.sync.deleted} stale removed`); } catch (err) { toast(err.message, true); }
    b.disabled = false; loadConnectors();
  });

  // ---- pan, zoom, drag
  function applyView(animate) {
    world.classList.toggle("animating", !!animate);
    world.style.transform = `translate(${view.x}px,${view.y}px) scale(${view.k})`;
    $("#zoom-pct").textContent = `${Math.round(view.k * 100)}%`;
    if (animate) setTimeout(() => world.classList.remove("animating"), 280);
  }
  function zoomAt(k, sx, sy, animate) {
    k = Math.min(1.6, Math.max(0.35, k));
    const wx = (sx - view.x) / view.k, wy = (sy - view.y) / view.k;
    view = { k, x: sx - wx * k, y: sy - wy * k }; applyView(animate);
  }
  function fit(animate = true) {
    const W = canvasEl.clientWidth, H = canvasEl.clientHeight; if (!W || !H) return;
    const xs = [HUB.x - HUB_W / 2, HUB.x + HUB_W / 2], ys = [HUB.y - 60, HUB.y + 60];
    nodes.forEach((n) => { const el = world.querySelector(`.node[data-uid="${n.uid}"]`); xs.push(n.x, n.x + NODE_W); ys.push(n.y, n.y + (el ? el.offsetHeight : 90)); });
    const pad = 48, x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    // Floor at 0.62: below that the cards stop being readable, and panning is cheaper than squinting.
    const k = Math.min(1, Math.max(0.62, Math.min((W - pad * 2) / (x1 - x0), (H - pad * 2) / (y1 - y0))));
    view = { k, x: (W - (x1 - x0) * k) / 2 - x0 * k, y: (H - (y1 - y0) * k) / 2 - y0 * k }; applyView(animate);
  }
  function revealNode(n) {           // pan just enough to bring a new node into view
    const W = canvasEl.clientWidth, H = canvasEl.clientHeight; if (!W) return;
    const sx = n.x * view.k + view.x, sy = n.y * view.k + view.y, w = NODE_W * view.k, h = 100 * view.k;
    if (sx >= 16 && sy >= 16 && sx + w <= W - 16 && sy + h <= H - 16) return;
    view.x = W / 2 - (n.x + NODE_W / 2) * view.k; view.y = H / 2 - (n.y + 50) * view.k; applyView(true);
  }
  $("#zoom-in").addEventListener("click", () => zoomAt(view.k * 1.2, canvasEl.clientWidth / 2, canvasEl.clientHeight / 2, true));
  $("#zoom-out").addEventListener("click", () => zoomAt(view.k / 1.2, canvasEl.clientWidth / 2, canvasEl.clientHeight / 2, true));
  $("#zoom-pct").addEventListener("click", () => zoomAt(1, canvasEl.clientWidth / 2, canvasEl.clientHeight / 2, true));
  $("#zoom-fit").addEventListener("click", () => fit(true));
  canvasEl.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = canvasEl.getBoundingClientRect();
    if (e.ctrlKey || e.metaKey) zoomAt(view.k * Math.exp(-e.deltaY * 0.01), e.clientX - r.left, e.clientY - r.top, false);
    else { view.x -= e.deltaX; view.y -= e.deltaY; applyView(false); }
  }, { passive: false });

  let gesture = null;
  canvasEl.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || e.target.closest(".zoomctl")) return;
    const el = e.target.closest(".node");
    const n = el && nodes.find((x) => x.uid === el.dataset.uid);
    gesture = { n, el, sx: e.clientX, sy: e.clientY, ox: n ? n.x : view.x, oy: n ? n.y : view.y, moved: false };
    canvasEl.setPointerCapture(e.pointerId);
  });
  canvasEl.addEventListener("pointermove", (e) => {
    if (!gesture) return;
    const dx = e.clientX - gesture.sx, dy = e.clientY - gesture.sy;
    if (!gesture.moved && Math.hypot(dx, dy) < 4) return;
    gesture.moved = true;
    if (gesture.n) {
      gesture.n.x = Math.round(gesture.ox + dx / view.k); gesture.n.y = Math.round(gesture.oy + dy / view.k);
      gesture.el.style.left = `${gesture.n.x}px`; gesture.el.style.top = `${gesture.n.y}px`; gesture.el.classList.add("dragging");
      drawEdges();
    } else { canvasEl.classList.add("panning"); view.x = gesture.ox + dx; view.y = gesture.oy + dy; applyView(false); }
  });
  function endGesture() {
    if (!gesture) return;
    const g = gesture; gesture = null; canvasEl.classList.remove("panning");
    if (g.n && g.moved) { g.el.classList.remove("dragging"); savePos([g.n]); return; }
    if (g.moved) return;
    const next = g.n ? g.n.uid : null;
    if (next !== selected) { selected = next; render(); }
  }
  canvasEl.addEventListener("pointerup", endGesture);
  canvasEl.addEventListener("pointercancel", endGesture);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && selected && location.hash === "#connectors" && !e.target.closest("input")) { selected = null; render(); }
  });
  window.addEventListener("resize", () => { if (location.hash === "#connectors") drawEdges(); });
  kindTiles();

  // ---------------------------------------------------------------- dashboard
  function bars(canvas, labels, values, colors) {
    const ctx = canvas.getContext("2d"); const W = canvas.width, H = canvas.height; ctx.clearRect(0, 0, W, H);
    const max = Math.max(1, ...values); const n = values.length; const pad = 34, gap = 14; const bw = (W - pad * 2 - gap * (n - 1)) / n;
    ctx.font = "12px system-ui"; ctx.textAlign = "center";
    values.forEach((v, i) => {
      const h = (H - 60) * v / max; const x = pad + i * (bw + gap); const y = H - 30 - h;
      ctx.fillStyle = colors[i % colors.length]; ctx.fillRect(x, y, bw, h);
      ctx.fillStyle = "#1a1f2b"; ctx.fillText(String(Number.isInteger(v) ? v : v.toFixed(2)), x + bw / 2, y - 5);
      ctx.fillStyle = "#5f6b7c"; ctx.fillText(labels[i].length > 14 ? labels[i].slice(0, 13) + "…" : labels[i], x + bw / 2, H - 12);
    });
    if (!values.some((v) => v > 0)) { ctx.fillStyle = "#8a94a6"; ctx.fillText("no data yet", W / 2, H / 2); }
  }
  const palette = ["#2457c5", "#1f8a4c", "#7c4dff", "#c0392b", "#b7791f", "#0f8b8d"];
  async function loadDashboard() {
    try {
      const [s, ss] = await Promise.all([api("/api/stats"), api("/api/sessions")]);
      $("#dash-uptime").textContent = `up ${fmtAgo(s.uptime_s)}`;
      const lat = s.turn_latency;
      const cards = [
        ["Turns", s.turns.total, Object.entries(s.turns.by_action).map(([k, v]) => `${k} ${v}`).join(" · ")],
        ["Turn latency p50 / p95", lat.p50 == null ? "-" : `${lat.p50}s / ${lat.p95}s`, `mean ${lat.mean == null ? "-" : lat.mean.toFixed(2) + "s"}`],
        ["LLM cost (USD)", s.llm.cost_usd.toFixed(4), `${s.llm.prompt_tokens + s.llm.completion_tokens} tokens · ${s.llm.errors} errors`],
        ["Guardrail blocks", Object.values(s.guardrails.output).reduce((a, b) => a + b, 0) - (s.guardrails.output.ok || 0), `input flags ${Object.values(s.guardrails.input).reduce((a, b) => a + b, 0)}`],
        ["Index", `${s.index_chunks} chunks`, `${Object.values(s.connectors).filter((c) => c.enabled).length} enabled connectors · ${s.index_current ? "current" : "needs sync"}`],
        ["Retrieval", `${s.retrieval.empty} empty`, `mean ${s.retrieval.hits.mean == null ? "-" : s.retrieval.hits.mean.toFixed(1)} chunks/turn`],
        ["Model", s.llm.model, s.embedder],
        ["Errors", s.errors, "unhandled turn errors"],
      ];
      $("#dash-cards").innerHTML = cards.map(([k, v, sub]) => `<div class="card"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="s">${esc(sub)}</div></div>`).join("");
      const a = s.turns.by_action; bars($("#chart-actions"), Object.keys(a), Object.values(a), ["#2457c5", "#1f8a4c", "#0f8b8d", "#c0392b"]);
      const lb = lat.buckets.filter((b) => b.le < 1e300); bars($("#chart-latency"), lb.map((b) => `≤${b.le}s`), lb.map((b) => b.count), ["#7c4dff"]);
      const g = { ...Object.fromEntries(Object.entries(s.guardrails.input).map(([k, v]) => ["in:" + k, v])), ...Object.fromEntries(Object.entries(s.guardrails.output).map(([k, v]) => ["out:" + k, v])) };
      bars($("#chart-guard"), Object.keys(g), Object.values(g), palette);
      const cn = Object.values(s.connectors); bars($("#chart-connectors"), cn.map((c) => c.name), cn.map((c) => c.chunks), palette);
      $("#sessions-table tbody").innerHTML = ss.sessions.length ? ss.sessions.map((x) => `<tr><td><code>${esc(x.session_id)}</code></td><td>${x.turns}</td><td><span class="chip ${esc(x.last_action)}">${esc(x.last_action || "-")}</span></td><td>${x.resolved ? "resolved" : x.escalated ? "escalated" : "open"}</td><td class="small">${esc(Object.entries(x.facts).map(([k, v]) => `${k}=${v}`).join(", "))}</td><td class="small">${fmtTime(x.updated_at)}</td></tr>`).join("") : `<tr><td colspan="6" class="muted">no conversations yet</td></tr>`;
    } catch (err) { toast(err.message, true); }
  }
  $("#dash-refresh").addEventListener("click", loadDashboard);

  // ---------------------------------------------------------------- boot
  refreshHealth();
  show(location.hash.slice(1) || "ask");
})();
