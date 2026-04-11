"""
Retrieve the last Claude Managed Agent product research session and extract
the competitive_analysis.md output.

Uses the Anthropic Managed Agents API (beta) to:
1. List recent sessions and find the latest product-research-run
2. Resume the session and ask the agent to output the MD file
3. Save the result locally

Requires: ANTHROPIC_API_KEY environment variable
"""

import os
import sys
import time
import json
import httpx

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


def list_sessions(limit=20):
    """List recent sessions, newest first."""
    resp = httpx.get(
        f"{BASE_URL}/sessions",
        headers=HEADERS,
        params={"limit": limit, "order": "desc"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"]


def get_session_status(session_id):
    resp = httpx.get(
        f"{BASE_URL}/sessions/{session_id}",
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["status"]


def send_message(session_id, text):
    """Send a user message event to a session."""
    resp = httpx.post(
        f"{BASE_URL}/sessions/{session_id}/events",
        headers=HEADERS,
        json={
            "events": [
                {
                    "type": "user.message",
                    "content": [{"type": "text", "text": text}],
                }
            ]
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def wait_for_idle(session_id, timeout_s=180):
    """Poll until session goes idle or timeout."""
    for attempt in range(timeout_s // 2):
        time.sleep(2)
        status = get_session_status(session_id)
        if attempt % 5 == 0:
            print(f"  Status: {status}")
        if status == "idle":
            return True
    return False


def get_latest_events(session_id, limit=100):
    resp = httpx.get(
        f"{BASE_URL}/sessions/{session_id}/events",
        headers=HEADERS,
        params={"limit": limit, "order": "desc"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"]


def find_research_session(sessions):
    """Find the most recent session with 'research' or 'product' in the title."""
    for s in sessions:
        title = (s.get("title") or "").lower()
        meta_str = str(s.get("metadata", {})).lower()
        if any(kw in title or kw in meta_str for kw in ["research", "product"]):
            return s
    return None


def extract_md_from_events(events, filename_hint="competitive_analysis"):
    """Search events for large MD content matching the filename hint."""
    for event in events:
        etype = event.get("type", "")
        if etype not in ("agent.message", "agent.tool_result"):
            continue
        for block in event.get("content", []):
            text = block.get("text", "")
            if len(text) > 3000 and (
                f"# Competitive Analysis" in text
                or (filename_hint in text.lower() and "#" in text[:200])
            ):
                return text
    return None


def main():
    # Step 1: Find the session
    print("Listing recent sessions...")
    sessions = list_sessions()
    if not sessions:
        sys.exit("No sessions found.")

    print(f"Found {len(sessions)} session(s):")
    for i, s in enumerate(sessions[:10]):
        title = s.get("title") or "(untitled)"
        created = s.get("created_at", "")[:19]
        print(f"  [{i}] {title}  ({created})  {s['id']}")

    session = find_research_session(sessions)
    if not session:
        session = sessions[0]
        print(f"\nNo research session found, using most recent: {session.get('title')}")
    else:
        print(f"\nUsing: {session.get('title')} ({session['id']})")

    session_id = session["id"]

    # Step 2: Check if agent already output the file in prior events
    print("\nChecking existing events for the MD file...")
    events = get_latest_events(session_id)
    md_content = extract_md_from_events(events)

    # Step 3: If not found, resume session and ask agent to output it
    if not md_content:
        status = get_session_status(session_id)
        if status != "idle":
            print(f"Session is {status}, waiting...")
            if not wait_for_idle(session_id):
                sys.exit(f"Session did not become idle.")

        print("Asking agent to output the file...")
        send_message(
            session_id,
            "Read /mnt/session/outputs/competitive_analysis.md and output its FULL "
            "contents in a single message. Do not summarize or truncate.",
        )

        print("Waiting for response...")
        if not wait_for_idle(session_id):
            sys.exit("Timed out waiting for agent response.")

        events = get_latest_events(session_id)
        md_content = extract_md_from_events(events)

    if not md_content:
        sys.exit("Could not extract the MD file content from session events.")

    # Clean line-number prefixes from read tool output (N\tcontent format)
    lines = md_content.split("\n")
    cleaned = []
    for line in lines:
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[0].strip().isdigit():
            cleaned.append(parts[1])
        else:
            cleaned.append(line)
    md_content = "\n".join(cleaned).strip()

    output_file = "competitive_analysis.md"
    with open(output_file, "w") as f:
        f.write(md_content + "\n")

    print(f"\nSaved: {output_file} ({len(md_content):,} chars)")


if __name__ == "__main__":
    main()
