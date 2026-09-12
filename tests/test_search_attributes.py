"""Browser/CLI parity for the "sweet"/"tannic"/... attribute vocabulary.

README §5.2 promises search understands descriptive words like "sweet
blackcurrant" by reading the wine's own sweetness scale, not just literal
text. That scoring lives in two places (app.js for the browser,
engine.attribute_boost for anything that wants to check it in Python) and
must agree - same shape of test as test_search_parity.py.

Skips cleanly when node isn't installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from src.engine import ATTRIBUTE_WORDS, attribute_boost

APP_JS = Path("templates/assets/app.js").resolve()

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


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


def js_boost(term: str, doc: dict) -> float:
    out = run_node(f"""
        console.log(JSON.stringify(S.attributeBoost({term!r}, {json.dumps(doc)})));
    """)
    return json.loads(out)


@pytest.mark.parametrize("term", sorted(ATTRIBUTE_WORDS.keys()) + ["blackcurrant", ""])
@pytest.mark.parametrize("value", [1, 2, 3, 4, 5])
def test_attribute_boost_matches_python(term, value):
    dim = ATTRIBUTE_WORDS.get(term, ("sweetness", 1))[0]
    doc = {"kind": "wine", dim: value}
    assert js_boost(term, doc) == pytest.approx(attribute_boost(term, doc))


def test_non_wine_docs_never_boosted():
    doc = {"kind": "food", "sweetness": 5}
    assert attribute_boost("sweet", doc) == 0
    assert js_boost("sweet", doc) == 0


def test_sweet_wine_outranks_dry_wine_for_shared_flavour():
    """The actual point of this feature: "sweet blackcurrant" should rank a
    genuinely sweet, blackcurrant-flavoured wine above a dry one that also
    happens to show blackcurrant."""
    sweet_wine = {
        "kind": "wine", "ref": "sweet-one", "title": "Sweet One",
        "flavours": "blackcurrant", "sweetness": 5,
    }
    dry_wine = {
        "kind": "wine", "ref": "dry-one", "title": "Dry One",
        "flavours": "blackcurrant", "sweetness": 1,
    }
    docs = [dry_wine, sweet_wine]
    hits = run_node(f"""
        const docs = {json.dumps(docs)};
        const hits = S.search(docs, "sweet blackcurrant", 10);
        console.log(JSON.stringify(hits.map(h => h.doc.ref)));
    """)
    order = json.loads(hits)
    assert order.index("sweet-one") < order.index("dry-one")
