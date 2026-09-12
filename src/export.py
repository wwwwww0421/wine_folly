"""
Wine Folly Exporter - Freeze the engine's answers into static files. (src/export.py)

The offline app must work no Python running, so every answer the engine can give is pre-computed here and written as JSON.
App.js then only *reads* files and does the small multi-tag score merge.

Outputs are all under site/data:

    index.json
    search-docs.json
    wines/<id>.json
    regions/<id>.json
    flavours.json

"""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from .engine import Engine, normalise
from .schema import SCALE_DIMS

SITE_DATA = Path("site/data")


def _write(path: Path, payload) -> int:
    """Write compact JSON (no spaces) - these ship to a phone!"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


class Exporter:
    def __init__(self, engine: Engine, out_dir: Path = SITE_DATA):
        self.engine = engine
        self.con = engine.con
        self.out_dir = out_dir

    def _wine_ids(self) -> list[int]:
        """Return all wine IDs in the database."""
        return [r["id"] for r in self.con.execute("SELECT id FROM wines ORDER BY id")]

    def _tag_ids(self) -> list[int]:
        """Return all tag IDs in the database."""
        return [
            r["id"] for r in self.con.execute("SELECT id FROM food_tags ORDER BY id")
        ]

    def _region_ids(self) -> list[int]:
        """Return all region IDs in the database."""
        return [r["id"] for r in self.con.execute("SELECT id FROM regions ORDER BY id")]

    def export_wines(self) -> int:
        """Export all wines to JSON files."""
        total_bytes = 0
        for wine_id in self._wine_ids():
            detail = self.engine.wine_detail(wine_id)
            payload = {
                "wine": detail.wine,
                "pairs_with": detail.pairs_with,
                "avoid": detail.avoid,
                "regions": detail.regions,
                "similar": [asdict(s) for s in detail.similar],
                "also_try": detail.also_try,
                "flavours": detail.flavours,
            }
            total_bytes += _write(self.out_dir / "wines" / f"{wine_id}.json", payload)

        return total_bytes

    def export_pairing(self) -> int:
        """
        One file per food tag.
        App.js loads one per matched tag and merges the scores client-side.
        """
        total_bytes = 0
        for tag_id in self._tag_ids():
            row = self.con.execute(
                """
                SELECT f.label, c.id AS category_id, c.label AS category_label,
                       c.pairing_principle
                FROM food_tags f JOIN food_categories c ON c.id = f.category_id
                WHERE f.id = ?
                """,
                (tag_id,),
            ).fetchone()
            payload = {
                "tag": tag_id,
                "label": row["label"],
                "category": row["category_id"],
                "category_label": row["category_label"],
                "pairing_principle": row["pairing_principle"],
                "aliases": [
                    r["alias"]
                    for r in self.con.execute(
                        "SELECT alias FROM aliases WHERE tag_id = ?", (tag_id,)
                    )
                ],
                "wines": [asdict(w) for w in self.engine.pair_food([tag_id], limit=50)],
                "avoid": [
                    r["wine_id"]
                    for r in self.con.execute(
                        "SELECT wine_id FROM pairings WHERE tag_id = ? AND is_avoid = 1",
                        (tag_id,),
                    )
                ],
            }
            total_bytes += _write(self.out_dir / "pairings" / f"{tag_id}.json", payload)
        return total_bytes

    def export_regions(self) -> int:
        total_bytes = 0
        for region_id in self._region_ids():
            detail = self.engine.region_detail(region_id)
            if detail is None:
                continue
            total_bytes += _write(
                self.out_dir / "regions" / f"{region_id}.json", asdict(detail)
            )
        return total_bytes

    def export_search_docs(self) -> int:
        """
        MiniSearch builds its index from this in the browser. Text is normalised here so index-time and query-time rules match exactly (accents stripped, lowercased) - the app must call the same normalise() function before search.
        """
        docs = []
        for row in self.con.execute("SELECT id, name, data FROM wines"):
            record = json.loads(row["data"])
            why_text = " ".join(
                r["why"]
                for r in self.con.execute(
                    "SELECT why FROM pairings WHERE wine_id = ?", (row["id"],)
                )
            )
            doc = {
                "id": f"wine:{row['id']}",
                "kind": "wine",
                "ref": row["id"],
                "title": row["name"],
                "grapes": normalise(" ".join(record.get("grapes", []))),
                # A list of phrases, not one flattened string: a flavour
                # like "black-cherry" normalises to two words, and a query
                # for "black" alone should only get partial credit for it,
                # not the full weight of a genuine one-word match.
                "flavours": [normalise(f) for f in record.get("flavours", [])],
                "regions": normalise(
                    " ".join(r["name"] for r in self.engine._regions_of(record))
                ),
                "wine_type": normalise(" ".join(record.get("wine_type", []))),
                "notes": normalise(record.get("notes") or ""),
                "why": normalise(why_text),
            }
            # Scale values ride along unnormalised (used numerically, not as
            # text) so the browser can match attribute words like "sweet" or
            # "tannic" against how the wine actually tastes.
            doc.update({d: record.get(d) for d in SCALE_DIMS})
            docs.append(doc)

        for row in self.con.execute("SELECT id, label FROM food_tags"):
            aliases = [
                r["alias"]
                for r in self.con.execute(
                    "SELECT alias FROM aliases WHERE tag_id = ?", (row["id"],)
                )
            ]
            docs.append(
                {
                    "id": f"food:{row['id']}",
                    "kind": "food",
                    "ref": row["id"],
                    "title": row["label"],
                    "label": normalise(row["label"]),
                    # A list of phrases, not one flattened string - see the
                    # comment on the wine doc's "flavours" above. Without
                    # this, one incidental word inside a long multi-word
                    # alias (e.g. "green" inside "green curry") scores as
                    # high as a real one-word match like "truffle".
                    "aliases": [normalise(a) for a in aliases],
                }
            )

        for row in self.con.execute("SELECT id, name, country, known_for FROM regions"):
            docs.append(
                {
                    "id": f"region:{row['id']}",
                    "kind": "region",
                    "ref": row["id"],
                    "title": row["name"],
                    "label": normalise(row["name"]),
                    "country": normalise(row["country"] or ""),
                    "known_for": normalise(row["known_for"] or ""),
                }
            )

        return _write(self.out_dir / "search-docs.json", docs)

    def export_index(self) -> int:
        wines = []
        for row in self.con.execute("SELECT id, name, data FROM wines"):
            record = json.loads(row["data"])
            wines.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "wine_type": record.get("wine_type", []),
                    "grapes": record.get("grapes", []),
                    "flavours": record.get("flavours", []),
                    "regions": record.get("regions", []),
                    "price_band": record.get("price_band"),
                    "popularity": record.get("popularity"),
                    **{d: record.get(d) for d in SCALE_DIMS},
                }
            )

        payload = {
            "counts": {
                "wines": len(wines),
                "food_tags": len(self._tag_ids()),
                "regions": len(self._region_ids()),
            },
            "wines": wines,
            "food_tags": [
                {
                    "id": r["id"],
                    "label": r["label"],
                    "category": r["category_id"],
                    "category_label": r["category_label"],
                    "pairing_principle": r["pairing_principle"],
                }
                for r in self.con.execute(
                    """
                    SELECT f.id, f.label, c.id AS category_id, c.label AS category_label,
                           c.pairing_principle
                    FROM food_tags f JOIN food_categories c ON c.id = f.category_id
                    ORDER BY f.label
                    """
                )
            ],
            "regions": [
                {"id": r["id"], "name": r["name"], "country": r["country"]}
                for r in self.con.execute(
                    "SELECT id, name, country FROM regions ORDER BY name"
                )
            ],
            "food_categories": [
                {
                    "id": r["id"],
                    "label": r["label"],
                    "pairing_principle": r["pairing_principle"],
                }
                # rowid order = data/food-categories.yaml order (its intended
                # display order), not alphabetical.
                for r in self.con.execute(
                    "SELECT id, label, pairing_principle FROM food_categories ORDER BY rowid"
                )
            ],
            "flavours": self.engine.all_flavours(),
        }
        return _write(self.out_dir / "index.json", payload)

    def export_flavours(self) -> int:
        return _write(self.out_dir / "flavours.json", self.engine.all_flavours())

    def run(self) -> dict[str, int]:
        """Run all exports and return a dict of file sizes."""
        if self.out_dir.exists():
            shutil.rmtree(self.out_dir)

        return {
            "index.json": self.export_index(),
            "search-docs.json": self.export_search_docs(),
            "flavours.json": self.export_flavours(),
            "wines/": self.export_wines(),
            "pairings/": self.export_pairing(),
            "regions/": self.export_regions(),
        }


def write_audit_excel(
    engine: Engine, path: Path = Path("build/wine_audit.xlsx")
) -> Path:
    """Write an Excel file with all wines, pairings, and regions for auditing."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Wines"

    headers = [
        "id",
        "name",
        "wine_type",
        "grapes",
        *SCALE_DIMS,
        "price_band",
        "popularity",
        "regions",
        "flavours",
        "pairings",
        "avoid",
        "missing",
    ]
    ws.append(headers)

    con = engine.con
    for row in con.execute("SELECT id, name, data FROM wines ORDER BY id"):
        record = json.loads(row["data"])
        detail = engine.wine_detail(row["id"])
        missing = [d for d in SCALE_DIMS if record.get(d) is None]
        if not detail.pairs_with:
            missing.append("pairings")
        ws.append(
            [
                row["id"],
                row["name"],
                "/".join(record.get("wine_type", [])),
                ", ".join(record.get("grapes", [])),
                *[record.get(d) for d in SCALE_DIMS],
                record.get("price_band"),
                record.get("popularity"),
                ", ".join(r["name"] for r in detail.regions),
                ", ".join(record.get("flavours", [])),
                len(detail.pairs_with),
                len(detail.avoid),
                ", ".join(missing),
            ]
        )

    header_font = Font(name="Arial", bold=True)
    for cell in ws[1]:
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")
    for col, header in enumerate(headers, start=1):
        width = max(len(header) + 2, 10)  # Minimum width of 10
        if header in ("flavours", "regions", "grapes", "missing"):
            width = 34
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
