"""
Data Schema for Wine Folly - the single source of truth of what valid data looks like.

Design decisions encoded:
    * Optional-by-omission: unknown fields are absent, never blank.
    * No placeholder stubs: a pariing must have tags, a strength and a why.
    * extra="forbid": a typo like "tanin: 4" fails the build loudly.
    * Strict reference (warn only) since it points at wines not yet written.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, ClassVar, Optional, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator, Field

Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[-'][a-z0-9]+)*$")]

Scale = Annotated[int, Field(ge=1, le=5)]  ## The 5 scale chart

SCALE_DIMS: tuple[str, ...] = ("body", "sweetness", "tannin", "acidity", "alcohol")


WineType = Literal[
    "light-white",
    "full-white",
    "aromatic-white",
    "rosé",
    "light-red",
    "medium-red",
    "full-red",
    "sparkling",
    "dessert",
]

Glass = Literal[
    "flute-glass",
    "white-glass",
    "red-glass",
    "aroma-collector",
    "oversized",
    "dessert-glass",
]

PriceBand = Literal["£", "££", "£££", "££££"]

Climate = Literal[
    "cool",
    "continental",
    "continental-mediterranean",
    "continental-sub-mediterranean",
    "mediterranean",
    "maritime",
    "mediterranean-maritime",
    "maritime-continental",
    "maritime-mediterranean",
    "continental-maritime",
    "diverse",
    "continental-desert",
    "continental-monsoon",
    "high-altitude desert",
    "desert",
    "monsoon",
    "mediterranean-continental",
]


class Strength(str, Enum):
    PERFECT = "perfect"
    GREAT = "great"
    GOOD = "good"

    @property
    def score(self) -> int:
        return {"perfect": 3, "great": 2, "good": 1}[self.value]


class Popularity(str, Enum):
    RARE = "rare"
    UNCOMMON = "uncommon"
    COMMON = "common"
    POPULAR = "popular"

    @property
    def score(self) -> int:
        return {"rare": 4, "uncommon": 3, "popular": 2, "common": 1}[self.value]


### Base Models


class Serving(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temp_c: Optional[tuple[int, int]] = None
    glass: Optional[Glass] = None
    decant_minutes: Optional[Annotated[int, Field(ge=0)]] = None
    cellar_years: Optional[tuple[int, int]] = None

    @field_validator("temp_c", "cellar_years")
    @classmethod
    def low_before_high(cls, v):
        if v is not None and v[0] > v[1]:
            raise ValueError(f"range {v} must be [low, high]")
        return v


class Pairings(BaseModel):
    """No empty stubs allowed! Every field here is required and non-empty."""

    model_config = ConfigDict(extra="forbid")

    tags: Annotated[list[str], Field(min_length=1)]
    strength: Strength
    why: Annotated[str, Field(min_length=1)]


class Avoid(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tags: Annotated[list[str], Field(min_length=1)]
    why: Annotated[str, Field(min_length=1)]


## Entities


class Wine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ## Core Info
    id: Slug
    name: str = Field(min_length=1)
    wine_type: list[WineType] = Field(min_length=1)
    grapes: list[Slug] = Field(min_length=1)

    ## Wine Tasting
    body: Optional[Scale] = None
    sweetness: Optional[Scale] = None
    tannin: Optional[Scale] = None
    acidity: Optional[Scale] = None
    alcohol: Optional[Scale] = None

    ## Descriptive
    flavours: list[Slug] = []
    regions: list[Slug] = []
    countries: list[Slug] = []
    popularity: Optional[Popularity] = None
    price_band: Optional[PriceBand] = None
    serving: Optional[Serving] = None

    ## Wine Pairing
    pairings: list[Pairings] = None
    avoid: list[Avoid] = None
    notes: Optional[str] = None

    ## Similarity
    also_try: list[Slug] = []

    @field_validator("notes")
    @classmethod
    def _strip_wrapping_quotes(cls, v: Optional[str]) -> Optional[str]:
        """
        `notes: >` already yields a string: wrapping quotes would render literally in the app, so strip them and whitespace automatically.
        """
        if v is None:
            return v
        v = v.strip()
        if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
            v = v[1:-1].strip()

        return v

    @field_validator("flavours")
    @classmethod
    def _tidy_flavours(cls, v: Optional[str]) -> list[str]:
        cleaned = [f.strip().lower() for f in v if f.strip()]
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("Duplicate flavours!")

        return cleaned

    @model_validator(mode="after")
    def _dont_try_itself(self) -> "Wine":
        if self.id in self.also_try:
            raise ValueError("also_try cannot include itself!")

        return self

    ## Helpers for build.py

    STUDY_FIELDS: ClassVar[tuple[str, ...]] = (*SCALE_DIMS, "serving", "notes")

    def missing_info(self) -> list[str]:
        """Fields still unstudied - powers the completeness report."""
        gaps = [f for f in self.STUDY_FIELDS if getattr(self, f) is None]
        if not self.pairings:
            gaps.append("pairings")
        return gaps

    def attribute_vector(self) -> dict[str, int]:
        """
        Known scale attributes only.

        Similarity in engine.py compares whichever dimensions two wines SHARE, so gaps degrade gracefully.
        """
        # dims = ("body", "sweetness", "tannin", "acidity", "alcohol")
        # return {d: v for d in dims if (v := getattr(self, d)) is not None}
        return {d: v for d in SCALE_DIMS if (v := getattr(self, d)) is not None}


class FoodCategory(BaseModel):
    """
    Single entry in data/food-categories.yaml.

    Groups food tags by *why* they pair the way they do (fat needs tannin,
    delicate food needs acid, ...) rather than by cuisine style. Purely
    display grouping - a tag's id/aliases never depend on its category.
    """

    model_config = ConfigDict(extra="forbid")

    id: Slug
    label: str = Field(min_length=1)
    pairing_principle: str = Field(min_length=1)

    @field_validator("pairing_principle")
    @classmethod
    def _tidy_principle(cls, v: str) -> str:
        return v.strip()

    @staticmethod
    def load_categories(entries: list[dict]) -> list[FoodCategory]:
        cats = [FoodCategory.model_validate(e) for e in entries]
        seen: dict[str, str] = {}
        errors: list[str] = []
        for c in cats:
            if c.id in seen:
                errors.append(f"duplicate category id - {c.id}")
            seen[c.id] = c.id

        if errors:
            raise ValueError("food-categories.yaml: " + "; ".join(errors))
        return cats


class FoodTag(BaseModel):
    """
    Single yaml for food.

    The bridge between food types and wines. Labels are UI heading. Aliases are matched against search input.
    """

    model_config = ConfigDict(extra="forbid")

    id: Slug
    label: str = Field(min_length=1)
    category: Slug
    aliases: list[str] = Field(min_length=1)

    @field_validator("aliases")
    @classmethod
    def _tidy_aliases(cls, v: list[str]) -> list[str]:
        return [a.strip().lower() for a in v if a.strip()]

    @staticmethod
    def load_food_tags(entries: list[dict]) -> list[FoodTag]:
        """
        Validate the whole food-tags file, enforcing global unique - every id and every alias must resolve to exactly one tag.
        """
        tags = [FoodTag.model_validate(e) for e in entries]
        seen_ids: dict[str, str] = {}
        seen_aliases: dict[str, str] = {}
        errors: list[str] = []
        # for i, raw in enumerate(entries):
        #     tag_id = raw.get("id", f"index #{i}") if isinstance(raw, dict) else f"index #{i}"

        #     try:
        #         tags.append(FoodTag.model_validate(raw))
        #     except ValidationError as e:
        #         for err in e.errors():
        #             loc = '.'.join(str(p) for p in err['loc'])
        #             input_val = err.get('input', '')
        #             errors.append(f"{tag_id} -> field '{loc}: {err['msg']}")
        #         continue

        for t in tags:
            if t.id in seen_ids:
                errors.append(f"duplicate tag id - {t.id}")
            seen_ids[t.id] = t.id

            for a in t.aliases:
                if a in seen_aliases:
                    errors.append(
                        f"alias '{a}' is in both '{seen_aliases[a]}'. Please map it to one tag only."
                    )
                else:
                    seen_aliases[a] = t.id

        if errors:
            raise ValueError("food-tags.yaml: " + "; ".join(errors))
        return tags


class Region(BaseModel):

    model_config = ConfigDict(extra="forbid")
    id: Slug
    name: str
    country: str
    parent: str | None = None
    known_for: list[Slug] = []


class SubRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Slug
    name: str
    known_for: list[Slug] = []
    notes: Optional[str] = None


class RegionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Slug
    name: str
    country: str
    climate: Optional[Climate] = None
    known_for: list[Slug] = []
    notes: Optional[str] = None
    subregions: Optional[list[SubRegion]] = []

    def flatten(self) -> list[Region]:
        """
        Hierachy -> flat Region views (self first, then subregions).
        """

        out = [
            Region(
                id=self.id,
                name=self.name,
                country=self.country,
                known_for=self.known_for,
            )
        ]
        out += [
            Region(
                id=s.id,
                name=s.name,
                country=self.country,
                parent=self.id,
                known_for=s.known_for,
            )
            for s in self.subregions
        ]
        return out

    @staticmethod
    def _load_regions(entries: list[dict]) -> dict[str, Region]:
        regions: dict[str, Region] = {}
        errors: list[str] = []

        for i, raw in enumerate(entries):
            region_id = (
                raw.get("id", f"index #{i}") if isinstance(raw, dict) else f"index #{i}"
            )

            try:
                entry = RegionEntry.model_validate(raw)
            except ValidationError as e:
                for err in e.errors():
                    loc = ".".join(str(p) for p in err["loc"])
                    input_val = err.get("input", "")
                    errors.append(f"{region_id} -> field '{loc}: {err['msg']} \n\n")
                continue

            for region in entry.flatten():
                if region.id in regions:
                    errors.append(f"Duplicate region id '{region.id}'")
                else:
                    regions[region.id] = region

        if errors:
            raise ValueError("regions.yaml: " + ", ".join(errors))
        return regions


if __name__ == "__main__":
    from pathlib import Path
    import yaml
    from pydantic import ValidationError
    import sys

    ROOT = Path(__file__).resolve().parent.parent
    WINES_DIR = ROOT / "data" / "wines"
    FOOD_DIR = ROOT / "data" / "food-tags.yaml"
    REGION_DIR = ROOT / "data" / "regions.yaml"

    if args := sys.argv[1:]:
        targets = [Path(a) for a in args]
    else:
        targets = sorted(WINES_DIR.glob("*.yaml")) + sorted(WINES_DIR.glob("*.yml"))

        if not targets:
            print(f"No wine files found in {WINES_DIR}!!!")
            raise SystemExit(2)

    failures = 0

    for path in targets:
        try:
            wine = Wine.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))

        except ValidationError as e:
            print(f"FAIL {path.relative_to(ROOT)}!!!")
            for err in e.errors():
                loc = ".".join(str(p) for p in err["loc"]) or "(root)"
                print(f" - {loc}: {err['msg']}")

            continue

        print(f"{wine.name} is valid!")

        if gaps := wine.missing_info():
            print(f" still to study {', '.join(gaps)}")

    if failures:
        print(f"{failures} file failed validation!")
    print(f"{len(targets)} wine files are valid.")

    try:
        tags = FoodTag.load_food_tags(
            yaml.safe_load(FOOD_DIR.read_text(encoding="utf-8"))
        )
        print("Food Tag Valid!")
    except (ValueError, ValidationError) as e:
        failures += 1
        print(f"FAIL! {e}")

    try:
        reg = RegionEntry._load_regions(
            yaml.safe_load(REGION_DIR.read_text(encoding="utf-8"))
        )
        print("Region File Valid!")
    except (ValueError, ValidationError) as e:
        failures += 1
        print(f"FAIL! {e}")

    sys.exit(1 if failures else 0)
