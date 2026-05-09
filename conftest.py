"""Repo-root conftest — makes `import bot.*` and `import dashboard.*`
work when pytest is invoked without PYTHONPATH set (e.g. by the deploy
gate in scripts/deploy.sh on the VPS, where uv's auto-path-injection
isn't in play).

No fixtures defined here; the file exists for its side effect of being
loaded before test collection."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
