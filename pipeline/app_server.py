# Changes: reserve stdout for the sidecar port announcement.
"""Run: env -u PYTHONPATH uv run python app_server.py [--port 8765]"""

from __future__ import annotations

import repo_root

repo_root.ensure()

from app.server import main  # noqa: E402


if __name__ == "__main__":
    main()
