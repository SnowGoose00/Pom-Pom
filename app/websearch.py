"""Lightweight web search for game build/meta questions (no API key needed).

Sources are tried in order and the first non-empty one wins:
DuckDuckGo HTML (best relevance, rate limited) -> 360 搜索 (steady) -> 搜狗 (often blocked).
"""
from __future__ import annotations

import html as html_lib
import ipaddress
import os
import re
import socket
import time
from urllib.parse import unquote, urljoin, urlsplit

import httpx

BUILD_KEYWORDS = (
    "配装",
    "配队",
    "队友",
    "队伍",
    "最佳阵容",
    "阵容推荐",
    "组队",
    "遗器",
    "光锥",
    "武器推荐",
    "怎么培养",
    "如何培养",
    "怎么养",
    "养成",
    "攻略",
    "强度",
    "值得抽",
    "抽取建议",
    "星魂",
    "命座",
    "模拟宇宙",
    "混沌回忆",
    "虚构叙事",
    "末日幻影",
    "主词条",
    "副词条",
    "毕业面板",
    "速度阈值",
    "手法",
    "连招",
    "打法",
    "怎么打",
    "推荐阵容",
    "流派",
    "机制",
    "爆发",
)

BING_URL = "https://cn.bing.com/search"
SOGOU_URL = "https://www.sogou.com/web"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"


def is_build_question(question: str) -> bool:
    """Detect HSR build/combat/meta questions that need external guides."""
    return any(kw in question for kw in BUILD_KEYWORDS)


def build_web_query(question: str) -> str:
    """Add game context so search engines disambiguate HSR terms."""
    if any(tag in question for tag in ("星穹铁道", "星铁", "崩铁", "崩坏")):
        return question
    # "攻略/推荐" measurably lifts guide pages above fan-art/price pages.
    return f"{question} 崩坏星穹铁道 攻略 推荐"


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_bing_html(page: str, max_results: int = 5) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for block in re.findall(r'<li class="b_algo".*?</li>', page, re.S):
        link = re.search(
            r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S
        )
        if not link:
            continue
        title = _clean(link.group(2))
        if not title:
            continue
        snippet_match = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        snippet = _clean(snippet_match.group(1)) if snippet_match else ""
        results.append({"title": title, "url": link.group(1), "snippet": snippet})
        if len(results) >= max_results:
            break
    return results


def search_bing(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Search Bing and return result snippets; [] on any failure."""
    params = {"q": query, "setlang": "zh-hans"}
    try:
        resp = httpx.get(
            BING_URL,
            params=params,
            headers={"User-Agent": _UA},
            timeout=8.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return parse_bing_html(resp.text, max_results=max_results)
    except Exception:
        return []


def parse_sogou_html(page: str, max_results: int = 5) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    def add(url: str, title: str, snippet: str = "") -> bool:
        if not title or url in seen_urls:
            return False
        if url.startswith("/link"):
            url = "https://www.sogou.com" + url
        seen_urls.add(url)
        results.append({"title": title, "url": url, "snippet": snippet})
        return True

    # Classic organic results: <h3 class="vr-title"> + text-layout summary.
    for match in re.finditer(r'<h3 class="vr-title.*?</h3>', page, re.S):
        anchor = re.search(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', match.group(0), re.S)
        if not anchor:
            continue
        tail = page[match.end() : match.end() + 5000]
        snippet = ""
        space = re.search(r'<div class="fz-mid space-txt"[^>]*>(.*?)</div>', tail, re.S)
        if space:
            snippet = _clean(space.group(1))
        add(anchor.group(1), _clean(anchor.group(2)), snippet)
        if len(results) >= max_results:
            return results

    # Newer VR result cards (suplist), title-only.
    for match in re.finditer(
        r'<a href="(https?://[^"]+)"[^>]*>(?:<[^>]+>)*\s*<div class="suplist-pc__liTitle_[^"]+"[^>]*>(.*?)</div>',
        page,
        re.S,
    ):
        add(match.group(1), _clean(match.group(2)))
        if len(results) >= max_results:
            return results
    return results


def search_sogou(query: str, max_results: int = 5) -> list[dict[str, str]]:
    try:
        resp = httpx.get(
            SOGOU_URL,
            params={"query": query},
            headers={"User-Agent": _UA},
            timeout=8.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return parse_sogou_html(resp.text, max_results=max_results)
    except Exception:
        return []


DDG_URL = "https://html.duckduckgo.com/html/"
SO360_URL = "https://www.so.com/s"

_DDG_ITEM = re.compile(r'<a\b([^>]*class="result__a"[^>]*)>(.*?)</a>', re.S)
_DDG_SNIPPET = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)
_SO360_ITEM = re.compile(r'<h3\s+class="res-title">\s*<a\b([^>]*)>(.*?)</a>', re.S)
_SO360_SNIPPET = re.compile(r'class=[\'"]res-desc[\'"][^>]*>(.*?)</p>', re.S)


def _attr(tag: str, name: str) -> str:
    match = re.search(rf'{name}="([^"]*)"', tag)
    return match.group(1) if match else ""


def _ddg_target(href: str) -> str:
    """DuckDuckGo wraps hits in /l/?uddg=<encoded>; unwrap to the real URL."""
    if "uddg=" in href:
        match = re.search(r"uddg=([^&]+)", href)
        if match:
            return unquote(match.group(1))
    if href.startswith("//"):
        return "https:" + href
    return href


def parse_duckduckgo_html(page: str, max_results: int = 5) -> list[dict[str, str]]:
    """Parse html.duckduckgo.com results; [] when the page is a block/landing page."""
    results: list[dict[str, str]] = []
    for match in _DDG_ITEM.finditer(page):
        title = _clean(match.group(2))
        url = _ddg_target(_attr(match.group(1), "href"))
        if not title or not url:
            continue
        tail = page[match.end() : match.end() + 1500]
        snippet_match = _DDG_SNIPPET.search(tail)
        snippet = _clean(snippet_match.group(1)) if snippet_match else ""
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results


def search_duckduckgo(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """DuckDuckGo HTML endpoint; [] on failure or rate limiting."""
    try:
        resp = httpx.get(
            DDG_URL,
            params={"q": query},
            headers={"User-Agent": _UA},
            timeout=12.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return parse_duckduckgo_html(resp.text, max_results=max_results)
    except Exception:
        return []


def parse_so360_html(page: str, max_results: int = 5) -> list[dict[str, str]]:
    """Parse 360 搜索 results, preferring the real target over the /link redirect."""
    results: list[dict[str, str]] = []
    for match in _SO360_ITEM.finditer(page):
        tag = match.group(1)
        title = _clean(match.group(2))
        url = _attr(tag, "data-mdurl") or _attr(tag, "href")
        if not title or not url:
            continue
        tail = page[match.end() : match.end() + 3000]
        snippet_match = _SO360_SNIPPET.search(tail)
        snippet = _clean(snippet_match.group(1)) if snippet_match else ""
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results


def search_so360(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """360 搜索 HTML endpoint; [] on failure."""
    try:
        resp = httpx.get(
            SO360_URL,
            params={"q": query},
            headers={"User-Agent": _UA},
            timeout=12.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return parse_so360_html(resp.text, max_results=max_results)
    except Exception:
        return []


BOCHA_URL = "https://api.bochaai.com/v1/web-search"


def parse_bocha_json(payload: dict, max_results: int = 5) -> list[dict[str, str]]:
    """Map a Bocha (博查) web-search response into our result shape."""
    if not isinstance(payload, dict) or str(payload.get("code")) != "200":
        return []
    pages = ((payload.get("data") or {}).get("webPages") or {}).get("value") or []
    results: list[dict[str, str]] = []
    for page in pages:
        title = _clean(str(page.get("name") or ""))
        url = str(page.get("url") or "").strip()
        if not title or not url:
            continue
        snippet = _clean(str(page.get("summary") or page.get("snippet") or ""))
        results.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet[:400],
                "site": str(page.get("siteName") or ""),
            }
        )
        if len(results) >= max_results:
            break
    return results


def search_bocha(
    query: str, max_results: int = 5, api_key: str | None = None
) -> list[dict[str, str]]:
    """Bocha web search (API key required); [] when unconfigured or failing."""
    key = (api_key or os.getenv("BOCHA_API_KEY", "")).strip()
    if not key or not query:
        return []
    try:
        resp = httpx.post(
            BOCHA_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                "freshness": "noLimit",
                "summary": True,
                "count": max(1, min(max_results, 10)),
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        return parse_bocha_json(resp.json(), max_results=max_results)
    except Exception:
        return []


DEFAULT_SOURCES = ("bocha", "duckduckgo", "so360", "sogou")

SOURCE_LABELS = {
    "bocha": "博查攻略",
    "duckduckgo": "网络攻略",
    "so360": "网络攻略",
    "sogou": "网络攻略",
}

_SOURCE_FUNCTIONS = {
    "bocha": search_bocha,
    "duckduckgo": search_duckduckgo,
    "so360": search_so360,
    "sogou": search_sogou,
}

# Sources that only work when their API key is configured.
_SOURCE_API_KEYS = {"bocha": "BOCHA_API_KEY"}

# Per-source politeness: DuckDuckGo starts answering 202/landing pages after a few
# rapid hits, so it needs a long interval; the others are more tolerant.
_MIN_INTERVAL_SECONDS = {
    "bocha": 0.5,
    "duckduckgo": 12.0,
    "so360": 3.0,
    "sogou": 4.0,
}
_CACHE_TTL_SECONDS = 300.0
_EMPTY_CACHE_TTL_SECONDS = 45.0
# After an empty answer we assume the source is rate limiting us and leave it alone
# for a while instead of hammering it with every new query.
_EMPTY_COOLDOWN_SECONDS = 90.0
_EMPTY_COOLDOWN_BY_SOURCE = {"bocha": 15.0}

_LAST_CALL_AT: dict[str, float] = {}
_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_COOLDOWN_UNTIL: dict[str, float] = {}


def reset_search_state() -> None:
    """Clear throttle timestamps and the result cache (mostly for tests)."""
    _LAST_CALL_AT.clear()
    _CACHE.clear()
    _COOLDOWN_UNTIL.clear()


def _throttle(source: str, now, sleep) -> None:
    interval = _MIN_INTERVAL_SECONDS.get(source, 0.0)
    last = _LAST_CALL_AT.get(source, 0.0)
    wait = interval - (now() - last)
    if last and wait > 0:
        sleep(wait)
    _LAST_CALL_AT[source] = now()


def _cached(source: str, query: str, now) -> list[dict] | None:
    hit = _CACHE.get((source, query))
    if not hit:
        return None
    stored_at, results = hit
    ttl = _CACHE_TTL_SECONDS if results else _EMPTY_CACHE_TTL_SECONDS
    return results if now() - stored_at < ttl else None


def search_web(
    query: str,
    max_results: int = 5,
    sources: tuple[str, ...] | list[str] | None = None,
    now=time.time,
    sleep=time.sleep,
) -> list[dict[str, str]]:
    """Query the source chain and return the first non-empty result set.

    Every result is tagged with ``source`` so callers can show where it came from.
    """
    for source in tuple(sources or DEFAULT_SOURCES):
        fetch = _SOURCE_FUNCTIONS.get(source)
        if fetch is None:
            continue
        key_var = _SOURCE_API_KEYS.get(source)
        if key_var and not os.getenv(key_var, "").strip():
            continue
        if now() < _COOLDOWN_UNTIL.get(source, 0.0):
            continue
        cached = _cached(source, query, now)
        if cached is not None:
            if cached:
                return [dict(item) for item in cached]
            continue
        _throttle(source, now, sleep)
        try:
            results = list(fetch(query, max_results=max_results) or [])
        except Exception:
            results = []
        _CACHE[(source, query)] = (now(), results)
        if results:
            _COOLDOWN_UNTIL.pop(source, None)
            tagged = [dict(item, source=source) for item in results]
            _CACHE[(source, query)] = (now(), tagged)
            return [dict(item) for item in tagged]
        cooldown = _EMPTY_COOLDOWN_BY_SOURCE.get(source, _EMPTY_COOLDOWN_SECONDS)
        _COOLDOWN_UNTIL[source] = now() + cooldown
    return []


_BLOCKED_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")
_JS_REDIRECT = re.compile(r"location\.replace\(\s*[\"']([^\"']+)[\"']")
_META_REDIRECT = re.compile(r"URL=['\"]?([^'\"\s>]+)", re.I)


_MAX_REDIRECTS = 3
_MAX_PAGE_BYTES = 512_000
_TEXTUAL_TYPES = ("text/", "application/json", "application/xml", "application/xhtml")


def _resolve_public(host: str) -> str | None:
    """Resolve ``host`` and return one address to use, or None if it is not public.

    Every answer returned by the resolver must be a public address: if a name
    points at both a public and a private address, the whole name is refused.
    Going through the resolver also normalises the tricks that fool naive string
    checks (``2130706433``, ``0x7f000001``, ``[::1]`` all come back as addresses).
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        return None
    addresses: list[str] = []
    for info in infos:
        address = info[4][0].split("%")[0]  # drop any IPv6 scope id
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return None
        if not ip.is_global:
            return None
        addresses.append(address)
    return addresses[0] if addresses else None


def _is_public_http(url: str) -> bool:
    """Only allow http(s) URLs whose host resolves to a public address."""
    if not url:
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    return _resolve_public(parsed.hostname) is not None


def _redirect_target(page: str) -> str:
    """Search engines return a tiny JS/meta redirect page; extract its target."""
    match = _JS_REDIRECT.search(page) or _META_REDIRECT.search(page)
    target = match.group(1).strip() if match else ""
    return target if _is_public_http(target) else ""


def _read_limited(response, limit: int) -> bytes:
    """Read at most ``limit`` bytes and then stop pulling from the socket."""
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        chunks.append(chunk)
        total += len(chunk)
        if total >= limit:
            break
    return b"".join(chunks)[:limit]


def html_to_text(page: str) -> str:
    """Strip scripts/styles/tags from a page and return readable plain text."""
    page = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", page)
    page = re.sub(r"(?i)<br\s*/?>", "\n", page)
    page = re.sub(r"(?i)</(p|div|li|h[1-6])>", "\n", page)
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", page))
    lines = [
        re.sub(r"[ \t\u3000]+", " ", line).strip() for line in text.splitlines()
    ]
    return "\n".join(line for line in lines if line)


def fetch_page_text(url: str, max_chars: int = 4000, timeout: float = 10.0) -> str:
    """Fetch a public http(s) page and return its plain text; "" on any failure.

    Redirects are followed by hand so that **every** hop is re-checked: with
    ``follow_redirects=True`` a public URL can bounce the fetcher into the local
    network, which is what made the old address check useless.

    The body is streamed and cut off at ``_MAX_PAGE_BYTES`` and non-textual
    responses are skipped: fetching everything and truncating afterwards let a
    large page (or a compressed bomb) eat memory and a worker thread.
    """
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        if not _is_public_http(current):
            return ""
        try:
            with httpx.stream(
                "GET",
                current,
                headers={"User-Agent": _UA},
                timeout=timeout,
                follow_redirects=False,
            ) as resp:
                status = getattr(resp, "status_code", 200)
                if 300 <= status < 400:
                    location = (getattr(resp, "headers", None) or {}).get(
                        "location", ""
                    )
                    if not location:
                        return ""
                    current = urljoin(current, location)
                    continue
                resp.raise_for_status()
                content_type = (getattr(resp, "headers", None) or {}).get(
                    "content-type", ""
                )
                if content_type and not any(
                    kind in content_type.lower() for kind in _TEXTUAL_TYPES
                ):
                    return ""
                page = _read_limited(resp, _MAX_PAGE_BYTES).decode(
                    getattr(resp, "encoding", None) or "utf-8", errors="replace"
                )
        except Exception:
            return ""
        # Search engines return a tiny JS/meta redirect page instead of a 3xx.
        target = _redirect_target(page)
        if target:
            current = target
            continue
        return html_to_text(page)[:max_chars]
    return ""
