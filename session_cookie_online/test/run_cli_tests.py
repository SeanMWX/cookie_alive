#!/usr/bin/env python3
"""
Run the local unittest suite for session_cookie_online
"""

from pathlib import Path
import sys
import unittest


def main():
    repo_root = Path(__file__).resolve().parents[1]
    suite = unittest.defaultTestLoader.discover(str(repo_root / "test"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
