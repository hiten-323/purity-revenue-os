import subprocess
from types import SimpleNamespace
import pytest
from app.api.founder_router import _validated_recording_path


def test_recording_path_requires_exact_call_ref_and_decodable_audio(tmp_path, monkeypatch):
    root = tmp_path / "recordings"
    folder = root / "ppp-12-abc"
    folder.mkdir(parents=True)
    audio = folder / "audio.ogg"
    audio.write_bytes(b"x" * 2048)
    monkeypatch.setenv("CALL_RECORDINGS_DIR", str(root))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="1.25"))
    assert _validated_recording_path(str(audio), "ppp-12-abc", "CATALOGUE_REQUESTED") == str(audio)
    with pytest.raises(ValueError):
        _validated_recording_path(str(audio), "ppp-13-other", "CATALOGUE_REQUESTED")
    with pytest.raises(ValueError):
        _validated_recording_path(str(audio), "ppp-12-abc", "DISPATCH_FAILED")


def test_recording_path_rejects_outside_root_and_zero_duration(tmp_path, monkeypatch):
    root = tmp_path / "recordings"; root.mkdir()
    other = tmp_path / "other" / "ppp-12-abc"; other.mkdir(parents=True)
    audio = other / "audio.ogg"; audio.write_bytes(b"x" * 2048)
    monkeypatch.setenv("CALL_RECORDINGS_DIR", str(root))
    with pytest.raises(ValueError):
        _validated_recording_path(str(audio), "ppp-12-abc", "CATALOGUE_REQUESTED")
    folder = root / "ppp-12-abc"; folder.mkdir()
    inside = folder / "audio.ogg"; inside.write_bytes(b"x" * 2048)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="0"))
    with pytest.raises(ValueError):
        _validated_recording_path(str(inside), "ppp-12-abc", "CATALOGUE_REQUESTED")
