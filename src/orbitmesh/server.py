"""Optional HTTP wrapper around the same Agent, for cloud deployment and monitoring.

    POST /chat      {"session_id": "...", "message": "..."}  -> the JSONL response object
    GET  /health   liveness + index size (used by Cloud Run / load balancer probes)
    GET  /metrics   Prometheus exposition
    GET  /          a minimal browser chat page for manual testing

The CLI remains the primary interface; this exists so the deployment write-up has
something real behind it (see DEPLOYMENT.md and infra/).
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from .agent import build_agent
from .config import load_settings
from .observability import configure_logging

log = logging.getLogger("orbitmesh.server")


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=4000)


_PAGE = """<!doctype html><meta charset="utf-8"><title>OrbitMesh Support Assistant</title>
<style>body{font:15px system-ui;margin:0;background:#f5f6f8;color:#1b1f24}main{max-width:720px;margin:0 auto;padding:24px 16px}
h1{font-size:18px;margin:0 0 4px}p.sub{color:#5c6470;margin:0 0 16px}#log{background:#fff;border:1px solid #d9dde3;border-radius:8px;padding:12px;min-height:280px}
.m{margin:8px 0;padding:8px 10px;border-radius:6px;white-space:pre-wrap}.you{background:#e8f0fe}.bot{background:#f1f3f5}
.meta{font-size:12px;color:#5c6470;margin-top:4px}.act{display:inline-block;font-size:11px;padding:1px 6px;border-radius:10px;background:#dfe3e8;margin-right:6px}
form{display:flex;gap:8px;margin-top:12px}input{flex:1;padding:10px;border:1px solid #c9ced6;border-radius:6px;font-size:15px}
button{padding:10px 16px;border:0;border-radius:6px;background:#2457c5;color:#fff;font-size:15px}</style>
<main><h1>OrbitMesh Support Assistant</h1><p class="sub">Describe your OrbitMesh problem. Session <code id="sid"></code></p>
<div id="log"></div><form id="f"><input id="q" autocomplete="off" placeholder="My node keeps disconnecting" autofocus><button>Send</button></form></main>
<script>
const sid='web-'+Math.random().toString(36).slice(2,10);document.getElementById('sid').textContent=sid;
const log=document.getElementById('log');function add(c,t,meta){const d=document.createElement('div');d.className='m '+c;d.textContent=t;
if(meta){const m=document.createElement('div');m.className='meta';m.innerHTML=meta;d.appendChild(m)}log.appendChild(d);log.scrollTop=1e9}
document.getElementById('f').onsubmit=async e=>{e.preventDefault();const q=document.getElementById('q');const t=q.value.trim();if(!t)return;q.value='';add('you',t);
const r=await fetch('/chat',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({session_id:sid,message:t})});
const j=await r.json();const c=(j.citations||[]).map(x=>x.source_id+' › '+x.locator).join(' · ');
add('bot',j.response,'<span class="act">'+j.action+'</span>'+(c?'sources: '+c:''))};
</script>"""


def create_app() -> FastAPI:
    settings = load_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="OrbitMesh Support Assistant", version=os.environ.get("APP_VERSION", "dev"))
    agent = build_agent(settings)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _PAGE

    @app.get("/health")
    def healthz() -> dict:
        return {"ok": True, "index_chunks": agent.retriever.size, "llm": settings.llm_provider,
                "model": settings.llm_model, "version": app.version}

    @app.get("/metrics")
    def metrics() -> PlainTextResponse:
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/chat")
    def chat(req: ChatRequest) -> dict:
        try:
            return agent.handle(req.session_id, req.message).as_jsonl()
        except Exception as exc:  # noqa: BLE001
            log.exception("turn failed")
            raise HTTPException(status_code=500, detail=type(exc).__name__) from exc

    return app
