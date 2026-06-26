from types import SimpleNamespace

from fastapi.testclient import TestClient

from kairo.web.i18n import CATALOG, DEFAULT_LANG, SUPPORTED, resolve_lang, translator
from kairo.web.server import create_app


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


def _client(root):
    return TestClient(create_app(root))


def test_html_lang_defaults_to_en(tmp_path):
    r = _client(tmp_path).get("/")
    assert '<html lang="en">' in r.text


def test_html_lang_follows_accept_language(tmp_path):
    r = _client(tmp_path).get("/", headers={"Accept-Language": "zh-CN,zh;q=0.9"})
    assert '<html lang="zh">' in r.text


def test_set_lang_sets_cookie_and_redirects(tmp_path):
    c = _client(tmp_path)
    r = c.get("/set-lang/zh", headers={"Referer": "/"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert c.cookies.get("lang") == "zh"


def test_set_lang_ignores_unknown_code(tmp_path):
    c = _client(tmp_path)
    r = c.get("/set-lang/fr", follow_redirects=False)
    assert r.status_code == 303
    assert c.cookies.get("lang") is None


def test_cookie_lang_drives_html_lang(tmp_path):
    c = _client(tmp_path)
    c.cookies.set("lang", "zh")
    r = c.get("/")
    assert '<html lang="zh">' in r.text


def test_language_toggle_present(tmp_path):
    r = _client(tmp_path).get("/")
    assert 'href="/set-lang/en"' in r.text and 'href="/set-lang/zh"' in r.text


def test_chrome_translates_under_zh(tmp_path):
    c = _client(tmp_path)
    en = c.get("/").text
    assert "New workspace" in en
    zh = c.get("/", headers={"Accept-Language": "zh-CN"}).text
    assert "新建 workspace" in zh


def test_create_workspace_error_is_localized(tmp_path):
    c = _client(tmp_path)
    r_en = c.post("/workspaces", data={"topic": ""})
    assert r_en.status_code == 400 and "Topic cannot be empty" in r_en.text
    r_zh = c.post("/workspaces", data={"topic": ""}, headers={"Accept-Language": "zh-CN"})
    assert r_zh.status_code == 400 and "topic 不能为空" in r_zh.text
