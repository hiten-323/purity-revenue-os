import os

from app.services import scrapling_client
from app.services.scrapling_harvester import _extract


def test_scrapling_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SCRAPLING_ENABLED", raising=False)
    assert scrapling_client.enabled() is False


def test_scrapling_requires_explicit_enable(monkeypatch):
    monkeypatch.setenv("SCRAPLING_ENABLED", "1")
    assert scrapling_client.enabled() is True


def test_first_party_contact_extraction():
    html = """
    <html><body>
      <a href='mailto:sales@example.in'>sales@example.in</a>
      <a href='tel:+91-98765-43210'>Call</a>
      <a href='https://wa.me/919876543210'>WhatsApp</a>
      <span>agency@unrelated-agency.com</span>
    </body></html>
    """
    result = _extract(html, "https://example.in")
    assert "sales@example.in" in result["emails"]
    assert "+91-98765-43210" in result["phones"]
    assert result["whatsapp"] == "+91-98765-43210"
    assert "agency@unrelated-agency.com" not in result["emails"]


def test_response_content_normalization():
    assert scrapling_client.content_from_response({"content": "hello"}) == "hello"
    assert scrapling_client.content_from_response({"content": None}) == ""
