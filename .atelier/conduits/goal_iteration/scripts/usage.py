#!/usr/bin/env python3
"""Read remaining usage for the two agents goal_iteration runs and compare it with floors.

    python3 usage.py MIN_CLAUDE_FABLE_REMAINING MIN_CLAUDE_5H_REMAINING MIN_CODEX_REMAINING
    All arguments are whole percentages, 1-100.

Every limit is a floor on the remaining allowance of its meter, 100 minus the used percent the
provider reports (below the floor pauses; exactly the floor still runs). Prints one readable line
per meter and a final USAGE_READY (every meter at or above its floor) or USAGE_PAUSE (a meter
below its floor, or unreadable). Anything missing, malformed, stale
or ambiguous counts as unreadable and pauses; usage is never assumed to be 0. Exit code is 0
either way; only a bug in this file exits non-zero. Nothing secret is printed: a reason is a
short fixed string or an exception class name, never a response body, a token or a message.

Claude: GET https://api.anthropic.com/api/oauth/usage with the subscription OAuth token Claude
Code itself uses: CLAUDE_CODE_OAUTH_TOKEN if set; else, on macOS with the default config dir,
the Keychain item "Claude Code-credentials" and, only when that item is absent,
~/.claude/.credentials.json; else <CLAUDE_CONFIG_DIR or ~/.claude>/.credentials.json. With a
custom CLAUDE_CONFIG_DIR only that directory's file is read, never the default Keychain item of
a possibly different account. Redirects are refused so the token is never forwarded. The
endpoint is what the installed Claude CLI uses, not a stable public API: a schema or auth change
pauses the loop until this file is updated. The Fable meter is the `limits` entry scoped to the
model with display_name "Fable" (the model-specific weekly limit,
https://support.claude.com/en/articles/15424964-claude-fable-models-on-your-plan); the all-model
seven_day total is deliberately never used. five_hour.utilization is the 5-hour meter.

Codex: JSONL RPC on a private `codex app-server --listen stdio://` process (initialize,
initialized, account/rateLimits/read, see
https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt) through the existing codex
login; no thread or turn is started and the process is stopped before returning. Only the weekly
window (10080 minutes) of the `codex` bucket counts; Spark or base-model buckets are ignored.
"""

import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

CLAUDE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_CMD = ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"]
CODEX_CMD = ["codex", "app-server", "--listen", "stdio://"]
MAC = sys.platform == "darwin"
# With the keychain read, RPC budget and cleanup, one check takes about 30 seconds at most.
HTTP_TIMEOUT = 10
RPC_TIMEOUT = 10
WEEK_MINS = 10080
METERS = ("claude fable weekly", "claude 5h", "codex weekly")


class Unavailable(Exception):
    """Usage could not be read; str(e) is a short reason that is safe to print."""


def percent(value, reset_epoch, now):
    """A consumed percentage: numeric, 0-100, from a window that has not reset yet.
    Only an unstarted window (0 with no reset time) may lack a reset time."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
        raise Unavailable("bad percent")
    if reset_epoch is None:
        if value != 0:
            raise Unavailable("no reset time")
    elif (
        isinstance(reset_epoch, bool)
        or not isinstance(reset_epoch, (int, float))
        or not math.isfinite(reset_epoch)
    ):
        raise Unavailable("bad reset time")
    elif reset_epoch <= now:
        raise Unavailable("stale window")
    return value


def iso_epoch(text):
    if text is None:
        return None
    stamp = datetime.fromisoformat(text)  # anything that is not an ISO string raises and pauses
    if stamp.tzinfo is None:
        raise Unavailable("bad reset time")
    return stamp.timestamp()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, *args
    ):  # following one would forward the bearer token; it becomes an http error instead
        return None


def claude_token():
    """The OAuth access token Claude Code itself uses. Never printed."""
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if token:
        return token
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    raw = ""
    if MAC and not config_dir:
        try:
            raw = subprocess.run(KEYCHAIN_CMD, capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            raw = ""
    if (
        not raw.strip()
    ):  # no Keychain item: the same config dir's credentials file, nothing else is searched
        try:
            raw = Path(config_dir or Path.home() / ".claude", ".credentials.json").read_text()
        except OSError:
            raw = ""
    try:
        token = json.loads(raw)["claudeAiOauth"]["accessToken"]
    except (ValueError, KeyError, TypeError):
        token = None
    if not isinstance(token, str) or not token:
        raise Unavailable(
            "no Claude login found; log in with claude or export CLAUDE_CODE_OAUTH_TOKEN"
        )
    return token


def claude_fetch():
    headers = {
        "Authorization": "Bearer " + claude_token(),
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "automatic-goal-usage-check",
    }
    request = urllib.request.Request(CLAUDE_URL, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect).open(
            request, timeout=HTTP_TIMEOUT
        ) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise Unavailable(f"http {error.code}") from None
    except urllib.error.URLError:
        raise Unavailable("network error") from None


def claude_meters(data, now):
    """(Fable weekly used %, 5-hour used %). Exactly one weekly limit scoped to the Fable model
    must exist."""
    fable = [
        entry
        for entry in data["limits"]
        if entry.get("kind") == "weekly_scoped"
        and entry.get("group") == "weekly"
        and ((entry.get("scope") or {}).get("model") or {}).get("display_name") == "Fable"
    ]
    if len(fable) != 1:
        raise Unavailable("no single Fable weekly meter")
    five = data["five_hour"]
    return (
        percent(fable[0].get("percent"), iso_epoch(fable[0].get("resets_at")), now),
        percent(five.get("utilization"), iso_epoch(five.get("resets_at")), now),
    )


def send(proc, message):
    try:
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
    except OSError:
        raise Unavailable("codex exited") from None


def reply(lines, wanted, until):
    """Read the matching response, skipping notifications, other IDs and non-JSON lines."""
    while True:
        try:
            line = lines.get(timeout=max(0.0, until - time.monotonic()))
        except queue.Empty:
            raise Unavailable("codex rpc timeout") from None
        if line is None:
            raise Unavailable("codex exited")
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("id") == wanted:
            if "error" in message or "result" not in message:
                raise Unavailable("codex rpc error")
            return message["result"]


def codex_fetch():
    """Read account/rateLimits/read from a private app-server and stop it afterwards."""
    try:
        proc = subprocess.Popen(
            CODEX_CMD,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        raise Unavailable("codex not found") from None
    lines = queue.Queue()
    reader = threading.Thread(
        target=lambda: ([lines.put(line) for line in proc.stdout], lines.put(None)), daemon=True
    )
    reader.start()
    until = time.monotonic() + RPC_TIMEOUT
    try:
        send(
            proc,
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "automatic_goal_usage", "version": "1.0"}},
            },
        )
        reply(lines, 1, until)
        send(proc, {"method": "initialized"})
        send(proc, {"id": 2, "method": "account/rateLimits/read"})
        return reply(lines, 2, until)
    finally:  # only the process spawned above is touched
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        reader.join(timeout=1)
        if not reader.is_alive():
            proc.stdout.close()


def codex_weekly(result, now):
    """usedPercent of the weekly window of the `codex` bucket."""
    buckets = result.get("rateLimitsByLimitId")
    if (
        buckets is None
    ):  # legacy single bucket, only when the map is absent and the bucket is the codex one
        bucket = result.get("rateLimits")
        if isinstance(bucket, dict) and bucket.get("limitId") not in (None, "codex"):
            bucket = None
    elif isinstance(
        buckets, dict
    ):  # modern map: never read another bucket, never fall back to the legacy field
        bucket = buckets.get("codex")
    else:
        bucket = None
    if not isinstance(bucket, dict):
        raise Unavailable("no codex rate limits")
    weekly = [
        window
        for window in (bucket.get("primary"), bucket.get("secondary"))
        if isinstance(window, dict) and window.get("windowDurationMins") == WEEK_MINS
    ]
    if len(weekly) != 1:
        raise Unavailable("no single weekly window")
    return percent(weekly[0].get("usedPercent"), weekly[0].get("resetsAt"), now)


def reason(error):
    return str(error) if isinstance(error, Unavailable) else type(error).__name__


def now():
    return time.time()


def read_usage():
    """{meter: (used percent or None, reason)}; never raises. The clock is sampled after each
    response so a window that reset during the read is seen as stale, not fresh."""
    rows = {}
    try:
        fable, five = claude_meters(claude_fetch(), now())
        rows.update({METERS[0]: (fable, ""), METERS[1]: (five, "")})
    except Exception as error:
        rows.update({METERS[0]: (None, reason(error)), METERS[1]: (None, reason(error))})
    try:
        rows[METERS[2]] = (codex_weekly(codex_fetch(), now()), "")
    except Exception as error:
        rows[METERS[2]] = (None, reason(error))
    return rows


def main(argv):
    if len(argv) != 4:
        return (
            "usage: usage.py MIN_CLAUDE_FABLE_REMAINING MIN_CLAUDE_5H_REMAINING MIN_CODEX_REMAINING"
        )
    floors = dict(
        zip(METERS, (int(arg) for arg in argv[1:]), strict=True)
    )  # ranges are validated by the conduit
    ready = True
    for meter, (used, why) in read_usage().items():
        floor = floors[meter]  # floor on what is left; exactly the floor still runs
        left = None if used is None else 100 - used
        allowed = left is not None and left >= floor
        shown = f"unavailable ({why})" if left is None else f"{left:g}% remaining"
        line = f"{meter}: {shown} (pause below {floor}% remaining)"
        print(line + ("" if allowed else " -> pause"))
        ready = ready and allowed
    print("USAGE_READY" if ready else "USAGE_PAUSE")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
