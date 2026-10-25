"""Run: env -u PYTHONPATH uv run python app_server.py [--port 8765]"""

from __future__ import annotations

import repo_root
from termcolor import cprint

repo_root.ensure()

from app.server import main  # noqa: E402


if __name__ == "__main__":
    cprint("starting app server…", "green")
    main()
