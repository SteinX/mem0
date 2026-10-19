import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest


def load_models(monkeypatch: pytest.MonkeyPatch, enabled: str | None) -> ModuleType:
    if enabled is None:
        monkeypatch.delenv("MEM0_NLP_ENABLED", raising=False)
    else:
        monkeypatch.setenv("MEM0_NLP_ENABLED", enabled)
    path = Path(__file__).resolve().parents[2] / "mem0/utils/spacy_models.py"
    spec = spec_from_file_location("test_spacy_loader", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("getter", ["get_nlp_full", "get_nlp_lemma"])
@pytest.mark.parametrize("cached", [True, False])
@pytest.mark.parametrize("enabled", ["false", "FALSE", "0", "no", "off"])
def test_disabled_nlp_bypasses_cache_and_model_loading(
    monkeypatch: pytest.MonkeyPatch,
    getter: str,
    cached: bool,
    enabled: str,
) -> None:
    # Given a disabled process flag and either a cold or already populated cache.
    module = load_models(monkeypatch, enabled)
    model = Mock(name="cached-model")
    cache_name = "_nlp_full" if getter == "get_nlp_full" else "_nlp_lemma"
    monkeypatch.setattr(module, cache_name, model if cached else None)
    ensure_available = Mock()
    load = Mock(return_value=model)
    spacy = ModuleType("spacy")
    monkeypatch.setattr(spacy, "load", load, raising=False)
    monkeypatch.setitem(sys.modules, "spacy", spacy)
    monkeypatch.setattr(module, "_ensure_model_available", ensure_available)

    # When the public loader is called, then no model/download path is entered.
    assert getattr(module, getter)() is None
    ensure_available.assert_not_called()
    load.assert_not_called()


@pytest.mark.parametrize("getter", ["get_nlp_full", "get_nlp_lemma"])
@pytest.mark.parametrize("cached", [True, False])
@pytest.mark.parametrize("enabled", [None, "true"])
def test_default_and_enabled_nlp_preserve_existing_cache_and_load_paths(
    monkeypatch: pytest.MonkeyPatch,
    getter: str,
    cached: bool,
    enabled: str | None,
) -> None:
    # Given the default/enabled flag and either a cached or not-yet-loaded model.
    module = load_models(monkeypatch, enabled)
    model = Mock(name="spacy-model")
    cache_name = "_nlp_full" if getter == "get_nlp_full" else "_nlp_lemma"
    monkeypatch.setattr(module, cache_name, model if cached else None)
    ensure_available = Mock()
    load = Mock(return_value=model)
    spacy = ModuleType("spacy")
    monkeypatch.setattr(spacy, "load", load, raising=False)
    monkeypatch.setitem(sys.modules, "spacy", spacy)
    monkeypatch.setattr(module, "_ensure_model_available", ensure_available)

    # When the public loader is called, then existing cache/loading behavior stays.
    assert getattr(module, getter)() is model
    if cached:
        ensure_available.assert_not_called()
        load.assert_not_called()
    else:
        ensure_available.assert_called_once_with()
        kwargs = {"disable": ["ner", "parser"]} if getter == "get_nlp_lemma" else {}
        load.assert_called_once_with("en_core_web_sm", **kwargs)
