"""
One definition per concept, asserted structurally so it cannot drift back.

Duplication is not the defect. Drift is. Two copies agree on the day they are
written and diverge on the day one of them is improved, and nothing fails when
they do — which is why every instance below was found by a deliberate sweep
rather than by a broken test.

What the sweep actually found:

    normalise_msisdn   2 copies that DISAGREED. "09876543210" became
                       919876543210 in one and 09876543210 in the other. The
                       second is not a dialable destination, so the two
                       WhatsApp paths would have sent to different numbers.
                       A test in this repo already carried the comment "two
                       normalisers that disagree send to two different
                       numbers" — and asserted only one of them.

    FREE_MAIL          3 copies, all different.
    ROLE_PREFIX        2 copies differing by 8 entries — reservations@,
                       bookings@, events@, enquiries@ were known to
                       trust_promoter and unknown to contact_trust. Those are
                       what a hotel or restaurant publishes, so the two modules
                       judged the priority segment differently.

    CALLABLE_SEGMENTS  2 copies gating 2 different columns with 2 different
                       vocabularies, so one business could pass one gate and
                       fail the other.

These tests read the AST rather than the behaviour, because a behavioural test
only catches drift once the copies have already diverged.
"""
from __future__ import annotations

import ast
import os

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The single source. It is allowed — required — to define these.
IDENTITY = os.path.join("app", "services", "identity.py")

# Concepts that must have exactly one definition, and where it lives.
OWNED = {
    "digits_only": IDENTITY,
    "msisdn": IDENTITY,
    "is_landline": IDENTITY,
    "FREE_MAIL": IDENTITY,
    "ROLE_PREFIX": IDENTITY,
    "EMAIL_RE": IDENTITY,
}

# contact_enricher re-exports digits_only/is_landline under their own names for
# the many callers that already import them from there. A re-export is an
# alias, not a second definition, and the AST walk below only flags real ones.
ALLOWED_REDEFINITION = {
    # module path -> names it may legitimately redefine, with the reason
}


def _modules():
    for root in ("app", "scripts"):
        for dp, dn, fn in os.walk(os.path.join(BACKEND, root)):
            if "__pycache__" in dp:
                continue
            for f in fn:
                if f.endswith(".py"):
                    yield os.path.join(dp, f)
    for f in os.listdir(BACKEND):
        if f.endswith(".py"):
            yield os.path.join(BACKEND, f)


def _real_definitions(path, name):
    """Lines where `name` is DEFINED here, ignoring imports and aliases."""
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
    except SyntaxError:
        return []
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            hits.append(node.lineno)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if not (isinstance(t, ast.Name) and t.id == name):
                    continue
                # An alias is not a second definition. Both forms count as one:
                #   digits_only = digits_only            (ast.Name)
                #   digits_only = _identity.digits_only  (ast.Attribute)
                # The first version of this test only recognised the first form
                # and flagged contact_enricher's legitimate re-exports.
                if isinstance(node.value, (ast.Name, ast.Attribute)):
                    continue
                hits.append(node.lineno)
    return hits


@pytest.mark.parametrize("name,owner", sorted(OWNED.items()))
def test_defined_in_exactly_one_place(name, owner):
    offenders = []
    for path in _modules():
        rel = os.path.relpath(path, BACKEND)
        if rel == owner:
            continue
        if name in ALLOWED_REDEFINITION.get(rel, ()):
            continue
        for line in _real_definitions(path, name):
            offenders.append(f"{rel}:{line}")

    assert not offenders, (
        f"{name} is defined outside {owner}: {offenders}. Import it instead — "
        f"a second copy agrees today and drifts the day one is improved, and "
        f"nothing fails when it does.")


def test_the_two_msisdn_normalisers_are_the_same_object():
    """Not merely equal on the cases someone thought to test."""
    from app.services.identity import msisdn
    from app.services.whatsapp_evolution import normalise_msisdn as evo
    from app.services.whatsapp_gateway.client import normalise_msisdn as gw

    assert evo is msisdn
    assert gw is msisdn


def test_the_case_that_actually_diverged():
    """A national trunk prefix must not survive into a destination."""
    from app.services.identity import msisdn

    assert msisdn("09876543210") == "919876543210"
    assert msisdn("9876543210") == "919876543210"
    assert msisdn("+91 98765 43210") == "919876543210"


def test_matching_and_sending_are_different_functions():
    """digits_only is for MATCHING rows; msisdn is for SENDING. Conflating
    them is how "09876543210" reached a provider as a destination."""
    from app.services.identity import digits_only, msisdn

    assert digits_only("09876543210") == "9876543210"
    assert msisdn("09876543210") == "919876543210"
    assert digits_only("09876543210") != msisdn("09876543210")


def test_trust_modules_share_one_vocabulary():
    from app.services import scrapling_harvester as sh
    from app.services import trust_promoter as tp
    from app.services.identity import FREE_MAIL, ROLE_PREFIX

    assert tp.FREE_MAIL is FREE_MAIL
    assert tp.ROLE_PREFIX is ROLE_PREFIX
    assert sh.FREE_MAIL is FREE_MAIL


def test_the_role_prefixes_that_were_missing():
    """A hotel publishes reservations@; a restaurant publishes bookings@.
    contact_trust did not know either, so it judged the priority segment
    differently from trust_promoter."""
    from app.services.identity import ROLE_PREFIX

    for prefix in ("reservations", "bookings", "events", "enquiries"):
        assert prefix in ROLE_PREFIX, prefix


def test_the_free_mail_domains_that_were_missing():
    from app.services.identity import FREE_MAIL

    for domain in ("icloud.com", "protonmail.com", "ymail.com", "yahoo.co.in"):
        assert domain in FREE_MAIL, domain


def test_identity_imports_nothing_from_the_app():
    """It must stay a leaf, or the modules that need it cannot import it."""
    import inspect

    from app.services import identity

    tree = ast.parse(inspect.getsource(identity))
    for node in ast.walk(tree):
        mod = ""
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
        elif isinstance(node, ast.Import):
            mod = node.names[0].name
        assert not mod.startswith("app."), (
            f"identity imports {mod}; it must remain a leaf so every module "
            f"can use it without a cycle")
