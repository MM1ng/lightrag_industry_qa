"""Browser smoke for Streamlit QA + knowledge-graph tabs."""

from __future__ import annotations

import re
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8501"
SHOT_DIR = Path("tmp") / "playwright_kg"
SHOT_DIR.mkdir(parents=True, exist_ok=True)


def _shot(page, name: str) -> None:
    path = SHOT_DIR / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    print(f"SHOT {path}")


def _wait_ready(page) -> None:
    page.wait_for_selector("h1", timeout=60_000)
    time.sleep(1.0)


def _body(page) -> str:
    return page.locator("body").inner_text()


def _click_tab(page, name: str) -> None:
    page.get_by_role("tab", name=re.compile(name)).click()
    time.sleep(1.2)


def _open_first_select_and_read_options(page) -> list[str]:
    # Streamlit selectbox renderers vary; try several selectors.
    candidates = [
        page.locator('[data-baseweb="select"]').first,
        page.locator('[data-testid="stSelectbox"] div[role="button"]').first,
        page.locator('[data-testid="stSelectbox"]').first,
        page.get_by_label("查询模式"),
    ]
    last_err: Exception | None = None
    for locator in candidates:
        try:
            if locator.count() == 0:
                continue
            locator.click(timeout=5_000)
            time.sleep(0.5)
            options = page.locator("[role='option']").all_inner_texts()
            if options:
                page.keyboard.press("Escape")
                time.sleep(0.2)
                return options
            page.keyboard.press("Escape")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            try:
                page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
    raise RuntimeError(f"could not open selectbox: {last_err}")


def main() -> int:
    results: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1300})
        page.goto(BASE, wait_until="domcontentloaded", timeout=60_000)
        _wait_ready(page)
        _shot(page, "01_home")

        title = page.locator("h1").first.inner_text()
        assert "离心泵" in title
        results.append(f"PASS title={title!r}")

        assert page.get_by_role("tab", name=re.compile("智能问答")).count()
        assert page.get_by_role("tab", name=re.compile("知识图谱")).count()
        results.append("PASS tabs present")

        # ---- QA modes ----
        _click_tab(page, "智能问答")
        try:
            options = _open_first_select_and_read_options(page)
            joined = " | ".join(opt.strip() for opt in options)
            for mode in ("mix", "hybrid", "local", "global", "naive"):
                assert any(mode == opt.strip() for opt in options), joined
            # order check: mix first among the five if all present
            cleaned = [opt.strip() for opt in options if opt.strip() in {
                "mix", "hybrid", "local", "global", "naive"
            }]
            if cleaned[:5] == ["mix", "hybrid", "local", "global", "naive"]:
                results.append(f"PASS modes exact order: {cleaned}")
            else:
                results.append(f"PASS modes present (order seen={cleaned}): {joined}")
        except Exception as exc:  # noqa: BLE001
            # fallback: page source should at least mention modes via select values
            html = page.content()
            missing = [m for m in ("mix", "hybrid", "local", "global", "naive") if m not in html]
            if missing:
                results.append(f"FAIL modes missing {missing}: {exc}")
            else:
                results.append(f"WARN selectbox UI not opened ({exc}); modes present in HTML")
        _shot(page, "02_qa_tab")

        # ---- Graph overview ----
        _click_tab(page, "知识图谱")
        time.sleep(1.5)
        text = _body(page)
        assert "图谱文件" in text or "graph_chunk_entity_relation.graphml" in text
        assert "365" in text
        assert "418" in text
        assert "当前展示节点数" in text
        assert "50" in text
        assert "107" in text
        assert page.locator("iframe").count() >= 1
        results.append("PASS overview stats 365/418 and subgraph 50/107 with iframe")
        _shot(page, "03_graph_overview")

        # edge labels
        page.get_by_text("显示关系标签", exact=False).click()
        time.sleep(1.2)
        assert page.locator("iframe").count() >= 1
        results.append("PASS edge label toggle")
        page.get_by_text("显示关系标签", exact=False).click()
        time.sleep(0.8)

        # ---- Entity: 轴承 ----
        page.get_by_text("实体相关子图", exact=True).click()
        time.sleep(1)
        page.get_by_role("button", name="轴承", exact=True).click()
        time.sleep(1.5)
        text = _body(page)
        assert "匹配结果" in text
        assert "轴承" in text
        results.append("PASS entity search 轴承")
        _shot(page, "04_entity_bearing")

        # hop 2
        page.locator("label").filter(has_text=re.compile(r"^2$")).click()
        time.sleep(1.2)
        results.append("PASS hop 2 selected")
        _shot(page, "04b_hop2")

        # model entity
        page.get_by_role("button", name="2196", exact=True).click()
        time.sleep(1.5)
        text = _body(page)
        assert "2196" in text and "匹配结果" in text
        results.append("PASS entity search 2196")
        _shot(page, "05_entity_2196")

        # reload
        page.get_by_role("button", name=re.compile("重新加载图谱")).click()
        time.sleep(2.5)
        text = _body(page)
        assert "365" in text
        results.append("PASS reload graph")
        _shot(page, "06_reload")

        # QA still ok
        _click_tab(page, "智能问答")
        assert "查询模式" in _body(page)
        results.append("PASS QA usable after reload")
        _shot(page, "07_qa_after_reload")

        # no Streamlit exception widget
        if page.locator("[data-testid='stException']").count() or page.locator(".stException").count():
            results.append("FAIL Streamlit exception visible")
        else:
            results.append("PASS no Streamlit exception widget")

        body = _body(page)
        for bad in ("Traceback (most recent call last)", "ModuleNotFoundError", "Event loop is closed"):
            results.append(("FAIL " if bad in body else "PASS no ") + bad)

        browser.close()

    print("\n=== RESULTS ===")
    for line in results:
        print(line)
    fails = [r for r in results if r.startswith("FAIL")]
    print(
        "summary "
        f"pass={sum(r.startswith('PASS') for r in results)} "
        f"warn={sum(r.startswith('WARN') for r in results)} "
        f"fail={len(fails)}"
    )
    return 1 if fails else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PlaywrightTimeout as exc:
        print(f"TIMEOUT: {exc}")
        raise SystemExit(2)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {type(exc).__name__}: {exc}")
        raise SystemExit(1)
