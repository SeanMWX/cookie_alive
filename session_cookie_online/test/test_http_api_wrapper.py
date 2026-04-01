#!/usr/bin/env python3
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace
from urllib import error, parse, request
import importlib.util
import json
import os
import sys
import unittest


def load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_refresh_server_handler():
    class Handler(BaseHTTPRequestHandler):
        hits_by_path = {}

        def do_GET(self):
            path = self.path
            type(self).hits_by_path[path] = type(self).hits_by_path.get(path, 0) + 1
            hit = type(self).hits_by_path[path]
            self.send_response(200)
            self.send_header("Set-Cookie", f"sessionid=renewed{hit}; Path=/; HttpOnly")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):
            return

    return Handler


def make_redirect_server_handler():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "https://account.chsi.com.cn/passport/login")
            self.end_headers()

        def log_message(self, format, *args):
            return

    return Handler


class TestHttpApiWrapper(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repo_root = Path(__file__).resolve().parents[2]
        cls.cookie_module = load_module(
            "cookie_alive_script_for_http_wrapper_tests",
            repo_root / "session_cookie_online" / "scripts" / "cookie_alive.py",
        )
        cls.wrapper_module = load_module(
            "http_api_wrapper_for_tests",
            repo_root / "examples" / "http_api_wrapper.py",
        )

    def setUp(self):
        self.temp_home = TemporaryDirectory()
        self.addCleanup(self.temp_home.cleanup)
        self.previous_home = os.environ.get("COOKIE_ALIVE_HOME")
        os.environ["COOKIE_ALIVE_HOME"] = self.temp_home.name
        self.addCleanup(self.restore_cookie_alive_home)
        self.db_name = "api_tests"

    def restore_cookie_alive_home(self):
        if self.previous_home is None:
            os.environ.pop("COOKIE_ALIVE_HOME", None)
        else:
            os.environ["COOKIE_ALIVE_HOME"] = self.previous_home

    def start_server(self, handler_cls, config=None):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        if config is not None:
            server.config = config
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        return server

    def api_get(self, server, path):
        url = f"http://127.0.0.1:{server.server_port}{path}"
        try:
            with request.urlopen(url, timeout=10) as response:
                return response.status, response.headers.get("Content-Type"), response.read().decode("utf-8")
        except error.HTTPError as exc:
            return exc.code, exc.headers.get("Content-Type"), exc.read().decode("utf-8")

    def upsert_profile(self, profile: str, refresh_url: str):
        record = self.cookie_module.upsert_profile(
            db_path=self.cookie_module.resolve_db_path(self.db_name),
            profile=profile,
            refresh_url=refresh_url,
            method="GET",
            interval_seconds=600,
            timeout_seconds=30,
            cookies={"sessionid": "initial"},
            headers={"User-Agent": "test-agent"},
            body=None,
            active=True,
        )
        self.assertEqual(profile, record.profile)

    def make_wrapper_config(self, default_profile: str):
        return SimpleNamespace(
            host="127.0.0.1",
            port=0,
            python_executable=sys.executable,
            db_name=self.db_name,
            profile=default_profile,
        )

    def test_health_and_root_routes(self):
        server = self.start_server(
            self.wrapper_module.CookieAliveAPIHandler,
            config=self.make_wrapper_config("main"),
        )

        status, content_type, body = self.api_get(server, "/health")
        self.assertEqual(200, status)
        self.assertIn("application/json", content_type)
        self.assertEqual(True, json.loads(body)["ok"])

        status, content_type, body = self.api_get(server, "/")
        self.assertEqual(200, status)
        payload = json.loads(body)
        self.assertEqual("cookie_alive_http_wrapper", payload["service"])
        self.assertIn("/pull", payload["routes"])

    def test_pull_refresh_and_list_routes(self):
        refresh_handler = make_refresh_server_handler()
        refresh_server = self.start_server(refresh_handler)
        self.upsert_profile("main", f"http://127.0.0.1:{refresh_server.server_port}/refresh")

        wrapper_server = self.start_server(
            self.wrapper_module.CookieAliveAPIHandler,
            config=self.make_wrapper_config("main"),
        )

        status, content_type, body = self.api_get(
            wrapper_server,
            "/pull?refresh=1&format=header",
        )
        self.assertEqual(200, status)
        self.assertIn("text/plain", content_type)
        self.assertEqual("sessionid=renewed1", body)

        status, content_type, body = self.api_get(
            wrapper_server,
            "/list?active_only=1",
        )
        self.assertEqual(200, status)
        self.assertIn("application/json", content_type)
        payload = json.loads(body)
        self.assertEqual(1, len(payload))
        self.assertEqual("main", payload[0]["profile"])
        self.assertEqual("sessionid=renewed1", payload[0]["cookie_header"])

    def test_check_route_reports_redirect_as_not_alive(self):
        redirect_handler = make_redirect_server_handler()
        redirect_server = self.start_server(redirect_handler)
        self.upsert_profile("main", f"http://127.0.0.1:{redirect_server.server_port}/redirect")

        wrapper_server = self.start_server(
            self.wrapper_module.CookieAliveAPIHandler,
            config=self.make_wrapper_config("main"),
        )

        status, content_type, body = self.api_get(wrapper_server, "/check")
        self.assertEqual(409, status)
        self.assertIn("application/json", content_type)
        payload = json.loads(body)
        self.assertEqual(False, payload["alive"])
        self.assertEqual(302, payload["last_status_code"])
        self.assertIn("redirect to https://account.chsi.com.cn/passport/login", payload["last_error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
