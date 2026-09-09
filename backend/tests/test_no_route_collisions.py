"""
Two handlers on one path means one of them never runs.

/api/v1/founder/actions was registered twice: endpoints.py served a "what
should the founder do next" suggestion feed, and founder_router served the
append-only log of what the founder actually DID. api_router is included
first, so the suggestion feed answered every request and the audit trail was
unreachable -- silently, because nothing in the frontend called either path.

The only symptom was a UserWarning about a duplicate operation id, emitted
while building the OpenAPI schema, which nobody reads at boot.

This test reads it.
"""
from __future__ import annotations

import warnings

import pytest


@pytest.fixture(scope="module")
def schema():
    from app.main import app
    return app.openapi()


def test_no_duplicate_operation_ids():
    """FastAPI warns rather than raises, so the warning is the assertion."""
    import importlib
    import sys

    for mod in ("app.main",):
        if mod in sys.modules:
            del sys.modules[mod]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app_module = importlib.import_module("app.main")
        app_module.app.openapi()

    dupes = sorted({
        str(w.message).split(" for function")[0]
        .replace("Duplicate Operation ID ", "")
        for w in caught
        if "Duplicate Operation ID" in str(w.message)
    })
    assert not dupes, (
        "these paths have more than one handler; only the first-included one "
        f"ever runs: {dupes}")


def test_both_founder_endpoints_are_reachable(schema):
    """The collision resolved by keeping both, on distinct paths -- not by
    deleting the one that was losing."""
    paths = schema["paths"]
    assert "/api/v1/founder/actions" in paths, "the decision audit trail"
    assert "/api/v1/founder/action-center" in paths, "the suggestion feed"


def test_every_path_is_unique(schema):
    """A cheap structural check: OpenAPI keys are unique by construction, so
    this guards the count instead -- a route lost to a collision shows up as a
    schema smaller than the routers that fed it."""
    from app.api.endpoints import router as api_router
    from app.api.founder_router import router as founder_router

    # founder_router is constructed with prefix="/founder", and its routes
    # already carry it. Adding it again here produced /founder/founder/... and
    # a failure that looked like five missing endpoints.
    declared = {r.path for r in api_router.routes if hasattr(r, "path")}
    declared |= {r.path for r in founder_router.routes if hasattr(r, "path")}
    served = {p[len("/api/v1"):] for p in schema["paths"] if p.startswith("/api/v1")}

    missing = sorted(declared - served)
    assert not missing, f"declared but not served: {missing}"
