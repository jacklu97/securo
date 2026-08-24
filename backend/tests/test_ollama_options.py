"""num_ctx must be set explicitly: Ollama's 2048 default truncates the agent
prompt (observed live: 9681 tokens cut to 2050) and breaks the tool loop."""
from app.agents.providers.ollama import _build_options


def test_num_ctx_default(monkeypatch):
    monkeypatch.delenv("AGENTS_OLLAMA_NUM_CTX", raising=False)
    assert _build_options(0.4, None)["num_ctx"] == 8192


def test_num_ctx_from_env(monkeypatch):
    monkeypatch.setenv("AGENTS_OLLAMA_NUM_CTX", "16384")
    assert _build_options(0.4, None)["num_ctx"] == 16384


def test_num_ctx_invalid_env_degrades_to_default(monkeypatch):
    monkeypatch.setenv("AGENTS_OLLAMA_NUM_CTX", "banana")
    assert _build_options(0.4, None)["num_ctx"] == 8192


def test_max_tokens_maps_to_num_predict(monkeypatch):
    monkeypatch.delenv("AGENTS_OLLAMA_NUM_CTX", raising=False)
    opts = _build_options(0.2, 512)
    assert opts["num_predict"] == 512
    assert opts["temperature"] == 0.2
