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
expect = pw.expect

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
    # ニュース（最初の画面）
    expect(page.get_by_role("heading", name="ニュースが広める「ふつう」を読む。")).to_be_visible()
    expect(page.get_by_role("link", name="The GDELT Project")).to_be_visible()
    page.get_by_text("日本と海外のニュースをくらべる").click()          # 開く
    expect(page.get_by_text("目安として見てください", exact=False)).to_be_visible()
    expect(page.locator("svg[aria-label*='この7日の記事の数']")).to_have_count(10)
    expect(page.get_by_text("今日の入れかえ：", exact=False)).to_be_visible()
    shot("01-news")
    page.get_by_role("button", name="立場を選んで考えてみる →").click()

    # 立場
    expect(page.get_by_role("img", name="ニュース・立場・質問・結果の順にめぐり", exact=False)).to_be_visible()
    shot("02-attr")
    page.get_by_role("button", name="女性", exact=True).click()
    page.get_by_role("button", name="30〜40代").click()
    page.get_by_role("button", name="働き方").click()
    page.get_by_role("button", name="お金", exact=True).click()
    page.get_by_role("button", name="ひとりぼっち").click()      # 3つ目は選べない
    expect(page.get_by_text("選べるのは2つまでです")).to_be_visible()
    expect(page.get_by_text("お金のこと", exact=False)).to_have_count(0)          # お金の2問は削除
    page.get_by_role("button", name="質問へ進む →").click()

    # 質問（14問）
    expect(page.get_by_text("1 / 14")).to_be_visible()
    expect(page.get_by_text("思いうかべた人（あなた自身でも、身近な人でも）に、あてはまりますか？")).to_be_visible()
    expect(page.get_by_text("この人は", exact=False)).to_have_count(0)
    shot("03-question")
    for i in range(14):
        name = ["はい", "いいえ", "わからない"][i % 3]
        page.get_by_role("button", name=name, exact=True).click()

    # 結果
    expect(page.get_by_text("注意：この結果は「はい／いいえ」の答えから考えた予想です。", exact=False).first).to_be_visible()
    expect(page.get_by_role("img", name="6つのかわし方のレーダーチャート")).to_be_visible()
    expect(page.get_by_text("お金の面での位置")).to_have_count(0)
    shot("04-result")

    # 結果からニュースにもどる（ループ）と、選んだ分野に近いテーマに印が付く
    page.get_by_role("button", name="ニュースにもどる ↻").click()
    expect(page.get_by_role("heading", name="ニュースが広める「ふつう」を読む。")).to_be_visible()
    expect(page.get_by_text("● 働き方による上下の見方")).to_be_visible()
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
    tops = page.evaluate("[...document.querySelectorAll('nav button')].map(b => Math.round(b.getBoundingClientRect().top))")
    assert len(set(tops)) == 1, tops                      # 手順のボタンが1行に並ぶ
    for step in ("結果", "立場", "質問"):
        page.get_by_role("button", name=step, exact=True).click()
        assert no_hscroll(page), step
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
    expect(page.get_by_text("確認待ち — AIが書いた下書き（groq:test-model）")).to_have_count(1)
    expect(page.get_by_role("link", name="元の記事：example.jp（2026-11-18）")).to_have_count(2)


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


def test_seo_without_javascript(browser, server):
    """JavaScript を動かさない検索エンジンにも、タイトル・説明・本文・構造化データが読める。"""
    import json
    ctx = browser.new_context(java_script_enabled=False)
    page = ctx.new_page()
    page.goto(server)
    assert "ニュースで見る社会のプレッシャー" in page.title()
    assert len(page.locator('meta[name="description"]').get_attribute("content")) > 50
    assert page.locator('link[rel="canonical"]').get_attribute("href").startswith("https://")
    assert page.locator('meta[property="og:image"]').get_attribute("content").endswith("og.png")
    ld = json.loads(page.locator('script[type="application/ld+json"]').inner_text())
    assert {g["@type"] for g in ld["@graph"]} == {"WebSite", "WebApplication", "Dataset"}
    expect(page.get_by_role("heading", level=1)).to_have_count(1)
    expect(page.locator(".static li")).to_have_count(10)
    ctx.close()


def test_single_h1_and_headings_with_js(browser, server):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(server)
    page.wait_for_load_state("networkidle")
    expect(page.locator("h1")).to_have_count(1)
    expect(page.locator(".static")).to_have_count(0)          # JS が動くと静的な本文は置きかわる


def test_reduced_motion_flow(browser, server):
    """動きを減らす設定でも、同じ流れで最後まで動く（アニメーションなし）。"""
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    page = ctx.new_page()
    errors = run_flow(page, server)
    assert errors == []
    ctx.close()


def test_loop_nav_has_no_numbers(browser, server):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(server)
    page.wait_for_load_state("networkidle")
    labels = page.evaluate("[...document.querySelectorAll('nav button')].map(b => b.textContent.trim())")
    assert labels == ["ニュース", "立場", "質問", "結果"]
    assert page.evaluate("[...document.querySelectorAll('nav .arw')].map(a => a.textContent)") == ["→", "→", "→", "↻"]
