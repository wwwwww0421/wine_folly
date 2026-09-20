"""Conversational queries must search the same as their stripped-down
keywords - "I want something with beef stew" should match exactly like
"beef stew" alone.

Found while testing: a short filler word like "in" or "i" is a SUBSTRING
of all sorts of unrelated text ("indian", "wine"...), so left in, a naive
"unmatched terms score 0" design doesn't save you - a stray substring hit
can outscore the real match entirely. SEARCH_STOPWORDS (src/engine.py) is
the fix, shipped to the browser via search-config.json and applied by
MiniSearch's processTerm at both index and query time (see buildIndex() in
templates/assets/app.js). These tests pin the concrete bug and the general
behaviour so it can't come back quietly.

Skips the end-to-end half cleanly when node isn't installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from src.engine import SEARCH_STOPWORDS

APP_JS = Path("templates/assets/app.js").resolve()


@pytest.mark.parametrize(
    "word", ["i", "im", "want", "having", "with", "of", "in", "good", "taste"]
)
def test_common_filler_is_a_stopword(word):
    assert word in SEARCH_STOPWORDS


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


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


def search(docs: list[dict], config: dict, query: str, limit: int = 10) -> list[str]:
    out = run_node(f"""
        const idx = S.buildIndex({json.dumps(docs)}, {json.dumps(config)});
        const hits = S.search(idx, {query!r}, {limit});
        console.log(JSON.stringify(hits.map(h => h.ref)));
    """)
    return json.loads(out)


FOOD_CONFIG = {"weights": {"label": 4, "aliases": 6, "country": 2}, "stopwords": sorted(SEARCH_STOPWORDS)}


@needs_node
def test_a_fully_filler_query_does_not_crash():
    """A query that's pure filler ("the a of") has nothing left to search -
    empty results is fine, a crash is not."""
    docs = [
        {"id": "food:x", "kind": "food", "ref": "x", "title": "X",
         "label": "x", "aliases": ["x"]},
    ]
    assert search(docs, FOOD_CONFIG, "the a of") == []


@needs_node
def test_sentence_query_matches_like_its_keywords():
    """The actual promise: typing a full sentence must not change the
    result versus typing just the keywords."""
    docs = [
        {"id": "food:braised-meat", "kind": "food", "ref": "braised-meat",
         "title": "Braised & stewed meats", "label": "braised stewed meats",
         "aliases": ["beef stew", "braised short ribs", "osso buco"]},
        {"id": "food:unrelated", "kind": "food", "ref": "unrelated",
         "title": "Something else", "label": "something else",
         "aliases": ["mango salsa"]},
    ]
    sentence = search(docs, FOOD_CONFIG, "I want something with beef stew")
    keywords = search(docs, FOOD_CONFIG, "beef stew")
    assert sentence == keywords
    assert sentence[0] == "braised-meat"


@needs_node
def test_short_filler_does_not_falsely_match_a_substring():
    """Regression test for the concrete bug: "in" is a substring of
    "indian", so a query like "good wines in Burgundy" was ranking a
    completely unrelated "Indian" food tag above the Burgundy region it
    was actually asking about."""
    docs = [
        {"id": "food:spiced", "kind": "food", "ref": "spiced",
         "title": "Spiced cuisine", "label": "spiced cuisine indian",
         "aliases": ["curry", "indian curry"]},
        {"id": "region:burgundy", "kind": "region", "ref": "burgundy",
         "title": "Burgundy", "label": "burgundy", "country": "france"},
    ]
    hits = search(docs, FOOD_CONFIG, "I want good wines in Burgundy")
    assert hits[0] == "burgundy"
