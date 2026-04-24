"""
Resume managed agent sessions that stalled after the OpenAI billing hard limit.

Sends a user.message to each session telling the agent the billing cap has been
lifted, so it can retry the `images` stage and complete the pipeline
(drive_upload -> xlsx -> Notion link).

Usage:
  python3 resume_billing_blocked_sessions.py                 # dry-run, prints targets
  python3 resume_billing_blocked_sessions.py --send          # actually post events
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

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

SESSIONS = [
    "sesn_011CaNC2jYWo3hgV9jZGRfLU",
    "sesn_011CaNBVknK3KksdAyF9UxQn",
    "sesn_011CaNAxqXTNuGpTWTYpGH34",
    "sesn_011CaNARwzXiHS7ypqsC7brP",
    "sesn_011CaNAD8yiN3ujR6qEjRk8B",
]

RESUME_MESSAGE = (
    "The OpenAI billing hard limit has been lifted. Please retry the `images` "
    "stage of the pipeline and continue through the remaining stages "
    "(image_recompose, drive_upload) so the final xlsx is uploaded to Drive "
    "and its link is posted to the Notion page. Do not re-run the stages that "
    "already completed in this session (vocabulary, template_plan, "
    "visual_briefs, meta_copy, primary_text, archetype_headlines) -- reuse "
    "their cached outputs. Report back with the Notion link once done, or "
    "stop and tell me if you hit any other blocker."
)


def _request(method, path, body=None):
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {e.code} on {method} {path}: {detail}")


def get_status(session_id):
    return _request("GET", f"/sessions/{session_id}").get("status")


def send_resume(session_id):
    return _request(
        "POST",
        f"/sessions/{session_id}/events",
        body={
            "events": [
                {
                    "type": "user.message",
                    "content": [{"type": "text", "text": RESUME_MESSAGE}],
                }
            ]
        },
    )


def main():
    send = "--send" in sys.argv[1:]

    print(f"Target sessions ({len(SESSIONS)}):")
    for sid in SESSIONS:
        status = get_status(sid)
        print(f"  - {sid}  status={status}")

    if not send:
        print("\nDry-run. Re-run with --send to actually post resume events.")
        return

    print("\nSending resume events...")
    for sid in SESSIONS:
        try:
            send_resume(sid)
            # small pause so we don't hammer the API
            time.sleep(1)
            new_status = get_status(sid)
            print(f"  sent -> {sid}  status={new_status}")
        except SystemExit as e:
            print(f"  FAIL  {sid}: {e}")


if __name__ == "__main__":
    main()
