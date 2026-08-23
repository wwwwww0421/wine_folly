"""
Validate YAML files load into SQLite

It will reject if invalid yaml file or unknown food tags.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from .schema import FoodTag, Wine, RegionEntry


@dataclass
class Dataset:
    wines: list[Wine]
    food_tags: list[FoodTag]
    regions: dict[str, RegionEntry]
    warnings: list[str] = field(default_factory=list)


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

            implied = {regions[r].country.lower() for r in w.regions if r in regions}
            declared = {c.lower() for c in w.countries}
            if declared and implied and not (declared & implied):
                warnings.append(f"{w.id}: countries {sorted(declared)} don't match! - CHECK REGION!")

        for t in w.also_try:
            if t not in wine_ids:
                warnings.append(f"{w.id}: also_try '{t}' not written yet.")

    if errors:
        raise DatasetError(errors)

    return Dataset(wines, food_tags, regions, warnings)


### Compiling

DDL = """
CREATE TABLE wines (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    data        TEXT NOT NULL
);

CREATE TABLE pairings (
    wine_id     TEXT NOT NULL REFERENCES wines(id),
    tag_id      TEXT NOT NULL,
    strength    TEXT,
    why         TEXT NOT NULL,
    is_avoid    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE food_tags (
    id          TEXT PRIMARY KEY,
    label       TEXT NOT NULL
);

CREATE TABLE aliases (
    alias       TEXT PRIMARY KEY,
    tag_id      TEXT NOT NULL REFERENCES food_tags(id)
);

CREATE TABLE regions (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    country     TEXT,
    parent_id   TEXT
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
        content = " ".join([w.name, *w.grapes, *w.flavours, *region_names])
        con.execute(
            "INSERT INTO search_index VALUES ('wine', ?, ?)", (w.id, content)
        )

    for t in ds.food_tags:
        con.execute("INSERT INTO food_tags VALUES (?, ?)", (t.id, t.label))
        for a in t.aliases:
            con.execute("INSERT INTO aliases VALUES (?, ?)", (a, t.id))

        con.execute("INSERT INTO search_index VALUES ('food', ?, ?)", (t.id, " ".join([t.label, *t.aliases])))


    for r in ds.regions.values():
        con.execute(
            "INSERT INTO regions VALUES (?, ?, ?, ?)", (r.id, r.name, r.country, r.parent)
        )

        con.execute(
            "INSERT INTO search_index VALUES ('region', ?, ?)",
            (r.id, f"{r.name} {r.country}")
        )

    con.commit()
    con.close()
    return db_path