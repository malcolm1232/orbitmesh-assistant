#!/usr/bin/env python3
"""Transport-contract checker for the OrbitMesh chat adapter (JSONL only).

Verifies that ``./scripts/chat.sh --jsonl`` (or another adapter command) speaks
the newline-delimited JSON protocol: one JSON object per input line, required
fields, valid actions, and session continuity. It does not judge answer quality.
"""

from __future__ import annotations

import argparse
import json
import queue
import shlex
import subprocess
import sys
import threading
from typing import Any

VALID_ACTIONS = {"ask", "instruct", "resolved", "escalate"}
PROBES = [
    {"session_id": "contract-a", "message": "Hello, my OrbitMesh node has a problem."},
    {"session_id": "contract-a", "message": "It is the node in my office."},
    {"session_id": "contract-b", "message": "My router will not power on."},
]

_EOF = object()


def add_required(checks: list[dict[str, str]], name: str, passed: bool) -> None:
    checks.append({"name": name, "status": "PASS" if passed else "FAIL"})


def add_warn(checks: list[dict[str, str]], name: str, passed: bool) -> None:
    checks.append({"name": name, "status": "PASS" if passed else "WARN"})


def reader(stream: Any, q: "queue.Queue[Any]") -> None:
    for line in stream:
        q.put(line)
    q.put(_EOF)


def read_line(q: "queue.Queue[Any]", timeout: float) -> Any:
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


def check_response(checks: list[dict[str, str]], label: str, line: str) -> bool:
    """Apply the per-response contract checks. Returns True if line parsed as an object."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        obj = None
    is_obj = isinstance(obj, dict)
    add_required(checks, f"{label}: single JSON object", is_obj)
    if not is_obj:
        return False

    response = obj.get("response")
    add_required(
        checks,
        f"{label}: response is a non-empty string",
        isinstance(response, str) and response.strip() != "",
    )
    add_required(
        checks,
        f"{label}: action is ask/instruct/resolved/escalate",
        obj.get("action") in VALID_ACTIONS,
    )

    if "citations" in obj:
        citations = obj["citations"]
        is_list = isinstance(citations, list)
        add_required(checks, f"{label}: citations is a list", is_list)
        if is_list:
            source_ok = all(
                isinstance(c, dict)
                and isinstance(c.get("source_id"), str)
                and c["source_id"].strip()
                for c in citations
            )
            add_required(checks, f"{label}: each citation has a string source_id", source_ok)
            locator_ok = all(
                isinstance(c, dict)
                and isinstance(c.get("locator"), str)
                and c["locator"].strip()
                for c in citations
            )
            add_warn(checks, f"{label}: each citation has a locator (recommended)", locator_ok)
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--command",
        default="./scripts/chat.sh --jsonl",
        help="chat command implementing the assignment's JSONL stdin/stdout contract",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="seconds to wait for each response line",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    process = subprocess.Popen(
        shlex.split(args.command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,  # pass adapter diagnostics through to the parent's stderr
        text=True,
        bufsize=1,
    )
    assert process.stdin is not None and process.stdout is not None

    q: "queue.Queue[Any]" = queue.Queue()
    thread = threading.Thread(target=reader, args=(process.stdout, q), daemon=True)
    thread.start()

    checks: list[dict[str, str]] = []
    all_json = True

    for index, probe in enumerate(PROBES, start=1):
        label = f"probe {index} ({probe['session_id']})"
        try:
            process.stdin.write(json.dumps(probe) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, ValueError):
            add_required(checks, f"{label}: adapter accepted the request", False)
            all_json = False
            break

        item = read_line(q, args.timeout)
        if item is None:
            add_required(
                checks,
                f"{label}: response within {args.timeout:g}s — is something printed to "
                "stdout before the first JSON line?",
                False,
            )
            all_json = False
            break
        if item is _EOF:
            add_required(checks, f"{label}: adapter still running (did not exit early)", False)
            all_json = False
            break

        parsed = check_response(checks, label, item)
        all_json = all_json and parsed

    # No stray or non-JSON output should have leaked onto stdout.
    extra_output = False
    while True:
        item = read_line(q, 0.2)
        if item is None or item is _EOF:
            break
        extra_output = True
    add_required(checks, "no extra or non-JSON output on stdout", all_json and not extra_output)

    # The adapter should shut down cleanly once stdin closes.
    try:
        process.stdin.close()
    except (BrokenPipeError, ValueError):
        pass
    try:
        process.wait(timeout=10)
        add_warn(checks, "adapter exits within 10s of stdin close", True)
    except subprocess.TimeoutExpired:
        process.terminate()
        add_warn(checks, "adapter exits within 10s of stdin close", False)

    width = max(len(check["name"]) for check in checks)
    print("Transport contract checks")
    print("-" * (width + 8))
    for check in checks:
        print(f"{check['status']:<4}  {check['name']}")

    failed = [check for check in checks if check["status"] == "FAIL"]
    warned = [check for check in checks if check["status"] == "WARN"]
    print("-" * (width + 8))
    print(f"{len(checks) - len(failed)}/{len(checks)} checks passed, {len(warned)} warning(s).")
    if failed:
        print("Contract check FAILED.")
        return 1
    print("Contract check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
