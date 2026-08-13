"""Models package. Importing models applies the fail-closed send-proof listener."""
from app.models import models as _models  # noqa: F401

# Same-connection, fail-closed EMAIL_SENT proof. Must run after models defines
# the original listener so event.remove can detach it. Worker and API both
# import this package (or models.models); either path installs the fix.
try:
    import app.models.send_proof_fix  # noqa: F401
except Exception as _e:
    # Never leave the original fail-open listener without a visible signal.
    print(f"[models] send_proof_fix not applied: {_e.__class__.__name__}: {_e}")
