"""
Inspect a managed agent session's events to diagnose why a run failed.

Usage:
  python3 inspect_session.py <session_id> [<session_id> ...]
  python3 inspect_session.py --today
      # fetch all sessions updated today (UTC) and inspect each
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not API_KEY:
    sys.exit("Error: ANTHROPIC_API_KEY environment variable is not set.")

BASE_URL = "https://api.anthropic.com/v1"
HEADERS = {
    "x-api-key": API_KEY,
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "managed-agents-2026-04-01",
    "content-type": "application/json",
}


def _request(method, path, params=None, body=None):
    url = f"{BASE_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {e.code} on {method} {path}: {detail}")


def list_sessions(limit=50):
    return _request("GET", "/sessions", params={"limit": limit, "order": "desc"}).get("data", [])


def get_session(session_id):
    return _request("GET", f"/sessions/{session_id}")


def list_events(session_id, limit=100):
    return _request(
        "GET",
        f"/sessions/{session_id}/events",
        params={"limit": limit, "order": "asc"},
    ).get("data", [])


def short(text, n=300):
    if text is None:
        return ""
    text = str(text)
    if len(text) <= n:
        return text
    return text[:n] + f"... [+{len(text) - n} chars]"


def summarize_event(ev):
    etype = ev.get("type", "?")
    ts = (ev.get("created_at") or "")[:19]
    parts = [f"{ts}  {etype}"]

    content = ev.get("content") or []
    if isinstance(content, list):
        for block in content:
            btype = block.get("type", "?")
            if btype == "text":
                parts.append(f"    text: {short(block.get('text'))}")
            elif btype == "tool_use":
                name = block.get("name")
                inp = block.get("input") or {}
                parts.append(f"    tool_use: {name}  input={short(json.dumps(inp, ensure_ascii=False), 200)}")
            elif btype == "tool_result":
                is_err = block.get("is_error")
                result = block.get("content")
                if isinstance(result, list):
                    result = "".join(b.get("text", "") for b in result if isinstance(b, dict))
                tag = "tool_result(ERROR)" if is_err else "tool_result"
                parts.append(f"    {tag}: {short(result, 400)}")
            else:
                parts.append(f"    {btype}: {short(json.dumps(block, ensure_ascii=False), 200)}")

    # surface top-level error field if present
    if ev.get("error"):
        parts.append(f"    EVENT.error: {short(json.dumps(ev['error'], ensure_ascii=False))}")
    if ev.get("stop_reason"):
        parts.append(f"    stop_reason: {ev['stop_reason']}")

    return "\n".join(parts)


def inspect(session_id):
    print("=" * 80)
    print(f"SESSION  {session_id}")
    print("=" * 80)
    s = get_session(session_id)
    print(f"  title:      {s.get('title')}")
    print(f"  status:     {s.get('status')}")
    print(f"  created_at: {s.get('created_at')}")
    print(f"  updated_at: {s.get('updated_at')}")
    md = s.get("metadata") or {}
    if md:
        print(f"  metadata:   {short(json.dumps(md, ensure_ascii=False), 500)}")
    for k in ("last_error", "error", "stop_reason", "termination_reason"):
        if s.get(k):
            print(f"  {k}: {short(json.dumps(s[k], ensure_ascii=False), 500)}")

    print("\n-- events (chronological) --")
    events = list_events(session_id, limit=200)
    print(f"  {len(events)} events\n")

    # Print each event with a focus on failures
    tool_errors = 0
    for ev in events:
        etype = ev.get("type", "")
        is_tool_err = False
        for block in ev.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                is_tool_err = True
        if is_tool_err:
            tool_errors += 1

        # Print every event for full trace
        print(summarize_event(ev))
        print()

    print(f"-- end of session {session_id}  (tool errors: {tool_errors}) --\n")


def sessions_updated_today():
    today = datetime.now(timezone.utc).date().isoformat()
    sessions = list_sessions(limit=50)
    return [s for s in sessions if (s.get("updated_at") or "").startswith(today)]


def main(argv):
    if not argv or argv[0] == "--today":
        targets = [s["id"] for s in sessions_updated_today()]
        if not targets:
            print("No sessions updated today.")
            return
        print(f"Inspecting {len(targets)} session(s) updated today: {targets}\n")
    else:
        targets = argv

    for sid in targets:
        try:
            inspect(sid)
        except SystemExit as e:
            print(f"[error] {sid}: {e}")


if __name__ == "__main__":
    main(sys.argv[1:])
