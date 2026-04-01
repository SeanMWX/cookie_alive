# Local Test Environment

This directory validates the deterministic runtime layer for `session_cookie_online`.

## What These Tests Cover

- database path resolution under `~/.cookie_alive/<db_name>.db`
- profile CRUD and cookie retrieval
- keepalive refreshes that merge `Set-Cookie` updates
- looped keepalive runs with deterministic test timing
- local HTTP wrapper routes for `/health`, `/pull`, `/check`, and `/list`

## Entry Points

- `run_smoke.py`: quick end-to-end sanity check
- `run_cli_tests.py`: full local unittest suite
