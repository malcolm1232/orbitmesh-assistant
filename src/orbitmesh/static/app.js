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

  // ---------------------------------------------------------------- connectors
  async function loadConnectors() {
    const list = $("#connector-list"); list.innerHTML = `<div class="muted">loading…</div>`;
    try {
      const j = await api("/api/connectors");
      list.innerHTML = j.connectors.map(connectorCard).join("");
      wireConnectorCards();
      refreshHealth();
    } catch (err) { list.innerHTML = `<p class="error">${esc(err.message)}</p>`; }
  }
  function connectorCard(c) {
    const docs = (c.documents || []).map((d) => `<tr><td>${esc(d.title)}<div class="source">${esc(d.filename)}</div></td><td>${esc(d.version || "-")}</td><td>${esc(d.effective_date || "-")}</td><td>${(d.bytes/1024).toFixed(1)} KB</td><td>${fmtTime(d.fetched_at)}</td><td>${c.read_only ? "" : `<button class="btn tiny danger" data-del-doc="${esc(d.doc_id)}" data-cid="${esc(c.id)}">remove</button>`}</td></tr>`).join("");
    const hint = c.kind === "upload" ? `<div class="dropzone" data-cid="${esc(c.id)}">Drop .md files here or click to choose<input type="file" accept=".md,.markdown,.txt" multiple hidden></div>` : "";
    const src = c.source && c.kind !== "corpus" ? `<div class="source">${esc(c.source)}</div>` : (c.kind === "corpus" ? `<div class="source">corpus/manifest.json (supplied with the assignment, read-only)</div>` : "");
    const err = c.last_error ? `<p class="error">last sync error: ${esc(c.last_error)}</p>` : "";
    return `<div class="connector ${c.enabled ? "" : "off"}" data-cid="${esc(c.id)}">
      <div class="connector-head">
        <div class="connector-title"><b>${esc(c.name)}</b><span class="kind">${esc(c.kind)}</span><span class="muted small">${c.documents.length} docs · ${c.chunks} chunks</span></div>
        <div class="connector-actions">
          ${c.kind === "gdrive" || c.kind === "sharepoint" ? `<button class="btn tiny" data-sync="${esc(c.id)}">Re-fetch &amp; sync</button>` : ""}
          ${c.read_only ? "" : `<button class="btn tiny danger" data-delete="${esc(c.id)}">Delete</button>`}
          <div class="switch ${c.enabled ? "on" : ""}" data-toggle="${esc(c.id)}" title="${c.enabled ? "enabled" : "disabled"}"></div>
        </div>
      </div>
      ${src}${err}
      <table class="docs"><thead><tr><th>document</th><th>version</th><th>date</th><th>size</th><th>fetched</th><th></th></tr></thead><tbody>${docs || `<tr><td colspan="6" class="muted">no documents yet</td></tr>`}</tbody></table>
      ${hint}
    </div>`;
  }
  function wireConnectorCards() {
    $$("[data-toggle]").forEach((el) => el.addEventListener("click", async () => {
      const on = !el.classList.contains("on");
      try { await api(`/api/connectors/${el.dataset.toggle}/enabled`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ enabled: on }) }); toast(on ? "Connector enabled and indexed" : "Connector disabled; its chunks were removed"); }
      catch (err) { toast(err.message, true); }
      loadConnectors();
    }));
    $$("[data-delete]").forEach((el) => el.addEventListener("click", async () => {
      try { await api(`/api/connectors/${el.dataset.delete}`, { method: "DELETE" }); toast("Connector deleted"); } catch (err) { toast(err.message, true); }
      loadConnectors();
    }));
    $$("[data-sync]").forEach((el) => el.addEventListener("click", async () => {
      el.disabled = true; el.textContent = "Fetching…";
      try { const j = await api(`/api/connectors/${el.dataset.sync}/sync`, { method: "POST" }); toast(`Fetched ${j.fetched} document(s); index now ${j.sync.total} chunks`); } catch (err) { toast(err.message, true); }
      loadConnectors();
    }));
    $$("[data-del-doc]").forEach((el) => el.addEventListener("click", async () => {
      try { await api(`/api/connectors/${el.dataset.cid}/documents/${el.dataset.delDoc}`, { method: "DELETE" }); toast("Document removed and index synced"); } catch (err) { toast(err.message, true); }
      loadConnectors();
    }));
    $$(".dropzone").forEach((dz) => {
      const input = $("input[type=file]", dz);
      dz.addEventListener("click", () => input.click());
      input.addEventListener("change", () => uploadFiles(dz.dataset.cid, input.files));
      dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
      dz.addEventListener("dragleave", () => dz.classList.remove("over"));
      dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("over"); uploadFiles(dz.dataset.cid, e.dataTransfer.files); });
    });
  }
  async function uploadFiles(cid, files) {
    if (!files || !files.length) return;
    const fd = new FormData(); Array.from(files).forEach((f) => fd.append("files", f, f.name));
    try { const j = await api(`/api/connectors/${cid}/upload`, { method: "POST", body: fd }); toast(`Added ${j.added.length} document(s); index now ${j.sync.total} chunks (${j.sync.deleted} stale removed)`); }
    catch (err) { toast(err.message, true); }
    loadConnectors();
  }
  $("#sync-all").addEventListener("click", async () => {
    try { const j = await api("/api/ingest", { method: "POST" }); toast(`Synced: ${j.sync.total} chunks, ${j.sync.written} written, ${j.sync.deleted} stale removed`); } catch (err) { toast(err.message, true); }
    loadConnectors();
  });

  // add-connector modal
  const modal = $("#modal");
  const hints = { upload: "Create it, then drop .md files onto the connector card.", gdrive: "Public file link (/file/d/… or a Google Doc). Folder links need GOOGLE_API_KEY on the server.", sharepoint: "An “Anyone with the link” file or folder link (Share → Anyone with the link → Copy link). No Microsoft sign-in needed; folders are crawled for .md files." };
  function syncKind() { const k = $("#c-kind").value; $("#c-source-row").hidden = k === "upload"; $("#c-hint").textContent = hints[k]; }
  $("#c-kind").addEventListener("change", syncKind);
  $("#add-connector").addEventListener("click", () => { $("#c-name").value = ""; $("#c-source").value = ""; $("#c-error").hidden = true; syncKind(); modal.hidden = false; $("#c-name").focus(); });
  $("#c-cancel").addEventListener("click", () => (modal.hidden = true));
  $("#c-create").addEventListener("click", async () => {
    const body = { name: $("#c-name").value.trim(), kind: $("#c-kind").value, source: $("#c-source").value.trim() };
    $("#c-create").disabled = true; $("#c-error").hidden = true;
    try {
      const j = await api("/api/connectors", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      modal.hidden = true;
      if (j.error) toast(`Connector created but the fetch failed: ${j.error}`, true);
      else toast(j.fetched ? `Fetched ${j.fetched} document(s) and indexed` : "Connector created - now add documents");
      loadConnectors();
    } catch (err) { $("#c-error").textContent = err.message; $("#c-error").hidden = false; }
    finally { $("#c-create").disabled = false; }
  });

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
