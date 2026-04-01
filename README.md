# cookie_alive

Repo-local OpenClaw skill workspace for `session_cookie_online`.

The skill stores session cookies in SQLite under `~/.cookie_alive/<db_name>.db`, refreshes them against a keepalive URL, and returns cookie headers or JSON for other programs.

## Layout

- `SKILL.md`: skill definition at the repository root
- `references/`: runtime reference docs
- `scripts/`: runtime scripts and example clients
- `test/`: local-only tests, ignored by git
- `skill_creator/`: local helper template used to scaffold the skill, ignored by git

## Local Verification

- `python skill_creator/scripts/quick_validate.py .`
- `python test/run_smoke.py`
- `python test/run_cli_tests.py`

## Example Client

- Examples assume `conda activate py12opcskills`
- `python scripts/examples/use_chsi_cookie.py`
- `python scripts/examples/use_chsi_cookie.py --no-refresh`
- `python scripts/examples/other_program_calls_cookie_alive.py --skip-request`
- `python scripts/examples/other_program_calls_cookie_alive.py --show-command`
- `python scripts/examples/http_api_wrapper.py --host 127.0.0.1 --port 8787`

## HTTP Wrapper

Start the local wrapper:

- `python scripts/examples/http_api_wrapper.py --host 127.0.0.1 --port 8787`

Then other software can call:

- `GET /health`
- `GET /pull?db_name=chsi&profile=main&refresh=1&format=header`
- `GET /check?db_name=chsi&profile=main`
- `GET /list?db_name=chsi&active_only=1`
