"""CLI behaviour tests.  (tests/test_query.py)

query.py is a presentation layer, so these check PRESENTATION: does the
right information reach the screen, are exit codes honest, do misses fail
gracefully. Engine correctness is test_engine.py's job — nothing here
re-tests ranking logic.

main() is called directly with an argv list, so there's no subprocess and
no shell quoting to fight.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from src.query import main

DB = "build/wine.db"


@pytest.fixture(scope="module")
def con():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture(scope="module")
def sample(con) -> dict:
    """Derive real ids from the database so these tests survive data changes."""
    wine = con.execute(
        "SELECT id, name, data FROM wines ORDER BY id LIMIT 1"
    ).fetchone()
    alias = con.execute(
        "SELECT alias FROM aliases WHERE tag_id IN"
        " (SELECT tag_id FROM pairings WHERE is_avoid = 0) LIMIT 1"
    ).fetchone()
    region = con.execute("SELECT id, name FROM regions LIMIT 1").fetchone()
    if not (wine and alias and region):
        pytest.skip("dataset too small to exercise the CLI")
    return {
        "wine_id": wine["id"],
        "wine_name": wine["name"],
        "record": json.loads(wine["data"]),
        "alias": alias["alias"],
        "region_id": region["id"],
        "region_name": region["name"],
    }


def run(capsys, argv: list[str]) -> tuple[int, str]:
    code = main(argv)
    return code, capsys.readouterr().out


# --- food query (the default command) -------------------------------------


def test_bare_text_is_treated_as_a_food_query(capsys, sample):
    """`query "bacalhau"` must work without typing `food` first."""
    code, out = run(capsys, [sample["alias"]])
    assert code == 0
    assert sample["alias"].lower() in out.lower()


def test_food_query_prints_the_why(capsys, con, sample):
    """The whole product promise: reasons, not just a ranked list."""
    code, out = run(capsys, ["food", sample["alias"]])
    row = con.execute(
        "SELECT p.why FROM pairings p JOIN aliases a ON a.tag_id = p.tag_id"
        " WHERE a.alias = ? AND p.is_avoid = 0 LIMIT 1",
        (sample["alias"],),
    ).fetchone()
    assert code == 0
    first_words = " ".join(row["why"].split()[:4])
    assert first_words in " ".join(out.split())


def test_food_query_suggests_a_next_hop(capsys, sample):
    """Terminal rehearsal of the web app's click-through."""
    _, out = run(capsys, ["food", sample["alias"]])
    assert "Next:" in out and "wine" in out


def test_unmatched_food_exits_nonzero_and_says_so(capsys):
    code, out = run(capsys, ["food", "pineapple-on-everything"])
    assert code == 1
    assert "No food tag matches" in out


# --- wine detail ----------------------------------------------------------


def test_wine_detail_prints_the_knowledge(capsys, sample):
    """Serving temp, glass, flavours — the 'how do I serve this' use case."""
    code, out = run(capsys, ["wine", sample["wine_id"]])
    assert code == 0
    assert sample["wine_name"] in out
    for flavour in sample["record"].get("flavours", [])[:2]:
        assert flavour in out
    serving = sample["record"].get("serving") or {}
    if serving.get("glass"):
        assert serving["glass"] in out
    if serving.get("temp_c"):
        assert str(serving["temp_c"][0]) in out


def test_wine_detail_shows_scale_values(capsys, sample):
    record = sample["record"]
    _, out = run(capsys, ["wine", sample["wine_id"]])
    for dim in ("body", "acidity", "tannin"):
        if record.get(dim) is not None:
            assert f"{dim}={record[dim]}" in out


def test_unknown_wine_exits_nonzero(capsys):
    code, out = run(capsys, ["wine", "chateau-imaginary"])
    assert code == 1
    assert "No wine" in out


# --- region, similar, flavour, search --------------------------------------


def test_region_lists_wines(capsys, sample):
    code, out = run(capsys, ["region", sample["region_id"]])
    assert code == 0
    assert sample["region_name"] in out


def test_unknown_region_exits_nonzero(capsys):
    code, _ = run(capsys, ["region", "narnia"])
    assert code == 1


def test_similar_prints_scores(capsys, sample):
    code, out = run(capsys, ["similar", sample["wine_id"]])
    if code == 1:
        pytest.skip("needs two wines with scale values")
    assert "compared on" in out


def test_search_groups_by_kind(capsys, sample):
    code, out = run(capsys, ["search", sample["wine_name"][:4]])
    assert code == 0
    assert "[wine" in out or "[food" in out or "[region" in out


def test_flavours_index_runs(capsys):
    code, out = run(capsys, ["flavours"])
    assert code == 0
    assert "flavour index" in out


def test_flavour_lookup_and_miss(capsys, sample):
    flavours = sample["record"].get("flavours")
    if not flavours:
        pytest.skip("no flavours recorded")
    code, out = run(capsys, ["flavour", flavours[0]])
    assert code == 0 and sample["wine_name"] in out
    assert run(capsys, ["flavour", "zzz-not-a-flavour"])[0] == 1


# --- argument handling ----------------------------------------------------


def test_limit_works_before_and_after_the_subcommand(capsys, sample):
    """Regression: argparse rejected options placed after the subcommand."""
    before = run(capsys, ["--limit", "1", "food", sample["alias"]])[1]
    after = run(capsys, ["food", sample["alias"], "--limit", "1"])[1]
    assert before == after


def test_limit_actually_limits(capsys, sample):
    out = run(capsys, ["food", sample["alias"], "--limit", "1"])[1]
    assert out.count("score ") <= 1


def test_multiword_query_is_joined(capsys, con):
    """`query salt cod` (unquoted) must behave like `query "salt cod"`."""
    row = con.execute(
        "SELECT alias FROM aliases WHERE alias LIKE '% %' LIMIT 1"
    ).fetchone()
    if not row:
        pytest.skip("no multi-word aliases yet")
    words = row["alias"].split()
    assert run(capsys, ["food", *words])[0] == 0


def test_no_arguments_prints_help(capsys):
    code, out = run(capsys, [])
    assert code == 2
    assert "usage" in out.lower()


def test_missing_database_fails_politely(capsys, sample):
    code, out = run(capsys, ["--db", "build/nope.db", "wine", sample["wine_id"]])
    assert code == 1
    assert "build.py" in out
