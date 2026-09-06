"""
The query layer - ENGINE!

Reads wine.db that produced by build.py.
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

from .schema import SCALE_DIMS

STRENGTH_SCORE = {"perfect": 3.0, "great": 2.0, "good": 1.0}


def normalise(text: str) -> str:
    """
    Lowercase, strip accents and punctuation - applied at index time and query time.
    """

    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


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
    flavours: list[str]


@dataclass
class RegionDetail:
    region: dict
    wines: list[dict]
    flavour_profile: list[str]
    siblings: list[dict]


class Engine:
    def __init__(self, db_path: Path | str = "build/wine.db"):
        if not Path(db_path).exists():
            raise FileNotFoundError(f"{db_path} not found - try run build.py.")
        self.con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row

    def _wine_record(self, wine_id: str) -> dict | None:
        row = self.con.execute(
            "SELECT data FROM wines WHERE id = ?", (wine_id,)
        ).fetchone()
        if row and row["data"]:
            return json.loads(row["data"]) if row else None
        return None

    def _regions_of(self, record: dict) -> list[dict]:
        out = []
        for rid in record.get("regions", []):
            r = self.con.execute(
                "SELECT id, name, country, parent_id FROM regions WHERE id = ?", (rid,)
            ).fetchone()
            if r:
                out.append(dict(r))
        return out

        # def resolve(self, query: str, limit: int = 8) -> list[Match]:
        """
        Input: Free text related to wine, food or regions.
        Logic: Exact alias hits win, otherwise FTS5 prefix matching gives search-as-you-type behaviour.
        Output: list of matches.
        """

        ### Search Box
        q = normalise(query)
        if not q:
            return []

        matches: list[Match] = []
        seen: set[tuple[str, str]] = set()

        def add(kind: str, rid: str, label: str) -> None:
            if (kind, rid) not in seen:
                seen.add((kind, rid))
                matches.append(Match(kind, rid, label))

        for row in self.con.execute(
            "SELECT a.tag_id, f.label FROM aliases a JOIN food_tags f ON f.id = a.tag_id WHERE a.alias = ?",
            (q,),
        ):
            add("food", row["tag_id"], row["label"])

        fts_query = " OR ".join(f'"{w}"*' for w in q.split())
        try:
            rows = self.con.execute(
                "SELECT doc_type, ref_id, rank FROM search_index WHERE search_index MATCH ? ORDER BY rank LIMIT ?",
                (fts_query, limit * 3),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

        order = {"food": 0, "wine": 1, "region": 2}
        # for row in sorted(rows, key=lambda r: order.get(r["doc_type"], 9)):
        #     add(
        #         row["doc_type"],
        #         row["ref_id"],
        #         self.label_for(row["doc_type"], row["ref_id"]),
        #     )
        for row in sorted(rows, key=lambda r: order.get(r["doc_type"], 9)):
            if row["doc_type"] == "food" and matches:
                continue

            add(
                row["doc_type"],
                row["ref_id"],
                self.label_for(row["doc_type"], row["ref_id"]),
            )

        return matches[:limit]

    def resolve(self, query: str, limit: int = 8) -> list[Match]:
        """
        Resolve free-text into food, wine, and region matches.

        Resolution priority:
        1. Exact whole-query food alias.
        2. Exact aliases for individual query terms.
        3. FTS prefix matching for remaining/general search.

        Exact food aliases are authoritative: once a food term has been
        resolved exactly, FTS is not allowed to add unrelated food tags.
        FTS may still contribute wine and region matches.

        Results are deduplicated and capped at `limit`.
        """
        q = normalise(query)
        if not q or limit <= 0:
            return []

        matches: list[Match] = []
        seen: set[tuple[str, str]] = set()
        exact_food_ids: set[str] = set()

        def add(kind: str, rid: str, label: str) -> None:
            key = (kind, rid)
            if key not in seen and len(matches) < limit:
                seen.add(key)
                matches.append(Match(kind, rid, label))

        # ---------------------------------------------------------------
        # 1. Exact whole-query food alias.
        #
        # This must happen before token matching so that an alias such as
        # "aged cured meats" or "truffle cheddar" wins as a single match.
        # ---------------------------------------------------------------
        rows = self.con.execute(
            """
            SELECT a.tag_id, f.label
            FROM aliases a
            JOIN food_tags f ON f.id = a.tag_id
            WHERE a.alias = ?
            """,
            (q,),
        ).fetchall()

        for row in rows:
            exact_food_ids.add(row["tag_id"])
            add("food", row["tag_id"], row["label"])

        # If the entire query was an exact food alias, there is no reason
        # for FTS to add broader food interpretations.
        if exact_food_ids:
            return matches[:limit]

        # ---------------------------------------------------------------
        # 2. Exact aliases for individual terms.
        #
        # Example:
        #     "truffle cheddar"
        #
        # should resolve to:
        #     truffle
        #     aged-cured-meats-cheeses
        #
        # rather than allowing FTS to invent an additional unrelated
        # food match such as chocolate-desserts.
        # ---------------------------------------------------------------
        terms = q.split()

        for term in terms:
            rows = self.con.execute(
                """
                SELECT a.tag_id, f.label
                FROM aliases a
                JOIN food_tags f ON f.id = a.tag_id
                WHERE a.alias = ?
                """,
                (term,),
            ).fetchall()

            for row in rows:
                # exact_food_ids.add(row["tag_id"])
                add("food", row["tag_id"], row["label"])

        # ---------------------------------------------------------------
        # 3. FTS fallback/general search.
        #
        # FTS is useful for search-as-you-type, wines, regions, and terms
        # that aren't exact aliases. But it must not add extra food tags
        # when exact food aliases have already been found.
        # ---------------------------------------------------------------
        fts_query = " OR ".join(f'"{word}"*' for word in terms)

        try:
            rows = self.con.execute(
                """
                SELECT doc_type, ref_id, rank
                FROM search_index
                WHERE search_index MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, limit * 3),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

        order = {
            "food": 0,
            "wine": 1,
            "region": 2,
        }

        for row in sorted(rows, key=lambda r: order.get(r["doc_type"], 9)):
            kind = row["doc_type"]
            rid = row["ref_id"]

            # Exact food aliases are authoritative. Don't let FTS add
            # additional food interpretations for the same query.
            if kind == "food":
                if exact_food_ids:
                    continue

                # Avoid adding an FTS food result that is already present.
                if rid in exact_food_ids:
                    continue

            add(
                kind,
                rid,
                self.label_for(kind, rid),
            )

            if len(matches) >= limit:
                break

        return matches[:limit]

    def label_for(self, kind: str, rid: str) -> str:
        table = {"food": "food_tags", "wine": "wines", "region": "regions"}[kind]
        col = "label" if kind == "food" else "name"
        row = self.con.execute(
            f"SELECT {col} AS l FROM {table} WHERE id = ?", (rid,)
        ).fetchone()
        return row["l"] if row else rid

    ### Food to Wine Pairing

    def pair_food(self, tag_ids: list[str], limit: int = 50) -> list[PairedWine]:
        """
        Multi-tag score merge: SUM strengths across matched tags, drop any wine that lists a matched tag under `avoid`.
        This is the same arthmetic app.js repeats client-side.
        """

        if not tag_ids:
            return []
        placeholder = ",".join(["?"] * len(tag_ids))

        excluded = {
            r["wine_id"]
            for r in self.con.execute(
                f"SELECT wine_id FROM pairings WHERE is_avoid = 1 AND tag_id IN ({placeholder})",
                tag_ids,
            )
        }

        acc: dict[str, PairedWine] = {}
        for row in self.con.execute(
            f"SELECT p.wine_id, w.name, p.tag_id, p.strength, p.why FROM pairings p JOIN wines w ON w.id = p.wine_id WHERE p.is_avoid = 0 AND p.tag_id IN ({placeholder})",
            tag_ids,
        ):
            if row["wine_id"] in excluded:
                continue
            pw = acc.get(row["wine_id"])
            if pw is None:
                pw = acc[row["wine_id"]] = PairedWine(
                    wine_id=row["wine_id"],
                    name=row["name"],
                    score=0.0,
                    strength=row["strength"],
                    why=[],
                    matched_tags=[],
                )
            pw.score += STRENGTH_SCORE.get(row["strength"], 0.0)
            pw.matched_tags.append(row["tag_id"])
            pw.why.append(row["why"])
            if STRENGTH_SCORE.get(row["strength"], 0) > STRENGTH_SCORE.get(
                pw.strength, 0
            ):
                pw.strength = row["strength"]

        for pw in acc.values():
            pw.score += 0.5 * (len(set(pw.matched_tags)) - 1)
            record = self._wine_record(pw.wine_id) or {}
            pw.flavours = record.get("flavours", [])
            pw.regions = self._regions_of(record)
            pw.also_try = record.get("also_try", [])

        return sorted(acc.values(), key=lambda p: (-p.score, p.name))[:limit]

    def pair_text(
        self, query: str, limit: int = 10
    ) -> tuple[list[Match], list[PairedWine]]:
        """
        Free text straight to pairings. Returns the tags it matched too, so the UI can show 'interpreting as: Cured fish'.
        """
        foods = [m for m in self.resolve(query) if m.kind == "food"]
        return foods, self.pair_food([m.id for m in foods], limit)

    ### wine -> info

    def wine_detail(self, wine_id: str) -> WineDetail | None:
        record = self._wine_record(wine_id)
        if record is None:
            return None

        pairs, avoid = [], []
        for row in self.con.execute(
            "SELECT p.tag_id, p.strength, p.why, p.is_avoid, COALESCE(f.label, p.tag_id) AS label FROM pairings p LEFT JOIN food_tags f ON f.id = p.tag_id WHERE p.wine_id = ?",
            (wine_id,),
        ):
            item = {"tag_id": row["tag_id"], "label": row["label"], "why": row["why"]}
            if row["is_avoid"]:
                avoid.append(item)
            else:
                pairs.append({**item, "strength": row["strength"]})

        pairs.sort(key=lambda p: -STRENGTH_SCORE.get(p["strength"], 0))

        also_try = []
        for other in record.get("also_try", []):
            row = self.con.execute(
                "SELECT id, name FROM wines WHERE id = ?", (other,)
            ).fetchone()
            if row:
                also_try.append(dict(row))

        return WineDetail(
            wine=record,
            pairs_with=pairs,
            avoid=avoid,
            regions=self._regions_of(record),
            similar=self.similar(wine_id),
            also_try=also_try,
            flavours=record.get("flavours", []),
        )

    ### region -> wines
    def region_detail(self, region_id: str) -> RegionDetail | None:
        row = self.con.execute(
            "SELECT id, name, country, parent_id FROM regions WHERE id = ?",
            (region_id,),
        ).fetchone()
        if row is None:
            return None
        region = dict(row)

        children = {
            r["id"]
            for r in self.con.execute(
                "SELECT id FROM regions WHERE parent_id = ?", (region_id,)
            )
        }
        wanted = {region_id} | children

        wines, flavour_counts = [], {}

        for wrow in self.con.execute("SELECT id, name, data FROM wines ORDER BY name"):
            record = json.loads(wrow["data"])
            if not (set(record.get("regions", [])) & wanted):
                continue
            wines.append(
                {
                    "id": wrow["id"],
                    "name": wrow["name"],
                    "flavours": record.get("flavours", []),
                    "wine_type": record.get("wine_type", []),
                }
            )

            for f in record.get("flavours", []):
                flavour_counts[f] = flavour_counts.get(f, 0) + 1

        siblings = [
            dict(r)
            for r in self.con.execute(
                "SELECT id, name FROM regions WHERE country = ? AND id != ?",
                (region["country"], region_id),
            )
        ]
        profile = [f for f, _ in sorted(flavour_counts.items(), key=lambda kv: -kv[1])][
            :8
        ]
        return RegionDetail(region, wines, profile, siblings)

    ### Similarity & Flavouring Browsing

    def similar(self, wine_id: str, limit: int = 5) -> list[SimilarWine]:
        """
        Cosine similar over the scale dimensions BOTH wines have, so a missing paramter degrades gracefully instead of crashing.
        Shared flavours break ties - two wines can score alike numerically and taste nothing alike!
        """
        base = self._wine_record(wine_id)
        if base is None:
            return []

        base_vec = {d: base[d] for d in SCALE_DIMS if base.get(d) is not None}
        base_flavours = set(base.get("flavours", []))
        if not base_vec:
            return []

        out: list[SimilarWine] = []
        for row in self.con.execute(
            "SELECT id, name, data FROM wines WHERE id != ?", (wine_id,)
        ):
            other = json.loads(row["data"])
            dims = [d for d in base_vec if other.get(d) is not None]
            if not dims:
                continue
            dot = sum(base_vec[d] * other[d] for d in dims)
            mag = math.sqrt(sum(base_vec[d] ** 2 for d in dims)) * math.sqrt(
                sum(other[d] ** 2 for d in dims)
            )
            if not mag:
                continue

            shared = sorted(base_flavours & set(other.get("flavours", [])))
            out.append(
                SimilarWine(
                    wine_id=row["id"],
                    name=row["name"],
                    similarity=round(dot / mag, 4),
                    shared_dim=dims,
                    shared_flavours=shared,
                )
            )

        out.sort(key=lambda s: (-s.similarity, -len(s.shared_flavours), s.name))
        return out[:limit]

    def by_flavour(self, flavour: str, limit: int = 20) -> list[dict]:
        """
        I liked the beeswax note -> every wine sharing it.
        """
        target = normalise(flavour)
        out = []
        for row in self.con.execute("SELECT id, name, data FROM  wines ORDER BY name"):
            record = json.loads(row["data"])
            hits = [f for f in record.get("flavours", []) if target in normalise(f)]
            if hits:
                out.append(
                    {"id": row["id"], "name": row["name"], "matched_flavours": hits}
                )

        return out[:limit]

    def all_flavours(self) -> list[dict]:
        """
        Flavour index for the browse page: [{flavour, count}].
        """
        counts: dict[str, int] = {}
        for row in self.con.execute("SELECT data FROM wines"):
            for f in json.loads(row["data"]).get("flavours", []):
                counts[f] = counts.get(f, 0) + 1

        return [
            {"flavour": f, "count": c}
            for f, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    def to_json(self, obj) -> dict:
        """Dataclass -> plain dict, ready for export.py"""
        return asdict(obj)
