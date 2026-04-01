#!/usr/bin/env python3
"""
Fast smoke test for session_cookie_online
"""

from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import importlib.util
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


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        hits = 0

        def do_GET(self):
            type(self).hits += 1
            self.send_response(200)
            self.send_header("Set-Cookie", f"sessionid=renewed{type(self).hits}; Path=/")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):
            return

    return Handler


def main():
    module = load_module()
    handler = make_handler()
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    previous_home = os.environ.get("COOKIE_ALIVE_HOME")
    try:
        with TemporaryDirectory() as temp_home:
            os.environ["COOKIE_ALIVE_HOME"] = temp_home
            base_args = ["--db-name", "smoke_store"]
            url = f"http://127.0.0.1:{server.server_port}/refresh"

            exit_code, _, stderr = invoke_cli(
                module,
                base_args
                + [
                    "upsert",
                    "--profile",
                    "dashboard",
                    "--refresh-url",
                    url,
                    "--cookie-header",
                    "sessionid=initial",
                    "--interval-seconds",
                    "1",
                ],
            )
            if exit_code != 0:
                raise RuntimeError(stderr or "upsert failed")

            exit_code, _, stderr = invoke_cli(
                module,
                base_args + ["refresh", "--profile", "dashboard"],
            )
            if exit_code != 0:
                raise RuntimeError(stderr or "refresh failed")

            exit_code, stdout, stderr = invoke_cli(
                module,
                base_args + ["get", "--profile", "dashboard", "--format", "header"],
            )
            if exit_code != 0:
                raise RuntimeError(stderr or "get failed")
            if "sessionid=renewed1" not in stdout:
                raise RuntimeError(f"unexpected cookie header: {stdout}")

            print("[OK] smoke test passed")
    finally:
        if previous_home is None:
            os.environ.pop("COOKIE_ALIVE_HOME", None)
        else:
            os.environ["COOKIE_ALIVE_HOME"] = previous_home
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


if __name__ == "__main__":
    main()
