#!/usr/bin/env python3
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
import importlib.util
import json
import os
import sys


def load_module():
    skill_root = Path(__file__).resolve().parents[1]
    script_path = skill_root / "scripts" / "cookie_alive.py"
    spec = importlib.util.spec_from_file_location("cookie_alive_script", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def invoke_cli(module, args):
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = module.main(args)
    return exit_code, stdout.getvalue().strip(), stderr.getvalue().strip()


def make_server_handler():
    class Handler(BaseHTTPRequestHandler):
        hits = 0
        cookies_seen = []

        def do_GET(self):
            type(self).hits += 1
            type(self).cookies_seen.append(self.headers.get("Cookie"))
            self.send_response(200)
            self.send_header("Set-Cookie", f"sessionid=renewed{type(self).hits}; Path=/; HttpOnly")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):
            return

    return Handler


def make_error_server_handler():
    class Handler(BaseHTTPRequestHandler):
        hits = 0

        def do_GET(self):
            type(self).hits += 1
            self.send_response(401)
            self.send_header("Set-Cookie", "sessionid=expired; Max-Age=0; Path=/")
            self.end_headers()
            self.wfile.write(b"denied")

        def log_message(self, format, *args):
            return

    return Handler


def make_redirect_server_handler():
    class Handler(BaseHTTPRequestHandler):
        hits = 0

        def do_GET(self):
            type(self).hits += 1
            self.send_response(302)
            self.send_header("Location", "https://account.chsi.com.cn/passport/login")
            self.send_header("Set-Cookie", "sessionid=expired; Max-Age=0; Path=/")
            self.end_headers()

        def log_message(self, format, *args):
            return

    return Handler


def make_path_cookie_server_handler():
    class Handler(BaseHTTPRequestHandler):
        hits_by_path = {}

        def do_GET(self):
            path = self.path
            type(self).hits_by_path[path] = type(self).hits_by_path.get(path, 0) + 1
            hit = type(self).hits_by_path[path]
            cookie_name = path.strip("/").replace("/", "_") or "root"
            self.send_response(200)
            self.send_header("Set-Cookie", f"{cookie_name}=renewed{hit}; Path=/; HttpOnly")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):
            return

    return Handler


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)

    def current_utc_dt(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)


class TestSessionCookieOnline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def setUp(self):
        self.temp_home = TemporaryDirectory()
        self.addCleanup(self.temp_home.cleanup)
        self.previous_home = os.environ.get("COOKIE_ALIVE_HOME")
        os.environ["COOKIE_ALIVE_HOME"] = self.temp_home.name
        self.addCleanup(self.restore_cookie_alive_home)
        self.db_args = ["--db-name", "unit_store"]

    def restore_cookie_alive_home(self):
        if self.previous_home is None:
            os.environ.pop("COOKIE_ALIVE_HOME", None)
        else:
            os.environ["COOKIE_ALIVE_HOME"] = self.previous_home

    def invoke(self, *args):
        return invoke_cli(self.module, self.db_args + list(args))

    def test_skill_root_contains_skill_md(self):
        skill_root = Path(__file__).resolve().parents[1]
        self.assertTrue((skill_root / "SKILL.md").exists())

    def test_resolve_db_path_uses_cookie_alive_home(self):
        db_path = self.module.resolve_db_path("tenant_one")
        expected = Path(self.temp_home.name) / "tenant_one.db"
        self.assertEqual(expected, db_path)

    def test_upsert_get_and_delete_profile(self):
        exit_code, stdout, stderr = self.invoke(
            "upsert",
            "--profile",
            "dashboard",
            "--refresh-url",
            "https://example.com/api/ping",
            "--cookie-header",
            "sessionid=abc123; csrftoken=xyz789",
            "--header",
            "X-Test: enabled",
            "--interval-seconds",
            "120",
        )
        self.assertEqual(0, exit_code, stderr)
        stored = json.loads(stdout)
        self.assertEqual("dashboard", stored["profile"])
        self.assertEqual("https://example.com/api/ping", stored["refresh_url"])
        self.assertEqual("enabled", stored["headers"]["X-Test"])

        exit_code, stdout, stderr = self.invoke(
            "get",
            "--profile",
            "dashboard",
            "--format",
            "header",
        )
        self.assertEqual(0, exit_code, stderr)
        self.assertEqual("csrftoken=xyz789; sessionid=abc123", stdout)

        exit_code, stdout, stderr = self.invoke(
            "delete",
            "--profile",
            "dashboard",
        )
        self.assertEqual(0, exit_code, stderr)
        deleted = json.loads(stdout)
        self.assertTrue(deleted["deleted"])

        exit_code, _, stderr = self.invoke(
            "get",
            "--profile",
            "dashboard",
        )
        self.assertEqual(1, exit_code)
        self.assertIn("profile not found", stderr)

    def test_upsert_from_cookie_file_json(self):
        cookie_file = Path(self.temp_home.name) / "cookie.json"
        cookie_file.write_text('{"sessionid":"abc123","csrftoken":"xyz789"}', encoding="utf-8")

        exit_code, _, stderr = self.invoke(
            "upsert",
            "--profile",
            "from_file",
            "--refresh-url",
            "https://example.com/api/ping",
            "--cookie-file",
            str(cookie_file),
            "--headers-json",
            '{"Referer":"https://example.com/app"}',
        )
        self.assertEqual(0, exit_code, stderr)

        exit_code, stdout, stderr = self.invoke(
            "get",
            "--profile",
            "from_file",
            "--format",
            "json",
        )
        self.assertEqual(0, exit_code, stderr)
        cookies = json.loads(stdout)
        self.assertEqual("abc123", cookies["sessionid"])
        self.assertEqual("xyz789", cookies["csrftoken"])

    def test_list_active_only_filters_inactive_profiles(self):
        exit_code, _, stderr = self.invoke(
            "upsert",
            "--profile",
            "active_profile",
            "--refresh-url",
            "https://example.com/api/ping",
            "--cookie-header",
            "sessionid=active",
        )
        self.assertEqual(0, exit_code, stderr)

        exit_code, _, stderr = self.invoke(
            "upsert",
            "--profile",
            "inactive_profile",
            "--refresh-url",
            "https://example.com/api/ping",
            "--cookie-header",
            "sessionid=inactive",
            "--inactive",
        )
        self.assertEqual(0, exit_code, stderr)

        exit_code, stdout, stderr = self.invoke("list")
        self.assertEqual(0, exit_code, stderr)
        profiles = {row["profile"] for row in json.loads(stdout)}
        self.assertEqual({"active_profile", "inactive_profile"}, profiles)

        exit_code, stdout, stderr = self.invoke("list", "--active-only")
        self.assertEqual(0, exit_code, stderr)
        active_profiles = {row["profile"] for row in json.loads(stdout)}
        self.assertEqual({"active_profile"}, active_profiles)

    def test_refresh_updates_cookie_from_response(self):
        handler = make_server_handler()
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 2)

        refresh_url = f"http://127.0.0.1:{server.server_port}/refresh"
        exit_code, _, stderr = self.invoke(
            "upsert",
            "--profile",
            "dashboard",
            "--refresh-url",
            refresh_url,
            "--cookie-header",
            "sessionid=initial",
        )
        self.assertEqual(0, exit_code, stderr)

        exit_code, stdout, stderr = self.invoke(
            "refresh",
            "--profile",
            "dashboard",
        )
        self.assertEqual(0, exit_code, stderr)
        refreshed = json.loads(stdout)
        self.assertEqual(200, refreshed["last_status_code"])
        self.assertEqual("sessionid=renewed1", refreshed["cookie_header"])
        self.assertEqual(["sessionid=initial"], handler.cookies_seen)

    def test_refresh_http_error_records_failure_state(self):
        handler = make_error_server_handler()
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 2)

        refresh_url = f"http://127.0.0.1:{server.server_port}/refresh"
        exit_code, _, stderr = self.invoke(
            "upsert",
            "--profile",
            "dashboard",
            "--refresh-url",
            refresh_url,
            "--cookie-header",
            "sessionid=initial; csrftoken=token1",
        )
        self.assertEqual(0, exit_code, stderr)

        exit_code, _, stderr = self.invoke(
            "refresh",
            "--profile",
            "dashboard",
        )
        self.assertEqual(1, exit_code)
        self.assertIn("HTTP Error 401", stderr)

        exit_code, stdout, stderr = self.invoke(
            "get",
            "--profile",
            "dashboard",
            "--format",
            "record",
        )
        self.assertEqual(0, exit_code, stderr)
        record = json.loads(stdout)
        self.assertEqual(401, record["last_status_code"])
        self.assertIn("HTTP 401", record["last_error"])
        self.assertEqual("csrftoken=token1", record["cookie_header"])

    def test_refresh_redirect_to_login_is_treated_as_failure(self):
        handler = make_redirect_server_handler()
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 2)

        refresh_url = f"http://127.0.0.1:{server.server_port}/refresh"
        exit_code, _, stderr = self.invoke(
            "upsert",
            "--profile",
            "dashboard",
            "--refresh-url",
            refresh_url,
            "--cookie-header",
            "sessionid=initial; csrftoken=token1",
        )
        self.assertEqual(0, exit_code, stderr)

        exit_code, _, stderr = self.invoke(
            "refresh",
            "--profile",
            "dashboard",
        )
        self.assertEqual(1, exit_code)
        self.assertIn("redirect to https://account.chsi.com.cn/passport/login", stderr)

        exit_code, stdout, stderr = self.invoke(
            "get",
            "--profile",
            "dashboard",
            "--format",
            "record",
        )
        self.assertEqual(0, exit_code, stderr)
        record = json.loads(stdout)
        self.assertEqual(302, record["last_status_code"])
        self.assertIn("redirect to https://account.chsi.com.cn/passport/login", record["last_error"])
        self.assertEqual("csrftoken=token1", record["cookie_header"])

    def test_check_command_reports_alive_state(self):
        handler = make_server_handler()
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 2)

        refresh_url = f"http://127.0.0.1:{server.server_port}/refresh"
        exit_code, _, stderr = self.invoke(
            "upsert",
            "--profile",
            "dashboard",
            "--refresh-url",
            refresh_url,
            "--cookie-header",
            "sessionid=initial",
        )
        self.assertEqual(0, exit_code, stderr)

        exit_code, stdout, stderr = self.invoke(
            "check",
            "--profile",
            "dashboard",
        )
        self.assertEqual(0, exit_code, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["alive"])
        self.assertEqual(200, payload["last_status_code"])

    def test_check_command_reports_not_alive_on_redirect(self):
        handler = make_redirect_server_handler()
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 2)

        refresh_url = f"http://127.0.0.1:{server.server_port}/refresh"
        exit_code, _, stderr = self.invoke(
            "upsert",
            "--profile",
            "dashboard",
            "--refresh-url",
            refresh_url,
            "--cookie-header",
            "sessionid=initial",
        )
        self.assertEqual(0, exit_code, stderr)

        exit_code, stdout, stderr = self.invoke(
            "check",
            "--profile",
            "dashboard",
        )
        self.assertEqual(1, exit_code)
        self.assertEqual("", stderr)
        payload = json.loads(stdout)
        self.assertFalse(payload["alive"])
        self.assertEqual(302, payload["last_status_code"])
        self.assertIn("redirect to https://account.chsi.com.cn/passport/login", payload["last_error"])

    def test_pull_returns_current_cookie_for_other_programs(self):
        exit_code, _, stderr = self.invoke(
            "upsert",
            "--profile",
            "dashboard",
            "--refresh-url",
            "https://example.com/api/ping",
            "--cookie-header",
            "sessionid=abc123; csrftoken=xyz789",
        )
        self.assertEqual(0, exit_code, stderr)

        exit_code, stdout, stderr = self.invoke(
            "pull",
            "--profile",
            "dashboard",
            "--format",
            "header",
        )
        self.assertEqual(0, exit_code, stderr)
        self.assertEqual("csrftoken=xyz789; sessionid=abc123", stdout)

    def test_pull_with_refresh_returns_updated_cookie(self):
        handler = make_server_handler()
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 2)

        refresh_url = f"http://127.0.0.1:{server.server_port}/refresh"
        exit_code, _, stderr = self.invoke(
            "upsert",
            "--profile",
            "dashboard",
            "--refresh-url",
            refresh_url,
            "--cookie-header",
            "sessionid=initial",
        )
        self.assertEqual(0, exit_code, stderr)

        exit_code, stdout, stderr = self.invoke(
            "pull",
            "--profile",
            "dashboard",
            "--refresh",
            "--format",
            "header",
        )
        self.assertEqual(0, exit_code, stderr)
        self.assertEqual("sessionid=renewed1", stdout)

    def test_run_repeats_keepalive_with_wait_override(self):
        handler = make_server_handler()
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 2)

        refresh_url = f"http://127.0.0.1:{server.server_port}/refresh"
        exit_code, _, stderr = self.invoke(
            "upsert",
            "--profile",
            "dashboard",
            "--refresh-url",
            refresh_url,
            "--cookie-header",
            "sessionid=initial",
            "--interval-seconds",
            "600",
        )
        self.assertEqual(0, exit_code, stderr)

        exit_code, stdout, stderr = self.invoke(
            "run",
            "--profile",
            "dashboard",
            "--iterations",
            "3",
            "--wait-seconds",
            "0",
        )
        self.assertEqual(0, exit_code, stderr)
        runs = json.loads(stdout)
        self.assertEqual(3, len(runs))
        self.assertTrue(all(run["ok"] for run in runs))
        self.assertEqual(3, handler.hits)

        exit_code, stdout, stderr = self.invoke(
            "get",
            "--profile",
            "dashboard",
            "--format",
            "header",
        )
        self.assertEqual(0, exit_code, stderr)
        self.assertEqual("sessionid=renewed3", stdout)

    def test_run_all_refreshes_multiple_profiles_with_individual_intervals(self):
        handler = make_path_cookie_server_handler()
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 2)

        exit_code, _, stderr = self.invoke(
            "upsert",
            "--profile",
            "fast",
            "--refresh-url",
            f"http://127.0.0.1:{server.server_port}/fast",
            "--cookie-header",
            "sessionid=fast",
            "--interval-seconds",
            "2",
        )
        self.assertEqual(0, exit_code, stderr)

        exit_code, _, stderr = self.invoke(
            "upsert",
            "--profile",
            "slow",
            "--refresh-url",
            f"http://127.0.0.1:{server.server_port}/slow",
            "--cookie-header",
            "sessionid=slow",
            "--interval-seconds",
            "4",
        )
        self.assertEqual(0, exit_code, stderr)

        fake_clock = FakeClock()
        original_clock = self.module.current_utc_dt
        original_sleep = self.module.time.sleep
        self.module.current_utc_dt = fake_clock.current_utc_dt
        self.module.time.sleep = fake_clock.sleep
        self.addCleanup(setattr, self.module, "current_utc_dt", original_clock)
        self.addCleanup(setattr, self.module.time, "sleep", original_sleep)

        results = self.module.run_all_profiles(
            db_path=self.module.resolve_db_path("unit_store"),
            iterations=3,
            timeout_override=None,
            stop_on_error=False,
            max_sleep_seconds=1.0,
        )

        self.assertEqual(3, len(results))
        self.assertEqual(["fast", "slow", "fast"], [row["profile"] for row in results])
        self.assertEqual({"/fast": 2, "/slow": 1}, handler.hits_by_path)

    def test_db_name_env_fallback_and_invalid_name_validation(self):
        previous_db_name = os.environ.get("COOKIE_ALIVE_DB_NAME")
        os.environ["COOKIE_ALIVE_DB_NAME"] = "env_store"
        try:
            db_path = self.module.resolve_db_path()
            self.assertEqual(Path(self.temp_home.name) / "env_store.db", db_path)
        finally:
            if previous_db_name is None:
                os.environ.pop("COOKIE_ALIVE_DB_NAME", None)
            else:
                os.environ["COOKIE_ALIVE_DB_NAME"] = previous_db_name

        with self.assertRaises(ValueError):
            self.module.resolve_db_path("../bad")


if __name__ == "__main__":
    unittest.main(verbosity=2)
