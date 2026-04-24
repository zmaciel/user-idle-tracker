"""
Inspect the latest Claude Managed Agent sessions and flag any that appear stuck.

A session is treated as "potentially stuck" if any of the following hold:
  * status is "running" / "in_progress" but no new events for STALE_RUNNING_S
  * status is "waiting_for_input" for longer than STALE_WAITING_S
  * status is "error" / "failed" (not stuck, but worth surfacing)

Requires: ANTHROPIC_API_KEY environment variable.
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

STALE_RUNNING_S = 15 * 60
STALE_WAITING_S = 60 * 60
ACTIVE_STATES = {"running", "in_progress", "processing", "busy", "active"}
WAITING_STATES = {"waiting_for_input", "awaiting_input", "paused"}
TERMINAL_ERROR_STATES = {"error", "failed", "crashed"}


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


def list_sessions(limit=20):
    return _request("GET", "/sessions", params={"limit": limit, "order": "desc"}).get("data", [])


def get_session(session_id):
    return _request("GET", f"/sessions/{session_id}")


def list_events(session_id, limit=5):
    return _request(
        "GET",
        f"/sessions/{session_id}/events",
        params={"limit": limit, "order": "desc"},
    ).get("data", [])


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def seconds_since(ts):
    dt = parse_ts(ts)
    if not dt:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds()


def human_age(seconds):
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def diagnose(session, last_event_age_s):
    status = (session.get("status") or "").lower()
    updated_age = seconds_since(session.get("updated_at"))

    if status in TERMINAL_ERROR_STATES:
        return "ERROR", f"status={status}"

    if status in ACTIVE_STATES:
        age = last_event_age_s if last_event_age_s is not None else updated_age
        if age is None:
            return "UNKNOWN", f"status={status}, no timestamps"
        if age > STALE_RUNNING_S:
            return "STUCK", f"running {human_age(age)} without new events"
        return "OK", f"running ({human_age(age)} since last event)"

    if status in WAITING_STATES:
        age = updated_age
        if age and age > STALE_WAITING_S:
            return "STUCK", f"waiting for input for {human_age(age)}"
        return "OK", f"waiting for input ({human_age(age)})"

    if status == "idle":
        return "OK", "idle"

    return "UNKNOWN", f"status={status!r}"


def main():
    print("Listing recent sessions...\n")
    sessions = list_sessions(limit=20)
    if not sessions:
        print("No sessions found.")
        return

    stuck = []
    for i, s in enumerate(sessions):
        sid = s["id"]
        title = s.get("title") or "(untitled)"
        status = s.get("status") or "?"
        created = (s.get("created_at") or "")[:19]
        updated = (s.get("updated_at") or "")[:19]

        # Pull latest event timestamp for freshness signal.
        last_event_age_s = None
        try:
            events = list_events(sid, limit=1)
            if events:
                last_event_age_s = seconds_since(events[0].get("created_at"))
        except SystemExit:
            # Swallow per-session errors so one bad id doesn't kill the report.
            pass

        verdict, detail = diagnose(s, last_event_age_s)
        marker = {"OK": " ", "STUCK": "!", "ERROR": "X", "UNKNOWN": "?"}[verdict]

        print(f"[{marker}] {i:>2}. {sid}")
        print(f"       title:   {title}")
        print(f"       status:  {status}  ({detail})")
        print(f"       created: {created}   updated: {updated}")
        if last_event_age_s is not None:
            print(f"       last event: {human_age(last_event_age_s)} ago")
        print()

        if verdict in ("STUCK", "ERROR"):
            stuck.append((sid, title, verdict, detail))

    print("=" * 60)
    if stuck:
        print(f"{len(stuck)} session(s) flagged:")
        for sid, title, verdict, detail in stuck:
            print(f"  [{verdict}] {sid}  {title} -- {detail}")
    else:
        print("No sessions appear stuck.")


if __name__ == "__main__":
    main()
