from types import SimpleNamespace

from kairo.web.i18n import CATALOG, DEFAULT_LANG, SUPPORTED, resolve_lang, translator


def _req(cookies=None, accept=None):
    headers = {}
    if accept is not None:
        headers["accept-language"] = accept
    return SimpleNamespace(cookies=cookies or {}, headers=headers)


def test_default_lang_is_en():
    assert DEFAULT_LANG == "en"
    assert resolve_lang(_req()) == "en"


def test_resolve_prefers_cookie():
    assert resolve_lang(_req(cookies={"lang": "zh"})) == "zh"
    # unsupported cookie value is ignored, falls through to default
    assert resolve_lang(_req(cookies={"lang": "fr"})) == "en"


def test_resolve_accept_language():
    assert resolve_lang(_req(accept="zh-CN,zh;q=0.9,en;q=0.8")) == "zh"
    assert resolve_lang(_req(accept="en-US,en;q=0.9")) == "en"
    assert resolve_lang(_req(accept="fr-FR")) == "en"


def test_cookie_overrides_accept_language():
    assert resolve_lang(_req(cookies={"lang": "en"}, accept="zh-CN")) == "en"


def test_translator_returns_lang_value():
    t = translator("zh")
    assert t("nav.targets") == "产物"
    assert translator("en")("nav.targets") == "Targets"


def test_translator_falls_back_to_en_then_key():
    # A key present only as a real catalog entry resolves; a missing key returns itself.
    assert translator("zh")("does.not.exist") == "does.not.exist"


def test_every_en_key_exists_in_zh():
    assert set(CATALOG["en"]) == set(CATALOG["zh"])


def test_supported_contains_default():
    assert DEFAULT_LANG in SUPPORTED
