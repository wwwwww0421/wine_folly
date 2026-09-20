"""Descriptive words like "sweet"/"tannic"/"strong" need no runtime search
logic at all: taste_tags() (src/engine.py) turns a wine's own 1-5 scale
values into literal, repeated words at build time (see export_search_docs
in src/export.py), so the browser's MiniSearch index just searches them
like any other field - a sweetness=5 wine naturally outranks a
sweetness=4 one for "sweet" because it has more repetitions of the word,
which is exactly what BM25 already rewards.

These tests exercise taste_tags() directly (the actual decision-making
code, now single-sourced in Python) and, for the end-to-end behavioural
guarantee, run the real MiniSearch engine (templates/assets/app.js) the
same way the browser does.

Skips the end-to-end half cleanly when node isn't installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from src.engine import ATTRIBUTE_WORDS, taste_tags
from src.schema import SCALE_DIMS

APP_JS = Path("templates/assets/app.js").resolve()


def test_center_value_produces_no_tags():
    """3 is neutral on every dimension - nothing should lean either way."""
    assert taste_tags({d: 3 for d in SCALE_DIMS}) == []


def test_missing_dimension_is_skipped_not_crashed():
    assert taste_tags({}) == []


@pytest.mark.parametrize("word,dim,direction", [(w, d, dirn) for w, (d, dirn) in ATTRIBUTE_WORDS.items()])
def test_each_attribute_word_appears_when_its_dimension_leans_that_way(word, dim, direction):
    extreme = 5 if direction > 0 else 1
    tags = taste_tags({dim: extreme})
    assert tags.count(word) == 2  # max distance from center (3) is 2

    opposite = 1 if direction > 0 else 5
    assert word not in taste_tags({dim: opposite})


def test_more_extreme_value_repeats_the_word_more():
    mild = taste_tags({"sweetness": 4}).count("sweet")
    extreme = taste_tags({"sweetness": 5}).count("sweet")
    assert 0 < mild < extreme


def test_a_wine_can_lean_on_several_dimensions_at_once():
    tags = taste_tags({"sweetness": 5, "tannin": 5, "acidity": 1})
    assert "sweet" in tags
    assert "tannic" in tags
    assert "dry" not in tags  # sweetness is high, not low


# --- end-to-end: the real browser engine must rank on this correctly -----


def run_node(script: str) -> str:
    prelude = f"const S = require({str(APP_JS)!r});\n"
    result = subprocess.run(
        ["node", "-e", prelude + textwrap.dedent(script)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_sweet_wine_outranks_dry_wine_for_shared_flavour():
    """The actual point of this feature: "sweet blackcurrant" should rank a
    genuinely sweet, blackcurrant-flavoured wine above a dry one that also
    happens to show blackcurrant - built the same way export_search_docs
    builds it, not a hand-simplified stand-in."""
    sweet_wine = {
        "id": "wine:sweet-one", "kind": "wine", "ref": "sweet-one", "title": "Sweet One",
        "flavours": ["blackcurrant"], "taste_tags": taste_tags({"sweetness": 5}),
    }
    dry_wine = {
        "id": "wine:dry-one", "kind": "wine", "ref": "dry-one", "title": "Dry One",
        "flavours": ["blackcurrant"], "taste_tags": taste_tags({"sweetness": 1}),
    }
    config = {
        "weights": {"flavours": 4, "taste_tags": 3},
        "stopwords": [],
    }
    docs = [dry_wine, sweet_wine]
    hits = run_node(f"""
        const idx = S.buildIndex({json.dumps(docs)}, {json.dumps(config)});
        const hits = S.search(idx, "sweet blackcurrant", 10);
        console.log(JSON.stringify(hits.map(h => h.ref)));
    """)
    order = json.loads(hits)
    assert order.index("sweet-one") < order.index("dry-one")
