"""Does the engine agree with the book?  (tests/test_pairings.py)

The opposite of test_engine.py: these assert SPECIFIC pairings taken from
Wine Folly's charts, and are meant to be dataset-specific. They are how
you tune the DATA — if the book says salt cod wants a high-acid white and
the engine ranks a Cabernet first, the data is wrong, not the test.

Cases live in tests/pairing_cases.yaml so transcribing the book never
means editing Python. Cases naming an unwritten wine skip automatically,
which makes the file a study to-do list too:

    pytest tests/test_pairings.py -q -rs     # -rs lists what skipped
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from src.engine import Engine

DB = "build/wine.db"
CASES = yaml.safe_load((Path(__file__).parent / "pairing_cases.yaml").read_text()) or []


@pytest.fixture(scope="module")
def engine() -> Engine:
    return Engine(DB)


@pytest.fixture(scope="module")
def written_wines() -> set[str]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    return {row[0] for row in con.execute("SELECT id FROM wines")}


def _case_id(case: dict) -> str:
    return f"{case['query']}->{case['expect']}"


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_book_pairing_is_reproduced(engine, written_wines, case):
    expect, rank = case["expect"], case.get("rank", 3)

    if expect not in written_wines:
        pytest.skip(f"{expect} not written yet — {case.get('note', '')}")

    foods, ranked = engine.pair_text(case["query"])
    assert foods, (
        f"'{case['query']}' matches no food tag — add it as an alias in "
        f"data/food-tags.yaml"
    )

    ids = [w.wine_id for w in ranked]
    assert expect in ids, (
        f"book says '{case['query']}' pairs with {expect}, engine returned "
        f"{ids or 'nothing'}. Add the pairing to data/wines/{expect}.yaml"
    )
    position = ids.index(expect) + 1
    assert position <= rank, (
        f"{expect} ranked #{position} for '{case['query']}', expected top {rank}. "
        f"Full order: {ids}"
    )


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_expected_wine_explains_itself(engine, written_wines, case):
    """A correct ranking with no reason attached is still a failure — the
    whole point is answering *why*."""
    if case["expect"] not in written_wines:
        pytest.skip("wine not written yet")
    _, ranked = engine.pair_text(case["query"])
    match = next((w for w in ranked if w.wine_id == case["expect"]), None)
    if match is None:
        pytest.skip("covered by the ranking test above")
    assert match.why and all(w.strip() for w in match.why), (
        f"{case['expect']} pairs with '{case['query']}' but has no `why` text"
    )


def test_case_file_is_well_formed():
    for case in CASES:
        assert {"query", "expect"} <= set(case), f"malformed case: {case}"
        assert isinstance(case.get("rank", 3), int)