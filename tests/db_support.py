"""Helpers for tests that execute Alembic against real PostgreSQL."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_alembic(*args: str) -> None:
    """Invoke the Alembic CLI in a subprocess (its env.py uses asyncio.run)."""

    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} failed with exit code {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
