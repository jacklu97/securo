"""The fixed 120s read timeout hung up on CPU-hosted models mid-computation
(observed: Ollama finished a 4m23s turn after the client had given up)."""
from app.agents.providers.ollama import _read_timeout


def test_default_timeout(monkeypatch):
    monkeypatch.delenv("AGENTS_OLLAMA_TIMEOUT_SECONDS", raising=False)
    assert _read_timeout() == 600.0


def test_env_override(monkeypatch):
    monkeypatch.setenv("AGENTS_OLLAMA_TIMEOUT_SECONDS", "900")
    assert _read_timeout() == 900.0


def test_invalid_env_degrades_to_default(monkeypatch):
    monkeypatch.setenv("AGENTS_OLLAMA_TIMEOUT_SECONDS", "soon")
    assert _read_timeout() == 600.0
