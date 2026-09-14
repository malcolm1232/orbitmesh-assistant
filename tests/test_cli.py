"""Interactive `make chat`: what a person at the terminal sees."""
import io
import logging
import sys
from types import SimpleNamespace

from orbitmesh import cli
from orbitmesh.observability import configure_logging


class _FakeAgent:
    def __init__(self):
        self.messages = []
        self.sessions = SimpleNamespace(reset=lambda sid: None, get=lambda sid: SimpleNamespace(to_dict=lambda: {}))

    def handle(self, session_id, message):
        self.messages.append(message)
        logging.getLogger("orbitmesh.agent").info("turn", extra={"data": {"turn": len(self.messages)}})
        logging.getLogger("httpx2").info("HTTP Request: POST https://openrouter.ai/api/v1/chat/completions")
        return SimpleNamespace(action="ask", response="Which system do you have?", citations=[])


def _run(monkeypatch, tmp_path, lines):
    inputs = iter(lines)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    configure_logging("INFO")                                  # after the swap, as `orbitmesh chat` does at start-up
    settings = SimpleNamespace(llm_provider="mock", llm_model="m", log_level="INFO", session_dir=tmp_path / "sessions")
    agent = _FakeAgent()
    code = cli._repl(agent, settings, session_id="cli-test")
    return code, agent, out.getvalue(), err.getvalue()


def test_the_terminal_conversation_is_not_interleaved_with_json_logs(monkeypatch, tmp_path):
    code, agent, out, err = _run(monkeypatch, tmp_path, ["my node keeps dropping", "/quit"])
    assert code == 0 and agent.messages == ["my node keeps dropping"]
    assert "Which system do you have?" in out
    assert '{"ts"' not in out + err                       # structured logs are not printed at the person
    log_file = tmp_path / "logs" / "chat.log"
    assert log_file.exists() and '"event": "turn"' in log_file.read_text()   # ...but they are still kept


def test_bare_quit_and_exit_end_the_session_instead_of_being_sent_as_a_message(monkeypatch, tmp_path):
    for word in ("quit", "exit", "Quit"):
        code, agent, _, _ = _run(monkeypatch, tmp_path, [word])
        assert code == 0 and agent.messages == []
