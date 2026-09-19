"""
Validate YAML files load into SQLite

It will reject if invalid yaml file or unknown food tags.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from .schema import FoodCategory, FoodTag, Wine, RegionEntry

# A couple of common abbreviations that show up in wines' `countries:` but
# never as the spelled-out name regions.yaml uses for its own `country:`.
COUNTRY_SYNONYMS = {"usa": "united-states", "us": "united-states", "uk": "united-kingdom"}


def _country_slug(name: str) -> str:
    """
    "United States" and "united-states" must compare equal - both sides of
    the also_try/region cross-check below normalise through this, so a
    slug (from a wine's `countries:`) and a plain name (from a region's
    `country:`) are judged the same way instead of failing on formatting.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return COUNTRY_SYNONYMS.get(slug, slug)


@dataclass
class Dataset:
    wines: list[Wine]
    food_tags: list[FoodTag]
    food_categories: list[FoodCategory]
    regions: dict[str, RegionEntry]
    warnings: list[str] = field(default_factory=list)
    # also_try id -> which wine(s) named it, for every id that isn't a
    # written wine or a known alias of one. One deduplicated study list
    # instead of a repeated warning per wine that mentions it.
    unwritten_also_try: dict[str, list[str]] = field(default_factory=dict)


class DatasetError(Exception):
    """
    All cross-file problems, collected - so one build run shows everything wrong, not just the first thing.
    """
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


### LOADING....
def load_dataset(data_dir: Path = Path("data")) -> Dataset:
    errors: list[str] = []
    warnings: list[str] = []

    ### FOOD CATEGORIES CHECK
    food_categories: list[FoodCategory] = []
    categories_path = data_dir / "food-categories.yaml"
    if categories_path.exists():
        try:
            food_categories = FoodCategory.load_categories(
                yaml.safe_load(categories_path.read_text(encoding='utf-8')) or []
            )
        except (ValueError, ValidationError) as e:
            errors.append(f"{categories_path}: {e}")
    else:
        warnings.append(f"no {categories_path} - food category check skipping...")

    ### FOOD TAGS CHECK
    food_tags: list[FoodTag] = []
    tags_path = data_dir / "food-tags.yaml"
    if tags_path.exists():
        try:
            food_tags = FoodTag.load_food_tags(yaml.safe_load(tags_path.read_text(encoding='utf-8')) or [])

        except (ValueError, ValidationError) as e:
            errors.append(f"{tags_path}: {e}")

    else:
        warnings.append(f"no {tags_path} - food tag skipping...")

    if food_tags and food_categories:
        category_ids = {c.id for c in food_categories}
        for t in food_tags:
            if t.category not in category_ids:
                errors.append(
                    f"{tags_path}: tag '{t.id}' has unknown category '{t.category}' - ADD TO FOOD-CATEGORIES!"
                )


    ### REGIONS CHECK
    regions: list[RegionEntry] = []
    regions_path = data_dir / "regions.yaml"
    if regions_path.exists():
        try:
            regions = RegionEntry._load_regions(yaml.safe_load(regions_path.read_text(encoding='utf-8')) or [])
        except (ValueError, ValidationError) as e:
            errors.append(f"{regions_path}: {e}")
    else:
        warnings.append(f"no {tags_path} - region check skipping...")


    ### WINES CHECK
    wines: list[Wine] = []
    seen_ids: dict[str, Path] = {}
    for path in sorted((data_dir / "wines").glob("*.yaml")):
        try:
            wine = Wine.model_validate(yaml.safe_load(path.read_text(encoding='utf-8')))

        except yaml.YAMLError as e:
            errors.append(f'{path} not valid YAML - {e}!')

        except ValidationError as e:
            for err in e.errors():
                loc = '.'.join(str(p) for p in err['loc']) or "(root)"
                errors.append(f"{path}: {loc}: {err['msg']}")
            continue

        if wine.id != path.stem:
            errors.append(f"{path}: id '{wine.id}' doesn't match filename")
        if wine.id in seen_ids:
            errors.append(f"{path}: duplicate wine id {wine.id}")
        seen_ids[wine.id] = path
        wines.append(wine)


    ### CROSS FILES CHECK
    tag_ids = {t.id for t in food_tags}
    wine_ids = {w.id for w in wines}

    # A wine's aliases (e.g. sherry.yaml declaring aliases: [cream-sherry,
    # palo-cortado-sherry]) must be globally unique and distinct from every
    # wine's own id - otherwise "which wine does this id mean" is
    # ambiguous, the same rule FoodTag.load_food_tags enforces for aliases.
    alias_owner: dict[str, str] = {}
    for w in wines:
        for a in w.aliases:
            if a in wine_ids:
                errors.append(f"{w.id}: alias '{a}' collides with an existing wine id.")
            elif a in alias_owner:
                errors.append(f"{w.id}: alias '{a}' already used by '{alias_owner[a]}'.")
            else:
                alias_owner[a] = w.id
    known_wine_refs = wine_ids | set(alias_owner)
    unwritten_also_try: dict[str, list[str]] = {}

    for w in wines:
        if food_tags:
            for group_name, group in (("pairing", w.pairings), ("avoid", w.avoid)):
                if group is not None:
                    for item in group:
                        for t in item.tags:
                            if t not in tag_ids:
                                errors.append(f"{w.id}: unknown food tag {t} in {group_name}. - ADD TO FOOD-TAG!")


        if regions:
            for r in regions:
                if r not in regions:
                    errors.append(f"{w.id}: unknown region '{r}'. - ADD TO REGION!")

            implied = {_country_slug(regions[r].country) for r in w.regions if r in regions}
            declared = {_country_slug(c) for c in w.countries}
            if declared and implied and not (declared & implied):
                warnings.append(f"{w.id}: countries {sorted(declared)} don't match! - CHECK REGION!")

        for t in w.also_try:
            if t not in known_wine_refs:
                unwritten_also_try.setdefault(t, []).append(w.id)

    if errors:
        raise DatasetError(errors)

    return Dataset(wines, food_tags, food_categories, regions, warnings, unwritten_also_try)


### Compiling

DDL = """
CREATE TABLE wines (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    data        TEXT NOT NULL
);

-- Other ids the SAME wine is also written as (e.g. "cream-sherry" -> the
-- wine whose own id is "sherry"). Lets also_try/similar-wine lookups
-- resolve a style-variant id to the one page that actually exists.
CREATE TABLE wine_aliases (
    alias       TEXT PRIMARY KEY,
    wine_id     TEXT NOT NULL REFERENCES wines(id)
);

CREATE TABLE pairings (
    wine_id     TEXT NOT NULL REFERENCES wines(id),
    tag_id      TEXT NOT NULL,
    strength    TEXT,
    why         TEXT NOT NULL,
    is_avoid    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE food_categories (
    id                  TEXT PRIMARY KEY,
    label               TEXT NOT NULL,
    pairing_principle   TEXT NOT NULL
);

CREATE TABLE food_tags (
    id          TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    category_id TEXT NOT NULL REFERENCES food_categories(id)
);

CREATE TABLE aliases (
    alias       TEXT PRIMARY KEY,
    tag_id      TEXT NOT NULL REFERENCES food_tags(id)
);

CREATE TABLE regions (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    country     TEXT,
    parent_id   TEXT,
    known_for   TEXT
);

CREATE VIRTUAL TABLE search_index USING fts5(
    doc_type    UNINDEXED,
    ref_id      UNINDEXED,
    content
)
"""


def write_sqlite(ds: Dataset, db_path: Path = Path("build/wine.db")) -> Path:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True) ## Always rebuild from scratch
    con = sqlite3.connect(db_path)
    con.executescript(DDL)

    for w in ds.wines:
        con.execute(
            "INSERT INTO wines VALUES (?, ?, ?)",
            (w.id, w.name, json.dumps(w.model_dump(mode="json"))),
        )
        for a in w.aliases:
            con.execute("INSERT INTO wine_aliases VALUES (?, ?)", (a, w.id))
        for p in w.pairings:
            for t in p.tags:
                con.execute(
                    "INSERT INTO pairings VALUES (?, ?, ?, ?, 0)",
                    (w.id, t, p.strength, p.why)
                )

        for a in (w.avoid or []):
            for t in a.tags:
                con.execute(
                    "INSERT INTO pairings VALUES (?, ?, NULL, ?, 1)",
                    (w.id, t, a.why)
                )

        region_names = [ds.regions[r].name for r in w.regions if r in ds.regions]
        why_text = [p.why for p in w.pairings] + [a.why for a in (w.avoid or [])]
        content = " ".join(
            [
                w.name,
                *w.grapes,
                *w.flavours,
                *region_names,
                *w.wine_type,
                w.notes or "",
                *why_text,
            ]
        )
        con.execute(
            "INSERT INTO search_index VALUES ('wine', ?, ?)", (w.id, content)
        )

    for c in ds.food_categories:
        con.execute(
            "INSERT INTO food_categories VALUES (?, ?, ?)",
            (c.id, c.label, c.pairing_principle),
        )

    for t in ds.food_tags:
        con.execute(
            "INSERT INTO food_tags VALUES (?, ?, ?)", (t.id, t.label, t.category)
        )
        for a in t.aliases:
            con.execute("INSERT INTO aliases VALUES (?, ?)", (a, t.id))

        con.execute("INSERT INTO search_index VALUES ('food', ?, ?)", (t.id, " ".join([t.label, *t.aliases])))


    for r in ds.regions.values():
        con.execute(
            "INSERT INTO regions VALUES (?, ?, ?, ?, ?)",
            (r.id, r.name, r.country, r.parent, " ".join(r.known_for)),
        )

        con.execute(
            "INSERT INTO search_index VALUES ('region', ?, ?)",
            (r.id, " ".join([r.name, r.country, *r.known_for]))
        )

    con.commit()
    con.close()
    return db_path