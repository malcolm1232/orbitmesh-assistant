"""OpenRouter client behaviour that does not need the network."""
from types import SimpleNamespace

import pytest

from orbitmesh import llm as llm_mod


def _client_returning(finish_reason, content):
    choice = SimpleNamespace(finish_reason=finish_reason, message=SimpleNamespace(content=content, refusal=None))
    resp = SimpleNamespace(choices=[choice], usage=None)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: resp)))


def test_a_provider_content_filter_is_reported_as_such_not_as_bad_json():
    llm = llm_mod.OpenRouterLLM(api_key="test", base_url="http://unused", model="m")
    llm.client = _client_returning("content_filter", "I'm sorry, but I cannot assist with that request.")
    with pytest.raises(llm_mod.ContentFiltered):
        llm.complete([{"role": "user", "content": "x"}])


def test_a_content_filter_reply_is_never_written_to_the_answer_cache(tmp_path):
    llm = llm_mod.OpenRouterLLM(api_key="test", base_url="http://unused", model="m", cache_dir=tmp_path)
    llm.client = _client_returning("content_filter", "I'm sorry, but I cannot assist with that request.")
    with pytest.raises(llm_mod.ContentFiltered):
        llm.complete([{"role": "user", "content": "x"}])
    assert list(tmp_path.iterdir()) == []
