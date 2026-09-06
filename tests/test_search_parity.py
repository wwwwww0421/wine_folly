"""Browser/CLI parity.  (tests/test_search_parity.py)

README §5.3 promises the app and the engine never disagree. app.js does
the multi-tag merge client-side, so that arithmetic is duplicated in two
languages — exactly the kind of duplication that silently drifts.

These tests run the REAL app.js in Node against the REAL exported JSON
and compare with engine.pair_food(). If someone tweaks the scoring in one
language and not the other, this goes red.

Skips cleanly when node isn't installed.
"""

from __future__ import annotations

import itertools
import json
import shutil
import sqlite3
import subprocess
import textwrap
from pathlib import Path

import pytest

from src.engine import Engine, normalise

DB = "build/wine.db"
APP_JS = Path("templates/assets/app.js").resolve()

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


@pytest.fixture(scope="module")
def engine() -> Engine:
    return Engine(DB)


@pytest.fixture(scope="module")
def exported(tmp_path_factory, engine) -> Path:
    from src.export import Exporter

    out = tmp_path_factory.mktemp("data")
    Exporter(engine, out).run()
    return out


def run_node(script: str) -> str:
    """Run a snippet with Sommelier loaded; returns stdout."""
    prelude = f"const S = require({str(APP_JS)!r});\n"
    result = subprocess.run(
        ["node", "-e", prelude + textwrap.dedent(script)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def tag_ids(engine) -> list[str]:
    return [r["id"] for r in engine.con.execute("SELECT id FROM food_tags ORDER BY id")]


# --- normalise parity -----------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Comté",
        "Rías Baixas!",
        "SALT COD",
        "  spaced  out  ",
        "Château d'Yquem",
        "jalapeño",
        "crème brûlée",
        "Nero d'Avola",
    ],
)
def test_normalise_matches_python(text):
    """Index-time (Python) and query-time (JS) rules must be identical or
    accented names silently miss."""
    js = run_node(f"console.log(JSON.stringify(S.normalise({text!r})));")
    assert json.loads(js) == normalise(text)


# --- merge parity ---------------------------------------------------------


def _merge_in_node(exported: Path, tags: list[str]) -> list[dict]:
    paths = [str(exported / "pairings" / f"{t}.json") for t in tags]
    return json.loads(run_node(f"""
        const payloads = {json.dumps(paths)}.map(p => require(p));
        console.log(JSON.stringify(S.mergePairings(payloads)));
    """))


def test_single_tag_merge_matches_engine(exported, engine):
    for tag in tag_ids(engine):
        js = _merge_in_node(exported, [tag])
        py = engine.pair_food([tag], limit=50)
        assert [w["wine_id"] for w in js] == [
            w.wine_id for w in py
        ], f"order differs for {tag}"
        for a, b in zip(js, py):
            assert a["score"] == pytest.approx(
                b.score
            ), f"score differs for {tag}/{a['wine_id']}"
            assert a["strength"] == b.strength
            assert a["why"] == b.why


def test_multi_tag_merge_matches_engine(exported, engine):
    """The coverage bonus and avoid filtering are the fiddly parts."""
    tags = tag_ids(engine)
    for combo in itertools.combinations(tags, 2):
        js = _merge_in_node(exported, list(combo))
        py = engine.pair_food(list(combo), limit=50)
        assert [w["wine_id"] for w in js] == [
            w.wine_id for w in py
        ], f"order differs for {combo}"
        for a, b in zip(js, py):
            assert a["score"] == pytest.approx(b.score), f"{combo}: {a['wine_id']}"


def test_avoid_is_applied_client_side(exported, engine):
    """A wine that avoids ANY matched tag must vanish from the merge."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute(
        "SELECT tag_id, wine_id FROM pairings WHERE is_avoid = 1 LIMIT 1"
    ).fetchone()
    if not row:
        pytest.skip("no avoid entries in the dataset yet")
    tag, wine_id = row
    for combo in ([tag], [tag, *[t for t in tag_ids(engine) if t != tag][:1]]):
        merged = _merge_in_node(exported, combo)
        assert wine_id not in {w["wine_id"] for w in merged}


def test_three_tag_merge_matches_engine(exported, engine):
    tags = tag_ids(engine)[:3]
    if len(tags) < 3:
        pytest.skip("needs three food tags")
    js = _merge_in_node(exported, tags)
    py = engine.pair_food(tags, limit=50)
    assert [w["wine_id"] for w in js] == [w.wine_id for w in py]


# --- fuzzy search ---------------------------------------------------------


def _search_in_node(exported: Path, query: str) -> list[dict]:
    path = str(exported / "search-docs.json")
    return json.loads(run_node(f"""
        const docs = require({path!r});
        const hits = S.search(docs, {query!r}, 10);
        console.log(JSON.stringify(hits.map(h => ({{kind: h.doc.kind, ref: h.doc.ref}}))));
    """))


def test_exact_alias_wins(exported, engine):
    row = engine.con.execute(
        "SELECT alias, tag_id FROM aliases WHERE alias NOT LIKE '% %' LIMIT 1"
    ).fetchone()
    hits = _search_in_node(exported, row["alias"])
    assert hits and hits[0]["ref"] == row["tag_id"]


def test_typo_still_finds_the_dish(exported, engine):
    """Fuzzy fallback: a one-character slip must still land."""
    row = engine.con.execute(
        "SELECT alias, tag_id FROM aliases WHERE length(alias) >= 7 AND alias NOT LIKE '% %' LIMIT 1"
    ).fetchone()
    if not row:
        pytest.skip("no alias long enough to typo meaningfully")
    typo = row["alias"][:-2] + row["alias"][-1]  # drop a letter near the end
    hits = _search_in_node(exported, typo)
    assert row["tag_id"] in {h["ref"] for h in hits}, f"{typo!r} lost {row['tag_id']}"


def test_unknown_query_returns_nothing(exported):
    assert _search_in_node(exported, "zzzqqqxyzzy") == []


def test_dishes_rank_above_wines(exported, engine):
    """'what do I drink with X' is the main use case."""
    row = engine.con.execute("SELECT alias FROM aliases LIMIT 1").fetchone()
    hits = _search_in_node(exported, row["alias"])
    kinds = [h["kind"] for h in hits]
    if "food" in kinds and "wine" in kinds:
        assert kinds.index("food") < kinds.index("wine")
