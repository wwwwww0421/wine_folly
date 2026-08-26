"""
The query layer - ENGINE!

Reads wines.db that produced by build.py.
It can answers 4 questions, all of which return objects that carry the ids you'd click NEXT - that's what make the web UI a matter of rendering than logic:

    food -> ranked wines + why                  pair_food()
    wine -> foods, regions, similar             wine_detail()
    region -> its wines + their flavours        region_detail()
    text -> "what did you mean?"                resolve()

Nothing here knows about HTML. export.py walks these same functions to pre-compute JSON for the offline app, and query.py wraps them for the CLI - one brain, 3 faces.
"""


from __future__ import annotations

import json
import math
import re
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path


STRENGTH_SCORE = {"perfect": 3.0, "great": 2.0, "good": 1.0}
SCALE_DIM = {"body", "sweetness", "tannin", "acidity", "alcohol"}

def normalise(text: str) -> str:
    """
    Lowercase, strip accents and punctuation - applied at index time and query time.
    """

    text = unicodedata.normalize("NFKD", text.lower())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", text).strip()


@dataclass
class Match:
    """One interpretation of a search box query."""
    kind: str
    id: str
    label: str


@dataclass
class PairedWine:
    wine_id: str
    name: str
    score: float
    strength: str
    why: list[str]
    matched_tags: list[str]
    flavours: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    also_try: list[str] = field(default_factory=list)

@dataclass
class SimilarWine:
    wine_id: str
    name: str
    similarity: float
    shared_dim: list[str]
    shared_flavours: list[str]


@dataclass
class WineDetail:
    wine: dict
    pairs_with: list[dict]
    avoid: list[dict]
    regions: list[dict]
    similar: list[SimilarWine]
    also_try: list[dict]
    falvours: list[str]


@dataclass
class RegionDetail:
    region: dict
    wines: list[str]
    flavour_profile: list[str]
    siblings: list[dict]