import pytest
from app.services.wording_gate import check


@pytest.mark.parametrize("subj,body", [
    ("A sample for Comfort Inn Alstonia?", "Hi"),
    ("Hello", "Shall I send sample kits?"),
    ("Hello", "We can send a complimentary sample kit to your kitchen."),
    ("Hello", "Following up on the sample we sent last week."),
    ("Hello", "A free 50g tasting jar is on us."),
    ("Hello", "Our coffee is 100% pure."),
    ("Hello", "Bold is a freeze-dried instant coffee."),
    ("Hello", "Ultra Blend, freeze dried for a rich cup."),
    ("Hello", "All our coffees are freeze-dried."),
    ("Hello", "Ultra Blend has no chicory."),
    ("Hello", "Ultra Blend is chicory-free."),
])
def test_blocks(subj, body):
    ok, why = check(subj, body)
    assert not ok, why


@pytest.mark.parametrize("body", [
    "Purista is pure coffee with no chicory.",
    "Bold and Ultra Blend are agglomerated instant coffees. Ultra Blend contains chicory.",
    "Purica is a freeze-dried pure coffee.",
    "Pure coffee. No chicory. Would a call this week help?",
    "",
])
def test_allows(body):
    ok, why = check("Purity Beans for your store", body)
    assert ok, why


def test_send_queue_refuses_automation_approval():
    from app.services import send_queue as sq
    class L: id = 1; email = "x@example.com"
    with pytest.raises(PermissionError):
        sq.approve(L(), None, approved_by="AUTO_WORKER")
    assert sq.is_founder_actor("FOUNDER")
    assert not sq.is_founder_actor("AUTO_WORKER")
    assert not sq.is_founder_actor("TECH_AUTOMATION")


def test_drain_has_no_head_slice():
    import inspect
    from app.services import send_queue as sq
    src = inspect.getsource(sq.drain)
    assert "todo[:max_per_run]" not in src
    assert "attempts >= max_per_run" in src


def test_domain_rule_keys():
    from app.services.domain_rule import key_for
    assert key_for("a@Shop.in") == "@shop.in"
    assert key_for("b@shop.in") == "@shop.in"
    assert key_for("x@gmail.com") == "x@gmail.com"
    assert key_for("bad") == ""


def test_domain_rule_fails_closed():
    from app.services.domain_rule import check
    ok, why = check(None, "a@shop.in")
    assert not ok and "unavailable" in why
