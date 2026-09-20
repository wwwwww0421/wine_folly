"""Acceptance test for the concrete ask that started the MiniSearch
rewrite: typing a plain sentence into the search box should resolve to
the right *kind* of answer. Run against the real, currently-exported
dataset (not synthetic docs) so this stays meaningful as the wine list
grows.

Skips cleanly when node isn't installed or the site hasn't been built yet
(run `python build.py` first).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

APP_JS = Path("templates/assets/app.js").resolve()
DOCS = Path("site/data/search-docs.json")
CONFIG = Path("site/data/search-config.json")

pytestmark = [
    pytest.mark.skipif(shutil.which("node") is None, reason="node not installed"),
    pytest.mark.skipif(not DOCS.exists() or not CONFIG.exists(), reason="run python build.py first"),
]


def top_kind(query: str) -> str:
    prelude = f"const S = require({str(APP_JS)!r});\n"
    script = f"""
        const docs = require({str(DOCS.resolve())!r});
        const config = require({str(CONFIG.resolve())!r});
        const idx = S.buildIndex(docs, config);
        const hits = S.search(idx, {query!r}, 5);
        console.log(JSON.stringify(hits[0] ? hits[0].kind : null));
    """
    result = subprocess.run(
        ["node", "-e", prelude + textwrap.dedent(script)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


@pytest.mark.parametrize(
    "query,expected_kind",
    [
        ("I want something strong", "wine"),
        ("I want something with beef stew", "food"),
        ("I'm having goat cheese", "food"),
        ("I want something taste of green apple", "wine"),
        ("I want good wines in Burgundy", "region"),
    ],
)
def test_natural_language_query_resolves_to_the_right_kind(query, expected_kind):
    assert top_kind(query) == expected_kind
