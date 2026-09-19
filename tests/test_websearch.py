import app.websearch as app_websearch
from app.websearch import (
    build_web_query,
    fetch_page_text,
    html_to_text,
    is_build_question,
    parse_bing_html,
    parse_sogou_html,
)
from app.websearch import parse_duckduckgo_html, parse_so360_html
from app.websearch import parse_bocha_json, reset_search_state, search_web


def test_is_build_question_detects_build_meta_questions():
    assert is_build_question("银狼如何配装？")
    assert is_build_question("流萤的遗器主词条怎么选？")
    assert is_build_question("有哪些配队适合忘归人？")
    assert is_build_question("黄泉值得抽吗？")
    assert is_build_question("姬子目前最佳队友是谁？")
    assert is_build_question("流萤适合什么队伍？")


def test_is_build_question_ignores_plot_and_real_world():
    assert not is_build_question("黑塔空间站是谁建造的？")
    assert not is_build_question("帕姆和姬子是什么关系？")
    assert not is_build_question("今天上海天气怎么样？")


def test_parse_bing_html_extracts_results():
    html = """
    <html><body>
      <li class="b_algo">
        <h2><a href="https://example.com/guide">银狼配装攻略</a></h2>
        <p>推荐遗器：盗匪荒漠的废土客，主词条选速度与效果命中。</p>
      </li>
      <li class="b_algo">
        <h2><a href="https://example.com/team">银狼配队推荐</a></h2>
        <p>与黄泉、花火组成终结技队。</p>
      </li>
    </body></html>
    """
    results = parse_bing_html(html)
    assert len(results) == 2
    assert results[0]["title"] == "银狼配装攻略"
    assert results[0]["url"] == "https://example.com/guide"
    assert "效果命中" in results[0]["snippet"]
    assert results[1]["title"] == "银狼配队推荐"


def test_build_web_query_adds_game_context():
    assert "星穹铁道" in build_web_query("银狼如何配装？")
    assert build_web_query("崩铁 银狼配装") == "崩铁 银狼配装"


def test_build_web_query_asks_for_guides():
    query = build_web_query("银狼的遗器主词条怎么选？")
    assert "攻略" in query and "推荐" in query  # 排序明显更好（实测）


def test_parse_sogou_html_extracts_direct_results():
    html = """
    <html><body>
      <div class="struct201102">
        <h3 class="vr-title"><a target="_blank" href="/link?url=abc">银狼最强培养攻略</a></h3>
        <div class="text-layout"><div class="fz-mid space-txt">推荐遗器选效果命中与速度。</div></div>
      </div>
      <h3 class="vr-title"><a href="/link?url=dup">银狼最强培养攻略</a></h3>
    </body></html>
    """
    results = parse_sogou_html(html)
    assert len(results) == 2
    assert results[0]["title"] == "银狼最强培养攻略"
    assert results[0]["url"].startswith("https://www.sogou.com/link")
    assert "效果命中" in results[0]["snippet"]
    assert results[1]["url"] == "https://www.sogou.com/link?url=dup"


def test_html_to_text_strips_scripts_and_tags():
    html = (
        "<html><head><style>p{color:red}</style><script>var a=1;</script></head>"
        "<body><h1>银狼配装</h1><p>推荐遗器：<b>废土客</b>。</p><p>速度 134 起。</p></body></html>"
    )
    text = html_to_text(html)
    assert "银狼配装" in text
    assert "废土客" in text
    assert "速度 134 起" in text
    assert "var a=1" not in text
    assert "color:red" not in text
    assert "<" not in text


class _StreamBody:
    """Adapts the simple fakes below to the streaming response API."""

    def __init__(self, inner):
        self._inner = inner

    @property
    def status_code(self):
        return getattr(self._inner, "status_code", 200)

    @property
    def headers(self):
        return getattr(self._inner, "headers", {}) or {}

    @property
    def encoding(self):
        return "utf-8"

    def raise_for_status(self):
        return self._inner.raise_for_status()

    def iter_bytes(self):
        if hasattr(self._inner, "iter_bytes"):
            yield from self._inner.iter_bytes()
        else:
            yield self._inner.text.encode("utf-8")


class _StreamContext:
    def __init__(self, inner):
        self._inner = inner

    def __enter__(self):
        return _StreamBody(self._inner)

    def __exit__(self, *exc):
        return False


def _patch_stream(monkeypatch, responder):
    """Make app.websearch fetch through a fake, keeping the streaming API."""
    def forbidden(*args, **kwargs):
        raise AssertionError("fetch_page_text 必须用 httpx.stream 限流读取")

    monkeypatch.setattr("app.websearch.httpx.get", forbidden)
    monkeypatch.setattr(
        "app.websearch.httpx.stream",
        lambda method, url, **kwargs: _StreamContext(responder(url, **kwargs)),
    )


def test_fetch_page_text_truncates_and_rejects_local_urls(monkeypatch):
    class Resp:
        text = "<html><body><p>" + "长" * 500 + "</p></body></html>"

        def raise_for_status(self):
            return None

    _patch_stream(monkeypatch, lambda *a, **k: Resp())
    monkeypatch.setattr(
        "app.websearch._resolve_public",
        lambda host: "93.184.216.34" if host.endswith("example.com") else None,
    )
    assert len(fetch_page_text("https://example.com/a", max_chars=50)) == 50
    assert fetch_page_text("http://127.0.0.1:8000/") == ""
    assert fetch_page_text("http://localhost/admin") == ""
    assert fetch_page_text("file:///c:/secret.txt") == ""


def test_fetch_page_text_refuses_every_kind_of_internal_address(monkeypatch):
    """Hostnames that *look* public but resolve inward must be rejected."""

    class Resp:
        text = "<html><body><p>内网内容</p></body></html>"

        def raise_for_status(self):
            return None

    _patch_stream(monkeypatch, lambda *a, **k: Resp())
    for url in (
        "http://2130706433/",  # loopback written as a decimal integer
        "http://0x7f000001/",  # loopback written in hex
        "http://[::1]/",  # IPv6 loopback
        "http://192.0.2.10/",  # 文档保留段（同样不是公网地址）
        "http://203.0.113.7/",  # 文档保留段
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://0.0.0.0/",
    ):
        assert fetch_page_text(url) == "", url


def test_fetch_page_text_refuses_a_redirect_into_the_local_network(monkeypatch):
    calls = []

    class Redirect:
        status_code = 302
        headers = {"location": "http://169.254.169.254/latest/meta-data/"}
        text = ""

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        calls.append(url)
        return Redirect()

    _patch_stream(monkeypatch, fake_get)
    monkeypatch.setattr(
        "app.websearch._resolve_public",
        lambda host: "93.184.216.34" if host == "example.com" else None,
    )

    assert fetch_page_text("https://example.com/x") == ""
    assert calls == ["https://example.com/x"], "不能真的去请求私网地址"


def test_fetch_page_text_returns_empty_on_error(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    _patch_stream(monkeypatch, boom)
    assert fetch_page_text("https://example.com/x") == ""


def test_fetch_page_text_stops_reading_a_huge_body(monkeypatch):
    """The byte cap must stop the download, not just truncate the string."""
    consumed = {"chunks": 0}

    class Huge:
        status_code = 200
        headers = {"content-type": "text/html"}

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            for _ in range(10_000):
                consumed["chunks"] += 1
                yield b"x" * 4096

    _patch_stream(monkeypatch, lambda *a, **k: Huge())
    monkeypatch.setattr("app.websearch._resolve_public", lambda host: "93.184.216.34")

    text = fetch_page_text("https://example.com/huge", max_chars=20)

    assert text == "x" * 20, "内容应当来自假响应"
    assert consumed["chunks"] < 200, f"应当提前停止读取，实际读了 {consumed['chunks']} 块"


def test_fetch_page_text_refuses_a_binary_body(monkeypatch):
    class Binary:
        status_code = 200
        headers = {"content-type": "application/pdf"}

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"%PDF-1.4 not text"

    _patch_stream(monkeypatch, lambda *a, **k: Binary())
    monkeypatch.setattr("app.websearch._resolve_public", lambda host: "93.184.216.34")

    assert fetch_page_text("https://example.com/a.pdf") == ""


def test_fetch_page_text_follows_js_redirect_from_search_engines(monkeypatch):
    calls = []

    class RedirectPage:
        text = (
            '<meta content="always" name="referrer">'
            '<script>window.location.replace("https://shouyou.example.com/gl/1.html")'
            "</script>"
        )

        def raise_for_status(self):
            return None

    class RealPage:
        text = "<html><body><p>真正的攻略正文：躯干选效果命中。</p></body></html>"

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        calls.append(url)
        return RedirectPage() if "sogou.com/link" in url else RealPage()

    _patch_stream(monkeypatch, fake_get)
    monkeypatch.setattr("app.websearch._resolve_public", lambda host: "93.184.216.34")
    text = fetch_page_text("https://www.sogou.com/link?url=abc")

    assert "真正的攻略正文" in text
    assert calls == [
        "https://www.sogou.com/link?url=abc",
        "https://shouyou.example.com/gl/1.html",
    ]


DUCKDUCKGO_HTML = """
<html><body>
  <div class="result results_links results_links_deep web-result">
    <a rel="nofollow" class="result__a"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.miyoushe.com%2Fsr%2Farticle%2F1&amp;rut=abc">
      【V4.2攻略】「银狼 遗器」主副词条该怎么选？
    </a>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=x">躯干选效果命中，脚部速度优先。</a>
  </div>
  <div class="result results_links">
    <a rel="nofollow" class="result__a" href="https://www.bilibili.com/video/BV1">银狼LV.999成品遗器鉴赏</a>
    <a class="result__snippet" href="https://x">速度大于一切，暴击 80+。</a>
  </div>
</body></html>
"""


def test_parse_duckduckgo_html_decodes_urls_and_snippets():
    results = parse_duckduckgo_html(DUCKDUCKGO_HTML)
    assert len(results) == 2
    assert results[0]["title"].startswith("【V4.2攻略】")
    assert results[0]["url"] == "https://www.miyoushe.com/sr/article/1"
    assert "效果命中" in results[0]["snippet"]
    assert results[1]["url"] == "https://www.bilibili.com/video/BV1"


def test_parse_duckduckgo_html_returns_empty_on_blocked_page():
    assert parse_duckduckgo_html("<html><body>DuckDuckGo</body></html>") == []


SO360_HTML = """
<html><body>
  <li class="res-list"><h3   class="res-title"><a href="https://www.so.com/link?m=abc"
     data-mdurl="https://ol.3dmgame.com/gl/234696.html" rel="noopener" target="_blank">
     崩坏星穹铁道<em>银狼遗器词条</em>怎么选择_3DM网游</a></h3>
     <div class='res-rich so-rich'><p class="res-desc">躯干优先效果命中，脚部速度。</p></div></li>
  <li class="res-list"><h3   class="res-title"><a href="https://www.so.com/link?m=def"
     data-mdurl="https://www.9game.cn/sr/1.html" rel="noopener" target="_blank">
     崩坏星穹铁道银狼遗器及词条搭配推荐_九游</a></h3></li>
</body></html>
"""


def test_parse_so360_html_prefers_real_url_and_reads_snippet():
    results = parse_so360_html(SO360_HTML)
    assert len(results) == 2
    assert results[0]["url"] == "https://ol.3dmgame.com/gl/234696.html"
    assert "银狼遗器词条" in results[0]["title"]
    assert "<em>" not in results[0]["title"]
    assert "效果命中" in results[0]["snippet"]
    assert results[1]["url"] == "https://www.9game.cn/sr/1.html"


def test_search_web_uses_first_non_empty_source_and_tags_it(monkeypatch):
    reset_search_state()
    calls = []

    def empty(query, max_results=5):
        calls.append(("duckduckgo", query))
        return []

    def working(query, max_results=5):
        calls.append(("so360", query))
        return [{"title": "银狼遗器词条选择", "url": "https://x/1", "snippet": "效果命中"}]

    monkeypatch.setitem(app_websearch._SOURCE_FUNCTIONS, "duckduckgo", empty)
    monkeypatch.setitem(app_websearch._SOURCE_FUNCTIONS, "so360", working)

    results = search_web("银狼 遗器", sources=("duckduckgo", "so360"))

    assert [name for name, _ in calls] == ["duckduckgo", "so360"]
    assert results[0]["source"] == "so360"
    assert results[0]["title"] == "银狼遗器词条选择"


def test_search_web_returns_empty_when_all_sources_fail(monkeypatch):
    reset_search_state()
    monkeypatch.setitem(app_websearch._SOURCE_FUNCTIONS, "duckduckgo", lambda q, max_results=5: [])
    monkeypatch.setitem(app_websearch._SOURCE_FUNCTIONS, "so360", lambda q, max_results=5: [])
    assert search_web("银狼 遗器", sources=("duckduckgo", "so360")) == []


def test_search_web_caches_repeated_queries(monkeypatch):
    reset_search_state()
    hits = []

    def working(query, max_results=5):
        hits.append(query)
        return [{"title": "T", "url": "https://x/1", "snippet": "s"}]

    monkeypatch.setitem(app_websearch._SOURCE_FUNCTIONS, "duckduckgo", working)

    first = search_web("银狼 遗器", sources=("duckduckgo",), now=lambda: 1000.0)
    second = search_web("银狼 遗器", sources=("duckduckgo",), now=lambda: 1001.0)

    assert hits == ["银狼 遗器"]
    assert second == first


def test_search_web_throttles_the_same_source(monkeypatch):
    reset_search_state()
    slept = []
    monkeypatch.setitem(
        app_websearch._SOURCE_FUNCTIONS,
        "duckduckgo",
        lambda q, max_results=5: [{"title": "T", "url": "https://x", "snippet": ""}],
    )
    monkeypatch.setitem(app_websearch._MIN_INTERVAL_SECONDS, "duckduckgo", 10.0)

    search_web("问题一", sources=("duckduckgo",), now=lambda: 100.0, sleep=slept.append)
    search_web("问题二", sources=("duckduckgo",), now=lambda: 103.0, sleep=slept.append)

    assert slept == [7.0]  # waited out the remaining 7s of the 10s interval


def test_search_web_cools_down_a_source_after_an_empty_result(monkeypatch):
    reset_search_state()
    calls = []

    def empty(query, max_results=5):
        calls.append(query)
        return []

    monkeypatch.setitem(app_websearch._SOURCE_FUNCTIONS, "duckduckgo", empty)

    assert search_web("问题一", sources=("duckduckgo",), now=lambda: 100.0) == []
    assert search_web("问题二", sources=("duckduckgo",), now=lambda: 110.0) == []
    assert calls == ["问题一"]  # cooling down, not hammered again

    assert search_web("问题三", sources=("duckduckgo",), now=lambda: 300.0) == []
    assert calls == ["问题一", "问题三"]  # cooldown expired, retried


BOCHA_PAYLOAD = {
    "code": 200,
    "log_id": "abc",
    "msg": None,
    "data": {
        "_type": "SearchResponse",
        "webPages": {
            "value": [
                {
                    "name": "崩坏星穹铁道银狼光锥遗器推荐一览",
                    "url": "https://www.wishdown.com/article/1",
                    "snippet": "躯干效果命中，脚部速度。",
                    "summary": "银狼遗器：躯干优先效果命中，脚部选速度，位面球量子伤害加成。",
                    "siteName": "心愿游戏",
                    "datePublished": "2025-08-01T00:00:00Z",
                },
                {
                    "name": "TapTap 银狼遗器选择详细介绍",
                    "url": "https://www.taptap.cn/moment/2",
                    "snippet": "副词条优先效果命中与速度。",
                    "summary": "",
                    "siteName": "TapTap",
                },
                {"name": "", "url": "https://example.com/skip"},
            ]
        },
    },
}


def test_parse_bocha_json_maps_results_and_skips_empty_titles():
    results = parse_bocha_json(BOCHA_PAYLOAD)
    assert len(results) == 2
    assert results[0]["title"] == "崩坏星穹铁道银狼光锥遗器推荐一览"
    assert results[0]["url"] == "https://www.wishdown.com/article/1"
    assert "效果命中" in results[0]["snippet"]
    assert results[1]["snippet"] == "副词条优先效果命中与速度。"  # falls back to snippet


def test_parse_bocha_json_handles_error_payloads():
    assert parse_bocha_json({"code": 403, "msg": "forbidden"}) == []
    assert parse_bocha_json({}) == []


def test_search_web_prefers_bocha_and_tags_the_source(monkeypatch):
    reset_search_state()
    monkeypatch.setenv("BOCHA_API_KEY", "test-key")
    monkeypatch.setitem(
        app_websearch._SOURCE_FUNCTIONS,
        "bocha",
        lambda query, max_results=5: [
            {
                "title": "崩坏星穹铁道银狼光锥遗器推荐一览",
                "url": "https://www.wishdown.com/article/1",
                "snippet": "躯干效果优先命中",
            }
        ],
    )

    results = search_web("银狼 遗器", sources=("bocha", "duckduckgo"))

    assert results[0]["source"] == "bocha"
    assert results[0]["title"].startswith("崩坏星穹铁道")


def test_search_web_skips_bocha_without_api_key(monkeypatch):
    reset_search_state()
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    called = []
    monkeypatch.setitem(
        app_websearch._SOURCE_FUNCTIONS,
        "bocha",
        lambda query, max_results=5: called.append(query) or [],
    )
    monkeypatch.setitem(
        app_websearch._SOURCE_FUNCTIONS,
        "duckduckgo",
        lambda query, max_results=5: [{"title": "T", "url": "https://x", "snippet": ""}],
    )

    results = search_web("银狼 遗器", sources=("bocha", "duckduckgo"))

    assert called == []  # bocha not even attempted without a key
    assert results[0]["source"] == "duckduckgo"
