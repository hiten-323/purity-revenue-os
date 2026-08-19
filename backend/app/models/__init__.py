"""Models package — loading this package applies fail-closed EMAIL_SENT proof."""
from app.models import models as _models  # noqa: F401

# Always install the fail-closed listener when the package is imported.
try:
    import app.models.send_proof_fix  # noqa: F401
except Exception as _e:
    print(f"[models] send_proof_fix not applied: {_e.__class__.__name__}: {_e}")

# Register adaptive outreach tables before Base.metadata.create_all().
try:
    import app.services.smart_outreach  # noqa: F401
except Exception as _e:
    print(f"[models] smart_outreach models not applied: {_e.__class__.__name__}: {_e}")
