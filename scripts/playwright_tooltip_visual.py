"""Verify stripped descriptions and capture a readable tooltip screenshot."""

from __future__ import annotations

import re
import time
from pathlib import Path

import networkx as nx
from industrial_rag import graph_visualizer as gv
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8501"
SHOT = Path("tmp") / "playwright_kg"
SHOT.mkdir(parents=True, exist_ok=True)


def check_data_layer() -> None:
    graph = nx.read_graphml("lightrag_storage/graph_chunk_entity_relation.graphml")
    node = "Pump Shaft"
    raw = str(graph.nodes[node].get("description", ""))
    clean = gv.get_node_description(graph.nodes[node])
    tip = gv.build_node_tooltip(node, graph.nodes[node])
    print("RAW_HAS_SEP", "<SEP>" in raw)
    print("CLEAN", clean[:220])
    print("CLEAN_HAS_SEP", "<SEP>" in clean or "&lt;SEP&gt;" in clean)
    print("CLEAN_HAS_TAG", bool(re.search(r"<[^>]+>", clean)))
    print("TIP_HAS_SEP", "<SEP>" in tip or "&lt;SEP&gt;" in tip)
    print("TIP_HAS_RAW_TAG", bool(re.search(r"<(?!/?(div|span)\b)[^>]+>", tip)))
    sub = gv.build_neighborhood_subgraph(graph, node, hops=1, max_nodes=20)
    html = gv.render_pyvis_html(sub)
    print("HTML_HAS_ESCAPED_SEP", "&lt;SEP&gt;" in html)
    print("HTML_HAS_RAW_SEP", "<SEP>" in html)
    print("HTML_HAS_DARK", "#111827" in html)
    # title payload should include cleaned description fragments
    assert "Pump Shaft" in html
    assert "&lt;SEP&gt;" not in html
    assert "<SEP>" not in html


def capture_browser() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=False, slow_mo=80)
        page = browser.new_page(viewport={"width": 1440, "height": 1200})
        page.goto(BASE, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_selector("h1", timeout=60_000)
        page.get_by_role("tab", name=re.compile("知识图谱")).click()
        time.sleep(1.5)
        page.get_by_text("实体相关子图", exact=True).click()
        time.sleep(0.6)
        # fill search
        search = page.locator("input").filter(has_not=page.locator("[type=hidden]")).first
        # better: the text input under 实体搜索
        inputs = page.locator('[data-testid="stTextInput"] input')
        if inputs.count():
            inputs.first.fill("Pump Shaft")
        else:
            page.get_by_role("textbox").first.fill("Pump Shaft")
        time.sleep(1.5)
        page.screenshot(path=str(SHOT / "13_entity_selected.png"), full_page=True)

        # scroll graph into view
        iframe = page.locator("iframe").first
        iframe.scroll_into_view_if_needed()
        time.sleep(1.0)
        box = iframe.bounding_box()
        assert box is not None, "graph iframe missing"
        print("IFRAME", box)

        # click several points inside iframe to trigger tooltip
        found = []
        for xr, yr in (
            (0.50, 0.50),
            (0.42, 0.48),
            (0.58, 0.52),
            (0.50, 0.38),
            (0.50, 0.62),
            (0.35, 0.50),
            (0.65, 0.50),
        ):
            x = box["x"] + box["width"] * xr
            y = box["y"] + box["height"] * yr
            page.mouse.move(x, y)
            time.sleep(0.35)
            page.mouse.click(x, y)
            time.sleep(0.7)
            for fr in page.frames:
                try:
                    tips = fr.locator("div.vis-tooltip")
                    for i in range(tips.count()):
                        el = tips.nth(i)
                        text = el.inner_text().strip()
                        visible = el.is_visible()
                        if text:
                            style = el.evaluate(
                                "e => getComputedStyle(e).backgroundColor + '|' + getComputedStyle(e).color"
                            )
                            found.append({"text": text, "visible": visible, "style": style})
                            print("TIP", style, text[:240].replace("\n", " | "))
                except Exception as exc:  # noqa: BLE001
                    print("tip err", exc)
            if found:
                page.screenshot(path=str(SHOT / "14_tooltip_capture.png"), full_page=False)
                break

        if not found:
            # fallback screenshot of graph region
            page.screenshot(
                path=str(SHOT / "14_tooltip_capture.png"),
                clip={
                    "x": max(0, box["x"] - 20),
                    "y": max(0, box["y"] - 20),
                    "width": min(box["width"] + 40, 1400),
                    "height": min(box["height"] + 40, 800),
                },
            )
            print("NO_VISIBLE_TOOLTIP_TEXT")
        else:
            sample = found[0]["text"]
            for bad in ("<SEP>", "&lt;SEP&gt;", "<b>", "</b>", "<script>"):
                print(f"BAD_{bad}", bad in sample)
            print("STYLE", found[0]["style"])

        print("Keeping browser open 45s...")
        time.sleep(45)
        browser.close()


if __name__ == "__main__":
    check_data_layer()
    print("==== browser ====")
    capture_browser()
