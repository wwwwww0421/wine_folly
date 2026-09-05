"""
Wine Folly - Render the static site. (src/build_site.py)

This turns the engine output into browsable HTML. Runs after export.py so that the site/data/*.json files are ready on disk to access.
These pages are in human readable style.

    site/index.html             ask a dish, browse in
    site/wines.html             wines and grouped by type
    site/foods.html             all the food tags
    site/regions.html           all the region and grouped by country
    site/flavours.html          the tasting vocabulary
    site/wines/<id>.html        single wine, with scales, serving, pairings, similar wines
    site/foods/<id>.html        single dish, with ranks of wines with reasons
    site/regions/<id>.html      single region, with its wines and flavour profile
    site/assets/style.css       the style of the site.

Every page is reachable without JavaScript - search function enhances a site that already work. Links are relative so the whole thing runs from a file:// paht or any subdirectory on GitHub Pages.
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .engine import Engine
from .schema import SCALE_DIMS

SITE = Path("site")
TEMPLATES = Path("templates")

SCALE_LABELS = {
    "body": "Body",
    "sweetness": "Sweetness",
    "tannin": "Tannin",
    "acidity": "Acidity",
    "alcohol": "Alcohol",
}

WINE_TYPE_ORDER = [
    "sparkling",
    "light-white",
    "full-white",
    "aromatic-white",
    "rosé",
    "light-red",
    "medium-red",
    "full-red",
    "dessert",
]


def _serving_facts(wine: dict) -> dict:
    """
    Turns the raw serving block into display string, so the template holds no formatting logic.
    """

    s = wine.get("serving") or {}

    facts = {}

    if s.get("temp_c"):
        facts["temp"] = f"{s['temp_c'][0]-s['temp_c'][1]}ºC"
    if s.get("glass"):
        facts["glass"] = s["glass"].replace("-", " ")
    if s.get("decant_minutes"):
        x = s["decant_minutes"]
        facts["decant"] = f"{x}+ mins" if x >= 60 else f"{x} mins"
    if s.get("cellar_years"):
        facts["cellar"] = f"{s['cellar_years'][0]}-{s['cellar_years'][1]} years"
    return facts


class SiteBuilder:
    def __init__(
        self, engine: Engine, out_dir: Path = SITE, templates: Path = TEMPLATES
    ):
        self.engine = engine
        self.con = engine.con
        self.out_dir = out_dir
        self.env = Environment(
            loader=FileSystemLoader(templates),  # the path goes HERE
            autoescape=select_autoescape(
                ["html"]
            ),  # a list of extensions, not the path
            trim_blocks=True,
            lstrip_blocks=True,
        )

        self.index = json.loads(
            (out_dir / "data" / "index.json").read_text(encoding="utf-8")
        )

        self.env.globals.update(scale_dims=SCALE_DIMS, scale_labels=SCALE_LABELS)
        self.globals = {
            "counts": self.index["counts"],
            "built_on": date.today().isoformat(),
        }

    def _render(self, template: str, path: Path, **context) -> None:
        depth = len(path.relative_to(self.out_dir).parts) - 1
        html = self.env.get_template(template).render(
            root="../" * depth, **self.globals, **context
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")

    def _wine_rows(self, wine_ids: list[str] | None = None) -> list[dict]:
        """
        Lightweight wine dicts for list rendering (id, name, type, flavours).
        """

        rows = self.index["wines"]
        if wine_ids is None:
            return rows

        keep = set(wine_ids)
        return [w for w in rows if w["id"] in keep]

    #### SITE PAGES
    def home(self) -> None:
        recent = sorted(self.index["wines"], key=lambda w: w["name"])[:8]
        self._render(
            "index.html",
            self.out_dir / "index.html",
            foot_tags=self.index["food_tags"],
            recent=recent,
        )

    def wine_page(self) -> int:
        for row in self.index["wines"]:
            detail = self.engine.wine_detail(row["id"])
            self._render(
                "wine.html",
                self.out_dir / "wines" / f"{row['id']}.html",
                d=detail,
                serving=_serving_facts(detail.wine),
            )
        return len(self.index["wines"])

    def food_page(self) -> int:
        tags = self.index["food_tags"]
        for tag in tags:
            payload = json.loads(
                (self.out_dir / "data" / "pairings" / f"{tag['id']}.json").read_text(
                    encoding="utf-8"
                )
            )
            self._render(
                "food.html",
                self.out_dir / "foods" / f"{tag['id']}.html",
                payload=payload,
            )

        return len(tags)

    def region_page(self) -> int:
        by_id = {r["id"]: r for r in self.index["regions"]}
        for region in self.index["regions"]:
            detail = self.engine.region_detail(region["id"])
            parent = (
                by_id.get(region.get("parent_id")) if region.get("parent_id") else None
            )
            self._render(
                "region.html",
                self.out_dir / "regions" / f"{region['id']}.html",
                d=detail,
                parent=parent,
            )

        return len(self.index["regions"])

    #### Get Indexes

    def wine_index(self) -> None:
        groups = []
        for wine_type in WINE_TYPE_ORDER:
            wines = [
                w for w in self.index["wines"] if wine_type in (w["wine_type"] or [])
            ]
            if wines:
                groups.append({"name": wine_type.replace("-", " "), "wines": wines})
        self._render(
            "list.html",
            self.out_dir / "wines.html",
            heading="Wines",
            subtitle=f"{self.index['counts']['wines']} in my notes.",
            groups=groups,
        )

    def food_index(self) -> None:
        items = [
            {"href": f"foods/{t['id']}.html", "label": t["label"]}
            for t in self.index["food_tags"]
        ]
        self._render(
            "list.html",
            self.out_dir / "foods.html",
            heading="Food",
            subtitle="Pick a dish to see what goes with it!",
            groups=[{"name": "Every dish I have tagged", "links": items}],
        )

    def region_index(self) -> None:
        wines_by_region: dict[str, int] = {}
        for w in self.index["wines"]:
            for r in w["regions"]:
                wines_by_region[r] = wines_by_region.get(r, 0) + 1

        by_country: dict[str, list] = {}
        for r in self.index["regions"]:
            by_country.setdefault(r["country"] or "Elsewhere", []).append(r)

        groups = []
        for (
            country,
            regions,
        ) in sorted(by_country.items()):
            regions.sort(key=lambda x: (-wines_by_region.get(r["id"], 0), r["name"]))
            items = []
            for r in regions:
                count = wines_by_region.get(r["id"], 0)
                label = f"{r['name']} ({count})" if count else r["name"]
                items.append({"href": f"regions/{r['id']}.html", "label": label})
            groups.append({"name": country, "links": items})

        covered = sum(1 for r in self.index["regions"] if wines_by_region.get(r["id"]))
        self._render(
            "list.html",
            self.out_dir / "regions.html",
            heading="Regions",
            subtitles=f"{covered} of {len(self.index['regions'])} have a wine in my notes so far",
            group=groups,
        )

    def flavour_index(self) -> None:
        flavours = []
        for entry in self.index["flavours"]:
            wines = self.engine.by_flavour(entry["flavour"])
            flavours.append({**entry, "wines": wines})
        self._render("flavours.html", self.out_dir / "flavours.html", flavours=flavours)

    #### EXECUTE
    def run(self) -> dict[str, int]:
        for stale in ("wines", "foods", "regions"):
            shutil.rmtree(self.out_dir / stale, ignore_errors=True)

        assets = self.out_dir / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        shutil.copy(TEMPLATES / "assets" / "style.css", assets / "style.css")

        self.home()

        counts = {
            "wines": self.wine_page(),
            "foods": self.food_page(),
            "regions": self.region_page(),
        }

        self.wine_index()
        self.food_index()
        self.region_index()
        self.flavour_index()
        return counts
