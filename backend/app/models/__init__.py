"""Models package — loading this package applies fail-closed EMAIL_SENT proof."""
from app.models import models as _models  # noqa: F401

# Always install the fail-closed listener when the package is imported.
# Worker and API often do `from app.models.models import ...` which does NOT
# execute this file; those paths still need an explicit send_proof_fix import
# (worker.py and main.py do that). Paths that import `app.models` get it here.
try:
    import app.models.send_proof_fix  # noqa: F401
except Exception as _e:
    print(
        f"[models] send_proof_fix not applied: {_e.__class__.__name__}: {_e}"
    )
