"""Static site tests.  (tests/test_build_site.py)

build_site.py is a rendering layer, so these check that data survives
templating and that the result is navigable: a page per entity, no broken
links, whys and serving details present, autoescaping on.

They render into tmp_path, so running the suite never overwrites the real
site/ directory.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from src.build_site import SiteBuilder
from src.engine import Engine
from src.export import Exporter

DB = "build/wine.db"
TEMPLATES = Path("templates")

import re
from html import unescape


def visible(text: str) -> str:
    """Rendered HTML as a reader sees it — entities decoded, whitespace
    collapsed. Assertions compare against this, not raw source, so
    autoescaping and line wrapping don't cause false failures."""
    return re.sub(r"\s+", " ", unescape(text)).strip()


@pytest.fixture(scope="module")
def engine() -> Engine:
    return Engine(DB)


@pytest.fixture(scope="module")
def con():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture(scope="module")
def site(engine, tmp_path_factory) -> Path:
    """One render shared by the whole module — building is the slow part."""
    out = tmp_path_factory.mktemp("site")
    Exporter(engine, out / "data").run()
    SiteBuilder(engine, out, TEMPLATES).run()
    return out


# --- completeness ---------------------------------------------------------


def test_a_page_per_entity(site, con):
    for table, folder in (
        ("wines", "wines"),
        ("food_tags", "foods"),
        ("regions", "regions"),
    ):
        for row in con.execute(f"SELECT id FROM {table}"):
            assert (
                site / folder / f"{row['id']}.html"
            ).exists(), f"missing {folder}/{row['id']}.html"


def test_index_pages_and_stylesheet_exist(site):
    for page in (
        "index.html",
        "wines.html",
        "foods.html",
        "regions.html",
        "flavours.html",
    ):
        assert (site / page).exists()
    assert (site / "assets" / "style.css").exists()


# --- content survives templating ------------------------------------------


def test_wine_pages_keep_their_whys(site, engine, con):
    """The product promise has to survive rendering."""
    for row in con.execute("SELECT id FROM wines"):
        detail = engine.wine_detail(row["id"])
        page = visible(
            (site / "wines" / f"{row['id']}.html").read_text(encoding="utf-8")
        )
        assert visible(detail.wine["name"]) in page
        for pairing in detail.pairs_with:
            assert visible(pairing["why"])[:30] in page, f"{row['id']} lost a why"


def test_wine_pages_show_serving_details(site, engine, con):
    """The 'what glass, how cold' use case."""
    for row in con.execute("SELECT id FROM wines"):
        detail = engine.wine_detail(row["id"])
        serving = detail.wine.get("serving") or {}
        if not serving.get("glass"):
            continue
        html = (site / "wines" / f"{row['id']}.html").read_text(encoding="utf-8")
        assert serving["glass"].replace("-", " ") in html


def test_scale_bars_render(site, engine, con):
    """Regression: macros imported with {% from %} don't see render
    context, which silently emptied the signature element."""
    checked = 0
    for row in con.execute("SELECT id FROM wines"):
        detail = engine.wine_detail(row["id"])
        if detail.wine.get("body") is None:
            continue
        html = (site / "wines" / f"{row['id']}.html").read_text(encoding="utf-8")
        assert 'class="step on"' in html, f"{row['id']} rendered no scale bars"
        assert "Body" in html
        checked += 1
    if not checked:
        pytest.skip("no wine has a body value yet")


def test_food_pages_rank_wines_like_the_engine(site, engine, con):
    for row in con.execute("SELECT id FROM food_tags"):
        ranked = engine.pair_food([row["id"]])
        html = (site / "foods" / f"{row['id']}.html").read_text(encoding="utf-8")
        positions = [html.find(f"wines/{w.wine_id}.html") for w in ranked]
        assert positions == sorted(
            positions
        ), f"{row['id']} lists wines out of rank order"


def test_region_pages_list_their_wines(site, engine, con):
    for row in con.execute("SELECT id FROM regions"):
        detail = engine.region_detail(row["id"])
        html = (site / "regions" / f"{row['id']}.html").read_text(encoding="utf-8")
        for wine in detail.wines:
            assert f'wines/{wine["id"]}.html' in html


# --- navigability ---------------------------------------------------------


def test_every_internal_link_resolves(site):
    """Relative paths must work from every depth — one bad ../ would
    break hundreds of pages at once."""
    broken = []
    for page in site.rglob("*.html"):
        for href in re.findall(r'href="([^"#]+)', page.read_text(encoding="utf-8")):
            if href.startswith(("http", "mailto:")):
                continue
            if not (page.parent / href).resolve().exists():
                broken.append(f"{page.relative_to(site)} -> {href}")
    assert not broken, "broken links: " + "; ".join(broken[:5])


def test_every_wine_is_reachable_from_the_index(site, con):
    """No orphan pages: browsing must reach everything without search."""
    index = (site / "wines.html").read_text(encoding="utf-8")
    for row in con.execute("SELECT id FROM wines"):
        assert f'wines/{row["id"]}.html' in index


def test_pages_are_valid_enough_html(site):
    for page in site.rglob("*.html"):
        html = page.read_text(encoding="utf-8")
        assert html.startswith("<!DOCTYPE html>")
        assert html.count("<html") == html.count("</html>") == 1
        assert "{{" not in html and "{%" not in html, f"{page.name}: unrendered Jinja"


def test_user_text_is_escaped(site, engine, con):
    """Autoescape on: a stray < or & in my notes must not break a page."""
    for row in con.execute("SELECT id FROM wines"):
        html = (site / "wines" / f"{row['id']}.html").read_text(encoding="utf-8")
        content = html.split("<main")[1].split("</main>")[0]
        assert "<script" not in content, "user text injected a script tag"


# --- search wiring (Week 6) -----------------------------------------------


def test_search_assets_are_shipped(site):
    assert (site / "assets" / "app.js").exists()
    assert (site / "data" / "search-docs.json").exists()


def test_every_page_loads_the_search_script(site):
    """SITE_ROOT must be correct at every depth or fetch() 404s."""
    for page in site.rglob("*.html"):
        html = page.read_text(encoding="utf-8")
        assert "assets/app.js" in html
        depth = len(page.relative_to(site).parts) - 1
        assert f'window.SITE_ROOT = "{"../" * depth}"' in html


def test_search_docs_reference_real_pages(site):
    """Every search hit must lead somewhere that exists."""
    import json

    docs = json.loads((site / "data" / "search-docs.json").read_text(encoding="utf-8"))
    folders = {"food": "foods", "wine": "wines", "region": "regions"}
    for doc in docs:
        target = site / folders[doc["kind"]] / f"{doc['ref']}.html"
        assert target.exists(), f"search points at missing page {target.name}"


def test_search_box_is_present_and_empty(site):
    html = (site / "index.html").read_text(encoding="utf-8")
    assert 'id="q"' in html and 'id="results"' in html


# --- progressive web app (Week 8) -----------------------------------------


def test_pwa_files_exist(site):
    assert (site / "manifest.webmanifest").exists()
    assert (
        site / "sw.js"
    ).exists(), "service worker must sit at the site root for scope"
    assert (site / "favicon.ico").exists()
    for size in (180, 192, 512):
        assert (site / "assets" / "icons" / f"icon-{size}.png").exists()


def test_manifest_is_valid_and_relative(site):
    import json

    m = json.loads((site / "manifest.webmanifest").read_text(encoding="utf-8"))
    for key in ("name", "short_name", "start_url", "display", "icons"):
        assert key in m
    assert m["display"] == "standalone"
    # a project site lives at /<repo>/ — absolute paths would 404
    assert not m["start_url"].startswith("/")
    for icon in m["icons"]:
        assert not icon["src"].startswith("/")
        assert (
            site / icon["src"]
        ).exists(), f"manifest points at missing {icon['src']}"


def test_precached_shell_urls_all_exist(site):
    """cache.addAll() rejects the whole install if ONE url 404s — that would
    silently break offline mode."""
    import json, re

    src = (site / "sw.js").read_text(encoding="utf-8")
    shell = json.loads(re.search(r"const SHELL = (\[.*?\]);", src, re.S).group(1))
    for url in shell:
        if url == "./":
            continue
        assert (site / url[2:]).exists(), f"precache lists missing {url}"


def test_service_worker_version_changes_with_data(site, engine, tmp_path):
    """A stale cache would keep serving old notes."""
    import re
    from src.build_site import SiteBuilder
    from src.export import Exporter

    first = re.search(r'VERSION = "(.*?)"', (site / "sw.js").read_text()).group(1)
    assert len(first) >= 8
    Exporter(engine, tmp_path / "data").run()
    SiteBuilder(engine, tmp_path, TEMPLATES).run()
    again = re.search(r'VERSION = "(.*?)"', (tmp_path / "sw.js").read_text()).group(1)
    assert first == again, "same data must give the same version (deterministic build)"


def test_every_page_declares_ios_install_metadata(site):
    for page in site.rglob("*.html"):
        html = page.read_text(encoding="utf-8")
        assert "apple-touch-icon" in html
        assert "manifest.webmanifest" in html
        assert 'name="theme-color"' in html
