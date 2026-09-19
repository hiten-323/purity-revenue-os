"""
Startup hook, called once from main.py.

It used to replace outreach_search.apply_call_outcome with a full second copy
of that function, so that an engine failure queued FOUNDER_REVIEW instead of
falling back to the OUTCOMES registry. outreach_search.apply_call_outcome has
since gained exactly that behaviour itself -- an AST comparison on 2026-09-19
found the two copies identical once whitespace and the `m.` prefix were
normalised. The patch had stopped adding behaviour and started adding risk:

  * The API process (which imports main.py) ran the patched copy; every other
    process, and every unit test, ran the original. A fix to
    outreach_search.apply_call_outcome passed its tests and was silently
    overridden in production -- two implementations, chosen by which process
    you happened to be in.
  * outreach_search's own docstring claimed the patch was installed "at the
    bottom of this file" so the worker got it too. It was not; only main.py
    ever called this.

So the patch is gone and there is one apply_call_outcome, in outreach_search,
for every process. test_call_outcome_single_implementation pins that.

What remains here is the OSM discovery fallback (Nominatim + Overpass) when
DISCOVERY_MAPS_PROVIDER is osm/auto, which this hook has always installed.
"""
from __future__ import annotations


def install() -> None:
    try:
        from app.services.osm_places import install_osm_maps_fallback
        install_osm_maps_fallback()
    except Exception as e:
        print(f"[OSM] maps fallback not installed: {e}")
