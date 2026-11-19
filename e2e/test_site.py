"""Playwright: site/index.html を開き、属性 → 14問 → 推定 → 報道まで操作する。

- コンソールのエラー・ページの例外がないこと
- 390px 幅で横にはみ出さないこと、スクリーンショットを e2e/screenshots/ に保存
- data/*.json が取れないとき（file:// で開いたとき）も、埋め込みの初期データで動くこと

実行: python -m pytest e2e  （事前に `python -m playwright install chromium`）
"""
import functools
import http.server
import pathlib
import threading

import pytest

pw = pytest.importorskip("playwright.sync_api")

SITE = pathlib.Path(__file__).resolve().parent.parent / "site"
SHOTS = pathlib.Path(__file__).resolve().parent / "screenshots"


@pytest.fixture(scope="module")
def server():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/index.html"
    httpd.shutdown()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def run_flow(page, url, shot_prefix=None):
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_load_state("networkidle")
    shot = (lambda name: page.screenshot(path=str(SHOTS / f"{shot_prefix}-{name}.png"), full_page=True)) if shot_prefix else (lambda n: None)
    shot("01-attr")

    # 01 属性
    page.get_by_role("button", name="女性", exact=True).click()
    page.get_by_role("button", name="30〜40代").click()
    page.get_by_role("button", name="働き方").click()
    page.get_by_role("button", name="お金・格差").click()
    page.get_by_role("button", name="孤立").click()            # 3つ目は選べない
    assert page.get_by_text("選べるのは2つまでです").is_visible()
    rows = page.locator("text=物価高で暮らしが苦しくなった").locator("xpath=..")
    rows.get_by_role("button", name="はい").click()
    page.get_by_role("button", name="質問へ進む →").click()

    # 02 質問（14問）
    assert page.get_by_text("1 / 14").is_visible()
    shot("02-question")
    for i in range(14):
        name = ["はい", "いいえ", "わからない"][i % 3]
        page.get_by_role("button", name=name, exact=True).click()

    # 03 推定
    assert page.get_by_text("前提：推定は「はい／いいえ」にもとづく仮説で、診断・評価・選別には用いない。").first.is_visible()
    assert page.get_by_role("img", name="6つの方略のレーダーチャート").is_visible()
    assert page.get_by_text("下向きの枝").is_visible()
    shot("03-result")

    # 04 報道
    page.get_by_role("button", name="04 報道").click()
    assert page.get_by_role("link", name="The GDELT Project").is_visible()
    assert page.get_by_text("日本の媒体と海外著名媒体").is_visible()
    assert page.locator("svg[aria-label*='直近7日の件数']").count() == 10
    assert page.get_by_text("● 雇用・職業による序列化").is_visible()   # 選んだ領域に対応する圧力の印
    shot("04-news")
    return errors


def no_hscroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def tap_targets_ok(page):
    small = page.evaluate("""[...document.querySelectorAll('button')]
        .map(b => b.getBoundingClientRect()).filter(r => r.width && (r.height < 44 || r.width < 44)).length""")
    return small == 0


def test_desktop_flow(browser, server):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = run_flow(page, server, "desktop")
    assert errors == []
    assert tap_targets_ok(page)


def test_mobile_390(browser, server):
    SHOTS.mkdir(exist_ok=True)
    page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    errors = run_flow(page, server, "mobile390")
    assert errors == []
    assert no_hscroll(page)
    page.get_by_role("button", name="03 推定").click()
    assert no_hscroll(page)
    page.get_by_role("button", name="01 属性").click()
    assert no_hscroll(page)
    assert tap_targets_ok(page)


def test_review_badge_and_fetch(browser, server):
    """site/data/news.json の review_required が表示に反映される（モックの応答で確かめる）。"""
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    case = {"kind": "事件", "symbol": "人物A", "title": "テストの事例", "summary": "s",
            "frames": [{"name": "テストの枠組み", "text": "t", "norm": "n", "places": ["職場"], "effect": "e",
                        "js": {"f": "observer", "t": "x"}}],
            "sources": [{"name": "example.jp（2026-11-18）", "url": "https://example.jp/1"}],
            "review_required": True, "generated_by": "groq:test-model"}
    reviewed = dict(case, title="確認済みの事例", review_required=False)
    import json
    page.route("**/data/news.json", lambda r: r.fulfill(content_type="application/json",
               body=json.dumps([{"date": "2026-11-18", "cases": [case, reviewed]}], ensure_ascii=False)))
    page.goto(server)
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name="04 報道").click()
    assert page.get_by_text("要確認 — LLM の下書き（groq:test-model）").count() == 1
    assert page.get_by_role("link", name="出典：example.jp（2026-11-18）").count() == 2


def test_embedded_fallback_without_server(browser):
    """file:// で開くと fetch が失敗するが、埋め込みの初期データで最後まで動く。"""
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = run_flow(page, (SITE / "index.html").as_uri())
    assert errors == []


def test_data_unavailable_uses_embedded(browser, server):
    """サーバーはあるが data/*.json が 404 のときも、埋め込みの初期データで動く。"""
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.route("**/data/*.json", lambda r: r.fulfill(status=404, body="not found"))
    errors = run_flow(page, server)
    assert [e for e in errors if "404" not in e] == []
