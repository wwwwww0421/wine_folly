"""Engine behaviour tests — dataset-independent.  (tests/test_engine.py)

    pytest -q

These assert RULES, never specific wines, so they stay green as the
cellar grows. Fixtures are DERIVED from whatever is in the database;
when the data can't exercise a rule yet, the test skips rather than
fails. Week 3's test_pairings.py is the opposite kind of test: it
asserts specific Wine Folly chart pairings on purpose.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.engine import STRENGTH_SCORE, Engine, normalise

DB = "build/wine.db"


@pytest.fixture(scope="module")
def engine() -> Engine:
    return Engine(DB)


@pytest.fixture(scope="module")
def con():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


# --- derived fixtures: whatever the dataset happens to contain -------------

@pytest.fixture(scope="module")
def any_wine(con) -> str:
    row = con.execute("SELECT id FROM wines ORDER BY id LIMIT 1").fetchone()
    if not row:
        pytest.skip("no wines yet")
    return row["id"]


@pytest.fixture(scope="module")
def any_alias(con) -> tuple[str, str]:
    """An alias plus the tag it maps to — must be a tag some wine pairs with."""
    row = con.execute(
        "SELECT a.alias, a.tag_id FROM aliases a"
        " WHERE a.tag_id IN (SELECT tag_id FROM pairings WHERE is_avoid = 0)"
        " ORDER BY a.alias LIMIT 1"
    ).fetchone()
    if not row:
        pytest.skip("no alias maps to a tag any wine pairs with")
    return row["alias"], row["tag_id"]


@pytest.fixture(scope="module")
def avoid_case(con) -> tuple[str, str]:
    """A (tag, wine) pair where the wine explicitly avoids the tag."""
    row = con.execute(
        "SELECT tag_id, wine_id FROM pairings WHERE is_avoid = 1 LIMIT 1"
    ).fetchone()
    if not row:
        pytest.skip("no `avoid` entries in the dataset yet")
    return row["tag_id"], row["wine_id"]


@pytest.fixture(scope="module")
def subregion_case(con) -> tuple[str, str, str]:
    """(parent_id, child_id, wine_id) where a wine sits in a subregion."""
    for row in con.execute("SELECT id, parent_id FROM regions WHERE parent_id IS NOT NULL"):
        for w in con.execute("SELECT id, data FROM wines"):
            if row["id"] in json.loads(w["data"]).get("regions", []):
                return row["parent_id"], row["id"], w["id"]
    pytest.skip("no wine sits in a subregion yet")


# --- normalization --------------------------------------------------------

def test_normalise_strips_accents_and_case():
    assert normalise("Comté") == normalise("comte") == "comte"


def test_normalise_strips_punctuation():
    assert normalise("Rías Baixas!") == "rias baixas"


# --- resolve --------------------------------------------------------------

def test_exact_alias_resolves_to_its_tag(engine, any_alias):
    alias, tag_id = any_alias
    assert ("food", tag_id) in {(m.kind, m.id) for m in engine.resolve(alias)}


def test_prefix_search_finds_a_wine_by_partial_name(engine, con, any_wine):
    name = con.execute("SELECT name FROM wines WHERE id = ?", (any_wine,)).fetchone()["name"]
    prefix = normalise(name)[:4]
    assert any(m.id == any_wine for m in engine.resolve(prefix)), \
        f"searching {prefix!r} should surface {any_wine}"


def test_region_search_returns_regions(engine, con):
    country = con.execute(
        "SELECT country FROM regions WHERE country != '' LIMIT 1"
    ).fetchone()
    if not country:
        pytest.skip("no regions with a country")
    matches = engine.resolve(country["country"], limit=50)
    assert any(m.kind == "region" for m in matches)


def test_resolve_respects_its_limit(engine, con):
    """Regression: the default limit truncates broad queries, so callers
    that need everything must pass their own."""
    country = con.execute("SELECT country FROM regions WHERE country != '' LIMIT 1").fetchone()
    if not country:
        pytest.skip("no regions with a country")
    assert len(engine.resolve(country["country"], limit=3)) <= 3


def test_empty_query_returns_nothing(engine):
    assert engine.resolve("   ") == []


def test_nonsense_query_returns_nothing(engine):
    assert engine.resolve("zzzqqqxyzzy") == []


# --- pairing --------------------------------------------------------------

def test_food_query_returns_ranked_wines_with_whys(engine, any_alias):
    alias, _ = any_alias
    foods, ranked = engine.pair_text(alias)
    assert foods, f"{alias!r} should resolve to at least one food tag"
    assert ranked, f"{alias!r} resolved but paired with nothing"
    assert all(w.why and all(w.why) for w in ranked)


def test_results_are_sorted_by_score_descending(engine, any_alias):
    _, ranked = engine.pair_text(any_alias[0])
    assert [w.score for w in ranked] == sorted((w.score for w in ranked), reverse=True)


def test_strength_maps_to_score(engine, any_alias):
    """A single-tag match scores exactly its strength — no hidden bonuses."""
    _, tag_id = any_alias
    for w in engine.pair_food([tag_id]):
        assert w.score == STRENGTH_SCORE[w.strength]


def test_avoid_excludes_the_wine(engine, avoid_case):
    tag_id, wine_id = avoid_case
    assert wine_id not in {w.wine_id for w in engine.pair_food([tag_id])}


def test_multi_tag_query_gets_coverage_bonus(engine, con):
    """A wine matching two tags must outscore its own single-tag score."""
    row = con.execute(
        "SELECT wine_id, GROUP_CONCAT(DISTINCT tag_id) AS tags FROM pairings"
        " WHERE is_avoid = 0 GROUP BY wine_id HAVING COUNT(DISTINCT tag_id) >= 2 LIMIT 1"
    ).fetchone()
    if not row:
        pytest.skip("no wine pairs with two different tags yet")
    tags = row["tags"].split(",")[:2]
    single = next(w for w in engine.pair_food(tags[:1]) if w.wine_id == row["wine_id"]).score
    both = next(w for w in engine.pair_food(tags) if w.wine_id == row["wine_id"]).score
    assert both > single


def test_unknown_tag_pairs_to_nothing(engine):
    assert engine.pair_food(["definitely-not-a-tag"]) == []


def test_empty_tag_list_is_safe(engine):
    assert engine.pair_food([]) == []


def test_results_carry_next_hop_ids(engine, any_alias):
    """The UI must navigate onward without another query."""
    _, ranked = engine.pair_text(any_alias[0])
    top = ranked[0]
    assert isinstance(top.also_try, list) and isinstance(top.regions, list)
    assert all("id" in r for r in top.regions)


# --- wine detail ----------------------------------------------------------

def test_wine_detail_shape(engine, any_wine):
    d = engine.wine_detail(any_wine)
    assert d is not None and d.wine["id"] == any_wine
    assert isinstance(d.pairs_with, list) and isinstance(d.similar, list)


def test_wine_detail_resolves_region_names(engine, con):
    row = con.execute("SELECT id, data FROM wines").fetchall()
    for w in row:
        if json.loads(w["data"]).get("regions"):
            d = engine.wine_detail(w["id"])
            if d.regions:
                assert {"id", "name", "country"} <= set(d.regions[0])
                return
    pytest.skip("no wine references a known region yet")


def test_also_try_only_lists_written_wines(engine, con):
    ids = {r["id"] for r in con.execute("SELECT id FROM wines")}
    for row in con.execute("SELECT id FROM wines"):
        d = engine.wine_detail(row["id"])
        assert {a["id"] for a in d.also_try} <= ids


def test_pairings_sorted_strongest_first(engine, con):
    for row in con.execute("SELECT id FROM wines"):
        d = engine.wine_detail(row["id"])
        scores = [STRENGTH_SCORE[p["strength"]] for p in d.pairs_with]
        assert scores == sorted(scores, reverse=True)


def test_unknown_wine_returns_none(engine):
    assert engine.wine_detail("chateau-does-not-exist") is None


# --- similarity -----------------------------------------------------------

def test_similar_uses_only_shared_dimensions(engine, con, any_wine):
    base = json.loads(con.execute(
        "SELECT data FROM wines WHERE id = ?", (any_wine,)).fetchone()["data"])
    known = {d for d in ("body", "sweetness", "tannin", "acidity", "alcohol", "intensity")
             if base.get(d) is not None}
    for s in engine.similar(any_wine):
        assert set(s.shared_dim) <= known


def test_similarity_scores_are_valid_and_descending(engine, any_wine):
    results = engine.similar(any_wine)
    if not results:
        pytest.skip("needs at least two wines with scale values")
    assert all(0 <= s.similarity <= 1 for s in results)
    assert [s.similarity for s in results] == sorted(
        (s.similarity for s in results), reverse=True)


def test_similarity_is_symmetric(engine, any_wine):
    results = engine.similar(any_wine, limit=1)
    if not results:
        pytest.skip("needs at least two wines with scale values")
    other = results[0]
    back = next((s for s in engine.similar(other.wine_id, limit=100)
                 if s.wine_id == any_wine), None)
    assert back is not None
    assert back.similarity == pytest.approx(other.similarity)


def test_wine_is_never_similar_to_itself(engine, any_wine):
    assert all(s.wine_id != any_wine for s in engine.similar(any_wine))


def test_shared_flavours_are_genuinely_shared(engine, con, any_wine):
    base = set(json.loads(con.execute(
        "SELECT data FROM wines WHERE id = ?", (any_wine,)).fetchone()["data"]
    ).get("flavours", []))
    for s in engine.similar(any_wine):
        assert set(s.shared_flavours) <= base


# --- region ---------------------------------------------------------------

def test_region_lists_only_its_own_wines(engine, con):
    for row in con.execute("SELECT id FROM regions"):
        d = engine.region_detail(row["id"])
        children = {r["id"] for r in con.execute(
            "SELECT id FROM regions WHERE parent_id = ?", (row["id"],))}
        wanted = {row["id"]} | children
        for w in d.wines:
            record = json.loads(con.execute(
                "SELECT data FROM wines WHERE id = ?", (w["id"],)).fetchone()["data"])
            assert set(record["regions"]) & wanted


def test_parent_region_rolls_up_subregion_wines(engine, subregion_case):
    parent, _child, wine_id = subregion_case
    assert wine_id in {w["id"] for w in engine.region_detail(parent).wines}


def test_region_siblings_share_a_country(engine, con):
    for row in con.execute("SELECT id, country FROM regions WHERE country != ''"):
        d = engine.region_detail(row["id"])
        for s in d.siblings:
            sib = con.execute("SELECT country FROM regions WHERE id = ?", (s["id"],)).fetchone()
            assert sib["country"] == row["country"]
            assert s["id"] != row["id"]


def test_unknown_region_returns_none(engine):
    assert engine.region_detail("narnia") is None


# --- flavours --------------------------------------------------------------

def test_flavour_search_finds_wines_with_that_flavour(engine, con):
    row = con.execute("SELECT data FROM wines").fetchone()
    flavours = json.loads(row["data"]).get("flavours", [])
    if not flavours:
        pytest.skip("no flavours recorded yet")
    target = flavours[0]
    for w in engine.by_flavour(target):
        assert any(normalise(target) in normalise(f) for f in w["matched_flavours"])


def test_flavour_index_counts_and_sorts(engine):
    index = engine.all_flavours()
    if not index:
        pytest.skip("no flavours recorded yet")
    assert [f["count"] for f in index] == sorted((f["count"] for f in index), reverse=True)
    assert all(f["count"] >= 1 for f in index)


# --- serialization --------------------------------------------------------

def test_results_are_json_ready(engine, any_alias, any_wine):
    """export.py depends on this: dataclass -> dict with no custom code."""
    _, ranked = engine.pair_text(any_alias[0])
    json.dumps(engine.to_json(ranked[0]))
    if sim := engine.similar(any_wine):
        json.dumps(engine.to_json(sim[0]))