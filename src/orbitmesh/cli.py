"""Command-line entry points.

    orbitmesh ingest            index the corpus (idempotent; safe to re-run after edits)
    orbitmesh chat              interactive terminal conversation
    orbitmesh chat --jsonl      one JSON object in per line on stdin, one out per line on stdout
    orbitmesh serve             HTTP wrapper (see server.py)

Diagnostics go to stderr as JSON lines; stdout carries only the conversation.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid

from .config import load_settings
from .observability import configure_logging, log_event

log = logging.getLogger("orbitmesh.cli")


def cmd_ingest(args: argparse.Namespace) -> int:
    from .connectors import ConnectorStore
    from .embeddings import build_embedder
    from .sync import sync_all
    from .vectorstore import VectorStore

    s = load_settings()
    embedder = build_embedder(s.embedding_provider, s.embedding_model, str(s.model_cache_dir))
    store = VectorStore(url=s.qdrant_url, api_key=s.qdrant_api_key, path=s.qdrant_path,
                        collection=s.collection, embedder=embedder)
    connectors = ConnectorStore(s.connectors_dir, s.corpus_dir)
    report = sync_all(connectors, store, s.index_state_path)
    n_docs = sum(v["documents"] for v in report.connectors.values() if v.get("enabled"))
    n_conn = sum(1 for v in report.connectors.values() if v.get("enabled"))
    log_event(log, "ingest.done", location=store.location, embedder=embedder.name, documents=n_docs,
              **report.as_dict())
    print(f"Indexed {report.total} chunks from {n_docs} documents across {n_conn} connector(s) into {store.location} "
          f"(written={report.written}, stale deleted={report.deleted}, embedder={embedder.name})", file=sys.stderr)
    return 0


def _agent():
    from .agent import build_agent

    s = load_settings()
    return build_agent(s), s


def cmd_chat(args: argparse.Namespace) -> int:
    agent, settings = _agent()
    if args.jsonl:
        return _jsonl_loop(agent)
    return _repl(agent, settings, session_id=args.session or f"cli-{uuid.uuid4().hex[:8]}")


def _jsonl_loop(agent) -> int:
    out = sys.stdout
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            session_id = str(req.get("session_id") or "default")
            message = str(req.get("message") or "")
        except (json.JSONDecodeError, AttributeError):
            out.write(json.dumps({"response": "Malformed input line; expected a JSON object with session_id and message.",
                                  "citations": [], "action": "ask", "error": "bad_request"}) + "\n")
            out.flush()
            continue
        try:
            result = agent.handle(session_id, message)
            payload = result.as_jsonl()
        except Exception as exc:  # noqa: BLE001 - the transport must always answer
            log.exception("turn failed")
            payload = {"response": "Sorry - something went wrong on my side. Please try again, or contact OrbitMesh "
                                   "Support through the app if it keeps happening.",
                       "citations": [], "action": "escalate", "session_id": session_id, "error": type(exc).__name__}
        out.write(json.dumps(payload, ensure_ascii=False) + "\n")
        out.flush()
    return 0


def _repl(agent, settings, *, session_id: str) -> int:
    err = sys.stderr
    print(f"OrbitMesh Support Assistant  (model={settings.llm_provider}:{settings.llm_model}, session={session_id})",
          file=err)
    print("Describe the problem. Type /reset to start over, /state to see what I remember, /quit to exit.", file=err)
    while True:
        try:
            message = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(file=err)
            return 0
        if not message:
            continue
        if message in ("/quit", "/exit"):
            return 0
        if message == "/reset":
            agent.sessions.reset(session_id)
            print("(session cleared)", file=err)
            continue
        if message == "/state":
            print(json.dumps(agent.sessions.get(session_id).to_dict(), indent=2), file=err)
            continue
        try:
            result = agent.handle(session_id, message)
        except Exception as exc:  # noqa: BLE001
            log.exception("turn failed")
            print(f"[error] {type(exc).__name__}: {exc}", file=err)
            continue
        cites = "; ".join(f"{c['source_id']} > {c['locator']}" for c in result.citations)
        print(f"\nassistant [{result.action}]> {result.response}")
        if cites:
            print(f"  sources: {cites}")
        print()


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .server import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orbitmesh", description="OrbitMesh Support Assistant")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest", help="index the corpus into the vector database").set_defaults(fn=cmd_ingest)
    chat = sub.add_parser("chat", help="talk to the assistant")
    chat.add_argument("--jsonl", action="store_true", help="JSONL stdin/stdout transport")
    chat.add_argument("--session", help="session id for the interactive REPL")
    chat.set_defaults(fn=cmd_chat)
    serve = sub.add_parser("serve", help="HTTP wrapper: POST /chat, GET /health, GET /metrics")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)
    serve.set_defaults(fn=cmd_serve)
    args = parser.parse_args(argv)
    configure_logging(load_settings().log_level)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
