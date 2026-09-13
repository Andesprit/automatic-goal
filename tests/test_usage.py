"""scripts/usage.py against synthetic fixtures, a local HTTP server and a fake app-server process.

No real account is ever queried: the module constants are patched here (test-only), there is no
production bypass. Run with the same interpreter as test_conduits.py.
"""

import http.server
import importlib.util
import io
import json
import math
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".atelier"
    / "conduits"
    / "goal_iteration"
    / "scripts"
    / "usage.py"
)
spec = importlib.util.spec_from_file_location("usage", SCRIPT)
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)

NOW = 1_800_000_000.0  # 2027; fixtures reset in 2099
TOKEN = "sk-ant-oat01-test-token-never-printed"
CLAUDE = {
    "five_hour": {"utilization": 10.0, "resets_at": "2099-01-01T01:00:00+00:00"},
    "seven_day": {"utilization": 8.0, "resets_at": "2099-01-07T01:00:00+00:00"},
    "limits": [
        {
            "kind": "session",
            "group": "session",
            "percent": 10,
            "resets_at": "2099-01-01T01:00:00+00:00",
            "scope": None,
            "is_active": False,
        },
        {
            "kind": "weekly_all",
            "group": "weekly",
            "percent": 8,
            "resets_at": "2099-01-07T01:00:00+00:00",
            "scope": None,
            "is_active": False,
        },
        {
            "kind": "weekly_scoped",
            "group": "weekly",
            "percent": 16,
            "resets_at": "2099-01-07T01:00:00+00:00",
            "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None},
            "is_active": True,
        },
    ],
}
CODEX = {
    "rateLimitsByLimitId": {
        "codex": {
            "primary": {"usedPercent": 41, "windowDurationMins": 10080, "resetsAt": 4070908800},
            "secondary": None,
        },
        "codex_bengalfox": {
            "primary": {"usedPercent": 0, "windowDurationMins": 300, "resetsAt": 4070908800},
            "secondary": {"usedPercent": 0, "windowDurationMins": 10080, "resetsAt": 4070908800},
        },
    }
}


def claude(**changes):
    data = json.loads(json.dumps(CLAUDE))
    for key, value in changes.items():
        data["limits"][2][key] = value
    return data


def window(percent=41, mins=10080, reset=4070908800):
    return {"usedPercent": percent, "windowDurationMins": mins, "resetsAt": reset}


def run_main(*limits):
    out = io.StringIO()
    with redirect_stdout(out):
        code = usage.main(["usage.py", *limits])
    return code, out.getvalue()


class Percent(unittest.TestCase):
    def test_accepts_numeric_0_to_100_with_future_reset(self):
        self.assertEqual(usage.percent(0, NOW + 1, NOW), 0)
        self.assertEqual(usage.percent(100, NOW + 1, NOW), 100)
        self.assertEqual(usage.percent(41.5, NOW + 1, NOW), 41.5)
        self.assertEqual(usage.percent(0, None, NOW), 0)  # unstarted window

    def test_rejects_bool_nan_string_none_and_out_of_range(self):
        for bad in [True, False, math.nan, "16", None, -1, 100.1, 101, [16]]:
            with self.assertRaisesRegex(usage.Unavailable, "bad percent", msg=repr(bad)):
                usage.percent(bad, NOW + 1, NOW)

    def test_rejects_stale_missing_or_bad_reset(self):
        with self.assertRaisesRegex(usage.Unavailable, "stale"):
            usage.percent(5, NOW, NOW)
        with self.assertRaisesRegex(usage.Unavailable, "no reset time"):
            usage.percent(5, None, NOW)
        for bad in [True, "2099-01-01", [], math.nan, math.inf, -math.inf]:
            with self.assertRaisesRegex(usage.Unavailable, "bad reset time"):
                usage.percent(5, bad, NOW)


class ClaudeParsing(unittest.TestCase):
    def test_uses_fable_scoped_meter_and_five_hour_not_seven_day(self):
        self.assertEqual(usage.claude_meters(CLAUDE, NOW), (16, 10.0))
        data = claude(percent=60)
        data["seven_day"]["utilization"] = 8
        data["limits"][1]["percent"] = 8
        self.assertEqual(usage.claude_meters(data, NOW)[0], 60)
        data = claude(percent=8)
        data["seven_day"]["utilization"] = 60
        data["limits"][1]["percent"] = 60
        self.assertEqual(usage.claude_meters(data, NOW)[0], 8)

    def test_is_active_is_ignored(self):
        self.assertEqual(usage.claude_meters(claude(is_active=False), NOW)[0], 16)

    def test_missing_ambiguous_or_other_model_fable_meter_pauses(self):
        data = claude()
        del data["limits"][2]
        for bad in [
            data,
            claude(scope={"model": {"id": None, "display_name": "Opus"}, "surface": None}),
            claude(scope=None),
            claude(scope={"model": None}),
            claude(group="session"),
            claude(kind="weekly_all"),
        ]:
            with self.assertRaisesRegex(usage.Unavailable, "Fable"):
                usage.claude_meters(bad, NOW)
        data = claude()
        data["limits"].append(dict(data["limits"][2]))
        with self.assertRaisesRegex(usage.Unavailable, "Fable"):
            usage.claude_meters(data, NOW)

    def test_stale_or_malformed_values_pause(self):
        with self.assertRaisesRegex(usage.Unavailable, "stale"):
            usage.claude_meters(claude(resets_at="2000-01-01T00:00:00Z"), NOW)
        with self.assertRaisesRegex(usage.Unavailable, "no reset time"):
            usage.claude_meters(claude(resets_at=None), NOW)
        self.assertEqual(usage.claude_meters(claude(percent=0, resets_at=None), NOW)[0], 0)
        with self.assertRaisesRegex(usage.Unavailable, "bad reset time"):
            usage.claude_meters(claude(resets_at="2099-01-01T00:00:00"), NOW)  # naive
        with self.assertRaisesRegex(usage.Unavailable, "bad percent"):
            usage.claude_meters(claude(percent="16"), NOW)
        data = claude()
        data["five_hour"]["utilization"] = None
        with self.assertRaisesRegex(usage.Unavailable, "bad percent"):
            usage.claude_meters(data, NOW)
        for broken in [
            {},
            {"limits": "x", "five_hour": {}},
            {"limits": [1], "five_hour": {}},
            {"limits": [], "five_hour": None},
        ]:
            with self.assertRaises((KeyError, AttributeError, usage.Unavailable)):
                usage.claude_meters(broken, NOW)


class ClaudeAuth(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.marker = self.tmp / "keychain-called"
        keychain = [
            sys.executable,
            "-c",
            f"import pathlib; pathlib.Path({str(self.marker)!r}).touch(); "
            'print(\'{"claudeAiOauth": {"accessToken": "from-keychain"}}\')',
        ]
        home = mock.patch.object(Path, "home", return_value=self.tmp)
        home.start()
        self.addCleanup(home.stop)
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        for var in ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR"):
            os.environ.pop(var, None)
        for name, value in [("MAC", True), ("KEYCHAIN_CMD", keychain)]:
            patcher = mock.patch.object(usage, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def creds(self, directory, token="from-file"):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"accessToken": token}})
        )

    def test_explicit_env_token_wins(self):
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = TOKEN
        self.assertEqual(usage.claude_token(), TOKEN)
        self.assertFalse(self.marker.exists())

    def test_mac_default_uses_keychain(self):
        self.creds(self.tmp / ".claude")
        self.assertEqual(usage.claude_token(), "from-keychain")
        self.assertTrue(self.marker.exists())

    def test_custom_config_dir_reads_only_that_dir_never_the_keychain(self):
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.tmp / "other")
        with self.assertRaisesRegex(usage.Unavailable, "CLAUDE_CODE_OAUTH_TOKEN"):
            usage.claude_token()
        self.creds(self.tmp / "other", "other-account")
        self.assertEqual(usage.claude_token(), "other-account")
        self.assertFalse(self.marker.exists())

    def test_linux_reads_default_credentials_file(self):
        with mock.patch.object(usage, "MAC", False):
            with self.assertRaisesRegex(usage.Unavailable, "no Claude login"):
                usage.claude_token()
            self.creds(self.tmp / ".claude")
            self.assertEqual(usage.claude_token(), "from-file")
        self.assertFalse(self.marker.exists())

    def test_mac_falls_back_to_the_default_file_only_when_the_keychain_item_is_absent(self):
        self.creds(self.tmp / ".claude")
        for absent in [
            [sys.executable, "-c", "raise SystemExit(44)"],
            [sys.executable, "-c", "print()"],
            ["/nonexistent/security"],
        ]:
            with mock.patch.object(usage, "KEYCHAIN_CMD", absent):
                self.assertEqual(usage.claude_token(), "from-file")
        with (
            mock.patch.object(usage, "KEYCHAIN_CMD", [sys.executable, "-c", "print('garbage')"]),
            self.assertRaisesRegex(usage.Unavailable, "no Claude login"),
        ):
            usage.claude_token()

    def test_keychain_failure_or_bad_json_pauses(self):
        for cmd in [
            [sys.executable, "-c", "raise SystemExit(44)"],
            [sys.executable, "-c", "print('{}')"],
            [sys.executable, "-c", 'print(\'{"claudeAiOauth": {"accessToken": ""}}\')'],
            ["/nonexistent/security"],
        ]:
            with (
                mock.patch.object(usage, "KEYCHAIN_CMD", cmd),
                self.assertRaisesRegex(usage.Unavailable, "no Claude login"),
            ):
                usage.claude_token()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
        status, body, headers = self.server.script.get(self.path, (404, b"", {}))
        if callable(body):
            body = body()
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ClaudeHttp(unittest.TestCase):
    def setUp(self):
        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.server.requests, self.server.script = [], {}
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]
        for name, value in [
            ("CLAUDE_URL", f"http://127.0.0.1:{self.port}/usage"),
            ("HTTP_TIMEOUT", 2),
            ("codex_fetch", lambda: CODEX),
        ]:  # read_usage must never start a real app-server here
            patcher = mock.patch.object(usage, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.env = mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_sends_bearer_and_headers_and_parses(self):
        self.server.script["/usage"] = (200, json.dumps(CLAUDE).encode(), {})
        self.assertEqual(usage.claude_fetch(), CLAUDE)
        path, headers = self.server.requests[0]
        self.assertEqual(headers["authorization"], "Bearer " + TOKEN)
        self.assertEqual(headers["anthropic-beta"], "oauth-2025-04-20")
        self.assertEqual(headers["user-agent"], "automatic-goal-usage-check")

    def test_auth_failure_pauses_without_printing_the_token(self):
        self.server.script["/usage"] = (401, b'{"error": "' + TOKEN.encode() + b'"}', {})
        with self.assertRaisesRegex(usage.Unavailable, "^http 401$"):
            usage.claude_fetch()
        code, out = run_main("50", "70", "50")
        self.assertEqual(code, 0)
        self.assertNotIn(TOKEN, out)
        self.assertIn(
            "claude fable weekly: unavailable (http 401) (pause below 50% remaining) -> pause", out
        )
        self.assertEqual(out.splitlines()[-1], "USAGE_PAUSE")

    def test_redirects_are_refused(self):
        self.server.script["/usage"] = (
            302,
            b"",
            {"Location": f"http://127.0.0.1:{self.port}/elsewhere"},
        )
        self.server.script["/elsewhere"] = (200, json.dumps(CLAUDE).encode(), {})
        with self.assertRaisesRegex(usage.Unavailable, "^http 302$"):
            usage.claude_fetch()
        self.assertEqual([path for path, _ in self.server.requests], ["/usage"])

    def test_bad_json_network_error_and_timeout_pause(self):
        self.server.script["/usage"] = (200, b"<html>not json</html>", {})
        rows = usage.read_usage()
        self.assertEqual(rows["claude fable weekly"], (None, "JSONDecodeError"))
        self.server.script["/usage"] = (200, lambda: time.sleep(1) or b"{}", {})
        with mock.patch.object(usage, "HTTP_TIMEOUT", 0.2):
            started = time.monotonic()
            self.assertIsNone(usage.read_usage()["claude 5h"][0])
            self.assertLess(time.monotonic() - started, 1.5)
        self.server.shutdown()
        self.server.server_close()
        with self.assertRaisesRegex(usage.Unavailable, "network error"):
            usage.claude_fetch()


class CodexParsing(unittest.TestCase):
    def test_selects_codex_bucket_weekly_window_not_spark(self):
        self.assertEqual(usage.codex_weekly(CODEX, NOW), 41)

    def test_weekly_may_be_secondary(self):
        result = {
            "rateLimitsByLimitId": {"codex": {"primary": window(3, 300), "secondary": window(33)}}
        }
        self.assertEqual(usage.codex_weekly(result, NOW), 33)

    def test_map_without_codex_entry_pauses_even_if_legacy_field_exists(self):
        result = {
            "rateLimitsByLimitId": {"codex_bengalfox": {"primary": window(0)}},
            "rateLimits": {"primary": window(12)},
        }
        with self.assertRaisesRegex(usage.Unavailable, "no codex rate limits"):
            usage.codex_weekly(result, NOW)

    def test_malformed_map_never_falls_back_to_legacy_but_a_null_map_does(self):
        for broken in [[], "codex", 7, {"codex": "x"}, {"codex": None}]:
            with self.assertRaisesRegex(
                usage.Unavailable, "no codex rate limits", msg=repr(broken)
            ):
                usage.codex_weekly(
                    {"rateLimitsByLimitId": broken, "rateLimits": {"primary": window(12)}}, NOW
                )
        self.assertEqual(
            usage.codex_weekly(
                {"rateLimitsByLimitId": None, "rateLimits": {"primary": window(12)}}, NOW
            ),
            12,
        )

    def test_legacy_single_bucket_only_when_it_is_codex(self):
        self.assertEqual(
            usage.codex_weekly(
                {"rateLimits": {"limitId": "codex", "primary": window(12), "secondary": None}}, NOW
            ),
            12,
        )
        self.assertEqual(
            usage.codex_weekly(
                {"rateLimits": {"primary": window(3, 300), "secondary": window(12)}}, NOW
            ),
            12,
        )
        with self.assertRaisesRegex(usage.Unavailable, "no codex rate limits"):
            usage.codex_weekly(
                {"rateLimits": {"limitId": "codex_bengalfox", "primary": window(12)}}, NOW
            )
        with self.assertRaisesRegex(usage.Unavailable, "no codex rate limits"):
            usage.codex_weekly({"credits": {"balance": 1}}, NOW)

    def test_missing_or_ambiguous_weekly_window_pauses(self):
        for bucket in [
            {"primary": window(3, 300), "secondary": None},
            {"primary": None, "secondary": None},
            {},
            {"primary": window(1), "secondary": window(2)},
            {"primary": "weekly"},
        ]:
            with self.assertRaisesRegex(usage.Unavailable, "no single weekly window"):
                usage.codex_weekly({"rateLimitsByLimitId": {"codex": bucket}}, NOW)

    def test_bad_or_stale_values_pause_and_null_is_not_zero(self):
        for bad in [None, True, math.nan, 101, "41"]:
            with self.assertRaisesRegex(usage.Unavailable, "bad percent"):
                usage.codex_weekly(
                    {"rateLimitsByLimitId": {"codex": {"primary": window(bad)}}}, NOW
                )
        with self.assertRaisesRegex(usage.Unavailable, "stale"):
            usage.codex_weekly(
                {"rateLimitsByLimitId": {"codex": {"primary": window(41, reset=NOW - 1)}}}, NOW
            )
        with self.assertRaisesRegex(usage.Unavailable, "no reset time"):
            usage.codex_weekly(
                {"rateLimitsByLimitId": {"codex": {"primary": window(41, reset=None)}}}, NOW
            )
        self.assertEqual(
            usage.codex_weekly(
                {"rateLimitsByLimitId": {"codex": {"primary": window(0, reset=None)}}}, NOW
            ),
            0,
        )
        with self.assertRaises(AttributeError):
            usage.codex_weekly(["not", "a", "dict"], NOW)


FAKE_APP_SERVER = """
import json, os, sys, time
mode, log, result = sys.argv[1], sys.argv[2], sys.argv[3]
def out(message):
    print(message if isinstance(message, str) else json.dumps(message))
    sys.stdout.flush()
with open(log, "a") as f:
    f.write("pid %d\\n" % os.getpid())
for line in sys.stdin:
    with open(log, "a") as f:
        f.write(line)
    if mode == "hang":
        time.sleep(30)
        continue
    message = json.loads(line)
    if message.get("method") == "initialize":
        out({"method": "some/notification", "params": {"x": 1}})
        out("not json at all")
        out({"id": 99, "result": {"unrelated": True}})
        out({"id": message["id"], "result": {"userAgent": "fake"}})
    elif message.get("method") == "account/rateLimits/read":
        if mode == "exit":
            sys.exit(0)
        if mode == "error":
            out({"id": message["id"], "error": {"code": -32000, "message": "nope"}})
        else:
            out({"id": message["id"], "result": json.loads(result)})
"""


class CodexRpc(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fake = self.tmp / "fake_app_server.py"
        self.fake.write_text(FAKE_APP_SERVER)
        self.log = self.tmp / "log"

    def fetch(self, mode="ok", result=CODEX, timeout=1.0):
        cmd = [sys.executable, str(self.fake), mode, str(self.log), json.dumps(result)]
        with (
            mock.patch.object(usage, "CODEX_CMD", cmd),
            mock.patch.object(usage, "RPC_TIMEOUT", timeout),
        ):
            return usage.codex_fetch()

    def messages(self):
        lines = self.log.read_text().splitlines()
        return int(lines[0].split()[1]), [json.loads(line) for line in lines[1:]]

    def assert_gone(self, pid):
        time.sleep(0.1)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_handshake_then_rate_limits_only_and_process_stopped(self):
        self.assertEqual(usage.codex_weekly(self.fetch(), NOW), 41)
        pid, sent = self.messages()
        self.assertEqual(
            [m.get("method") for m in sent],
            ["initialize", "initialized", "account/rateLimits/read"],
        )
        self.assertEqual(sent[0]["id"], 1)
        self.assertEqual(
            sent[0]["params"], {"clientInfo": {"name": "automatic_goal_usage", "version": "1.0"}}
        )
        self.assertNotIn("id", sent[1])
        self.assertEqual(sent[2]["id"], 2)
        self.assert_gone(pid)

    def test_hanging_server_times_out_within_budget_and_is_killed(self):
        started = time.monotonic()
        with self.assertRaisesRegex(usage.Unavailable, "codex rpc timeout"):
            self.fetch("hang", timeout=0.5)
        self.assertLess(time.monotonic() - started, 4)
        pid, _ = self.messages()
        self.assert_gone(pid)

    def test_eof_error_and_missing_binary_pause(self):
        with self.assertRaisesRegex(usage.Unavailable, "codex exited"):
            self.fetch("exit")
        with self.assertRaisesRegex(usage.Unavailable, "codex rpc error"):
            self.fetch("error")
        with (
            mock.patch.object(usage, "CODEX_CMD", ["/nonexistent/codex"]),
            self.assertRaisesRegex(usage.Unavailable, "codex not found"),
        ):
            usage.codex_fetch()


class Clock(unittest.TestCase):
    """Read the clock after each response to reject windows that reset during a slow read."""

    def setUp(self):
        self.clock = 1_800_000_000.0
        patcher = mock.patch.object(usage, "now", lambda: self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fetch(self, payload, advance=0):
        def delayed():
            self.clock += advance
            return payload

        return delayed

    def test_reset_passing_during_a_slow_response_pauses_that_provider_only(self):
        data = claude(resets_at="2027-01-16T00:00:00+00:00")  # epoch 1_800_230_400
        data["five_hour"]["resets_at"] = "2027-01-16T00:00:00+00:00"
        codex = {"rateLimitsByLimitId": {"codex": {"primary": window(41, reset=1_800_230_400)}}}
        with (
            mock.patch.object(usage, "claude_fetch", self.fetch(data)),
            mock.patch.object(usage, "codex_fetch", self.fetch(codex)),
        ):
            self.assertEqual([row[0] for row in usage.read_usage().values()], [16, 10.0, 41])
        with (
            mock.patch.object(usage, "claude_fetch", self.fetch(data, advance=300_000)),
            mock.patch.object(usage, "codex_fetch", self.fetch(codex)),
        ):
            rows = usage.read_usage()
        self.assertEqual(rows["claude fable weekly"], (None, "stale window"))
        self.assertEqual(rows["claude 5h"], (None, "stale window"))
        self.assertEqual(
            rows["codex weekly"], (None, "stale window")
        )  # the clock moved on before codex was validated
        self.clock = 1_800_000_000.0
        with (
            mock.patch.object(usage, "claude_fetch", self.fetch(data)),
            mock.patch.object(usage, "codex_fetch", self.fetch(codex, advance=300_000)),
        ):
            rows = usage.read_usage()
        self.assertEqual((rows["claude fable weekly"][0], rows["claude 5h"][0]), (16, 10.0))
        self.assertEqual(rows["codex weekly"], (None, "stale window"))


class Main(unittest.TestCase):
    def setUp(self):
        for name, value in [("claude_fetch", lambda: CLAUDE), ("codex_fetch", lambda: CODEX)]:
            patcher = mock.patch.object(usage, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_ready_only_when_every_meter_is_at_or_above_its_floor(self):
        # fixtures: fable 16% used, 5h 10% used, codex 41% used
        code, out = run_main("84", "90", "59")
        self.assertEqual((code, out.splitlines()[-1]), (0, "USAGE_READY"))
        self.assertEqual(
            out.splitlines()[:3],
            [
                "claude fable weekly: 84% remaining (pause below 84% remaining)",
                "claude 5h: 90% remaining (pause below 90% remaining)",
                "codex weekly: 59% remaining (pause below 59% remaining)",
            ],
        )
        self.assertEqual(run_main("50", "50", "30")[1].splitlines()[-1], "USAGE_READY")  # defaults

    def test_any_meter_below_its_floor_pauses(self):
        for limits, line in [
            (
                ("85", "1", "1"),
                "claude fable weekly: 84% remaining (pause below 85% remaining) -> pause",
            ),
            (("1", "91", "1"), "claude 5h: 90% remaining (pause below 91% remaining) -> pause"),
            (("1", "1", "60"), "codex weekly: 59% remaining (pause below 60% remaining) -> pause"),
        ]:
            _, out = run_main(*limits)
            self.assertIn(line, out)
            self.assertEqual(out.splitlines()[-1], "USAGE_PAUSE", limits)

    def fetch(self, meter, used):
        """Patch both reads so `meter` reports `used` percent and the other meters report 0."""
        data = claude(percent=0)
        data["five_hour"]["utilization"] = 0
        codex = {"rateLimitsByLimitId": {"codex": {"primary": window(0)}}}
        if meter == "claude fable weekly":
            data["limits"][2]["percent"] = used
        elif meter == "claude 5h":
            data["five_hour"]["utilization"] = used
        else:
            codex["rateLimitsByLimitId"]["codex"]["primary"]["usedPercent"] = used
        return mock.patch.multiple(usage, claude_fetch=lambda: data, codex_fetch=lambda: codex)

    def test_every_floor_compares_100_minus_used_with_equality_allowed(self):
        for meter in usage.METERS:
            for used, floor, ready in [
                (50, "50", True),  # exactly the floor remaining still runs
                (50.5, "50", False),  # just below pauses
                (49.5, "50", True),  # just above runs
                (30, "70", True),
                (30.5, "70", False),
                (29.5, "70", True),
                (70, "30", True),
                (70.5, "30", False),
                (69.5, "30", True),
                (0, "100", True),
                (1, "100", False),
                (100, "1", False),
                (99, "1", True),
            ]:
                floors = ["1", "1", "1"]
                floors[usage.METERS.index(meter)] = floor
                with self.fetch(meter, used):
                    _, out = run_main(*floors)
                self.assertEqual(
                    out.splitlines()[-1],
                    "USAGE_READY" if ready else "USAGE_PAUSE",
                    (meter, used, floor),
                )
                self.assertIn(
                    f"{meter}: {100 - used:g}% remaining (pause below {floor}% remaining)", out
                )

    def test_unreadable_meter_pauses_and_names_the_reason(self):
        def boom():
            raise usage.Unavailable("codex rpc timeout")

        with mock.patch.object(usage, "codex_fetch", boom):
            code, out = run_main("100", "100", "100")
        self.assertIn(
            "codex weekly: unavailable (codex rpc timeout) (pause below 100% remaining) -> pause",
            out,
        )
        self.assertEqual(out.splitlines()[-1], "USAGE_PAUSE")

        def crash():
            raise RuntimeError("secret body " + TOKEN)

        with mock.patch.object(usage, "claude_fetch", crash):
            code, out = run_main("100", "100", "100")
        self.assertIn("claude fable weekly: unavailable (RuntimeError)", out)
        self.assertNotIn(TOKEN, out)
        self.assertEqual(out.splitlines()[-1], "USAGE_PAUSE")

    def test_wrong_argument_count_is_an_error(self):
        self.assertIn("usage:", usage.main(["usage.py", "50"]))
        self.assertIn("usage:", usage.main(["usage.py", "50", "70", "40", "1"]))


if __name__ == "__main__":
    unittest.main()
