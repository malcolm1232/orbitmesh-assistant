"""The transport contract, end to end through the real scripts, in the no-credentials mode."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_check_contract_passes_in_mock_mode(tmp_path):
    env = {**os.environ, "LLM_PROVIDER": "mock", "EMBEDDING_PROVIDER": "hash", "QDRANT_URL": "",
           "QDRANT_PATH": str(tmp_path / "q"), "SESSION_DIR": str(tmp_path / "s"), "PYTHON": sys.executable}
    ingest = subprocess.run([str(ROOT / "scripts" / "ingest.sh")], env=env, cwd=ROOT, capture_output=True, text=True)
    assert ingest.returncode == 0, ingest.stderr
    check = subprocess.run([sys.executable, "scripts/check_contract.py", "--timeout", "60"], env=env, cwd=ROOT,
                           capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr
    assert "Contract check passed." in check.stdout


def test_stdout_is_pure_jsonl_even_for_bad_input(tmp_path):
    env = {**os.environ, "LLM_PROVIDER": "mock", "EMBEDDING_PROVIDER": "hash", "QDRANT_URL": "",
           "QDRANT_PATH": str(tmp_path / "q"), "SESSION_DIR": str(tmp_path / "s"), "PYTHON": sys.executable}
    subprocess.run([str(ROOT / "scripts" / "ingest.sh")], env=env, cwd=ROOT, check=True, capture_output=True)
    proc = subprocess.run([str(ROOT / "scripts" / "chat.sh"), "--jsonl"], env=env, cwd=ROOT, capture_output=True,
                          text=True, input='not json\n{"session_id":"x","message":"hi"}\n\n')
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    assert len(lines) == 2
    import json
    assert json.loads(lines[0])["error"] == "bad_request"
    assert json.loads(lines[1])["action"] in {"ask", "instruct", "resolved", "escalate"}
