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

import hashlib
import json
import re
import shutil
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .engine import Engine
from .schema import SCALE_DIMS

SITE = Path("site")
TEMPLATES = Path("templates")

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

# Country name (exactly as written in data/regions.yaml) -> lowercase ISO
# alpha-2 code(s) used as the <g>/<path> id or class in the vendored world
# map. A region entry like "Spain, Portugal" lights up both shapes.
COUNTRY_ISO: dict[str, list[str]] = {
    "Algeria": ["dz"],
    "Argentina": ["ar"],
    "Australia": ["au"],
    "Austria": ["at"],
    "Bulgaria": ["bg"],
    "Canada": ["ca"],
    "Chile": ["cl"],
    "China": ["cn"],
    "Croatia": ["hr"],
    "France": ["fr"],
    "Germany": ["de"],
    "Greece": ["gr"],
    "Hungary": ["hu"],
    "Italy": ["it"],
    "Morocco": ["ma"],
    "New Zealand": ["nz"],
    "Portugal": ["pt"],
    "Romania": ["ro"],
    "Slovakia": ["sk"],
    "Slovenia": ["si"],
    "South Africa": ["za"],
    "Spain": ["es"],
    "Spain, Portugal": ["es", "pt"],
    "Switzerland": ["ch"],
    "Tunisia": ["tn"],
    "Turkey": ["tr"],
    "United States": ["us"],
}


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")

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
        tags_by_category: dict[str, list] = {}
        for t in self.index["food_tags"]:
            tags_by_category.setdefault(t["category"], []).append(t)

        groups = []
        for cat in self.index["food_categories"]:
            tags = tags_by_category.get(cat["id"], [])
            if not tags:
                continue
            items = [{"href": f"foods/{t['id']}.html", "label": t["label"]} for t in tags]
            groups.append(
                {
                    "id": cat["id"],
                    "name": cat["label"],
                    "subtitle": cat["pairing_principle"],
                    "links": items,
                }
            )

        self._render(
            "list.html",
            self.out_dir / "foods.html",
            heading="Food",
            subtitle="Pick a dish to see what goes with it — grouped by why it pairs the way it does.",
            groups=groups,
        )

    def _build_world_map(self, wines_by_country: dict[str, int]) -> str:
        """
        Loads the vendored world map SVG and, for every country we have wine
        data on, wraps its shape in a link straight down to that country's
        section on the page (so clicking the map needs no JavaScript at all)
        and marks it as coloured-in. Everything else is dimmed and left
        unlinked.
        """
        tree = ET.parse(TEMPLATES / "assets" / "world-map.svg")
        root = tree.getroot()

        iso_to_country: dict[str, str] = {}
        for country, codes in COUNTRY_ISO.items():
            for code in codes:
                iso_to_country[code] = country

        def tag_subtree(el, extra_class: str) -> None:
            """
            Some countries (France, Spain, the US, China...) are drawn as
            several <path> children that each carry their own "landxx ..."
            class, rather than inheriting styling from the group. A rule
            matched directly on an element always wins over an inherited
            one regardless of specificity, so colouring only the outer
            <g> silently no-ops on those countries. Tagging every
            descendant sidesteps that entirely.
            """
            for node in el.iter():
                classes = (node.get("class") or "").split()
                if extra_class not in classes:
                    classes.append(extra_class)
                    node.set("class", " ".join(classes))

        for shape in list(root):
            tokens = {shape.get("id", ""), *(shape.get("class") or "").split()}
            iso = next((c for c in tokens if c in iso_to_country), None)
            if iso is None:
                continue

            country = iso_to_country[iso]
            count = wines_by_country.get(country, 0)

            if count:
                tag_subtree(shape, "has-wine")
                idx = list(root).index(shape)
                link = ET.Element(f"{{{SVG_NS}}}a")
                link.set("href", f"#country-{slugify(country)}")
                title = ET.SubElement(link, f"{{{SVG_NS}}}title")
                title.text = f"{country} — {count} wine{'s' if count != 1 else ''}"
                root.remove(shape)
                link.append(shape)
                root.insert(idx, link)
            else:
                tag_subtree(shape, "no-wine")

        # Scale by CSS (width:100%; height:auto) instead of the vendored
        # file's fixed pixel size, so the map fits a phone screen.
        root.set("class", "world-map")
        root.attrib.pop("width", None)
        root.attrib.pop("height", None)

        return ET.tostring(root, encoding="unicode")

    def region_index(self) -> None:
        wines_by_region: dict[str, int] = {}
        for w in self.index["wines"]:
            for r in w["regions"]:
                wines_by_region[r] = wines_by_region.get(r, 0) + 1

        by_country: dict[str, list] = {}
        for r in self.index["regions"]:
            by_country.setdefault(r["country"] or "Elsewhere", []).append(r)

        wines_by_country = {
            country: sum(wines_by_region.get(r["id"], 0) for r in regions)
            for country, regions in by_country.items()
        }

        groups = []
        for country, regions in sorted(by_country.items()):
            regions.sort(key=lambda r: (-wines_by_region.get(r["id"], 0), r["name"]))
            items = []
            for r in regions:
                count = wines_by_region.get(r["id"], 0)
                label = f"{r['name']} ({count})" if count else r["name"]
                items.append({"href": f"regions/{r['id']}.html", "label": label})
            groups.append(
                {
                    "name": country,
                    "slug": slugify(country),
                    "count": wines_by_country[country],
                    "links": items,
                }
            )

        covered = sum(1 for r in self.index["regions"] if wines_by_region.get(r["id"]))
        self._render(
            "regions.html",
            self.out_dir / "regions.html",
            heading="Regions",
            subtitle=f"{covered} of {len(self.index['regions'])} have a wine in my notes so far",
            groups=groups,
            world_map=self._build_world_map(wines_by_country),
        )

    def flavour_index(self) -> None:
        flavours = []
        for entry in self.index["flavours"]:
            wines = self.engine.by_flavour(entry["flavour"])
            flavours.append({**entry, "wines": wines})
        self._render("flavours.html", self.out_dir / "flavours.html", flavours=flavours)

    def journal(self) -> None:
        """
        Static shell only - notes.js reads the browser's IndexedDB and
        fills this page in entirely client-side, so there is no server
        data to pass here.
        """
        self._render("journal.html", self.out_dir / "journal.html")

    def _version(self) -> str:
        """
        Hash of dataset - changes whenever a note changes, which is what makes the service worker drop its old cache.
        """
        payload = json.dumps(self.index, sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:12]

    def manifest(self) -> None:
        """Relative paths throughout: a project site lives at repo, not domain root."""
        payload = {
            "name": "Wine Folly",
            "short_name": "WineFolly",
            "description": "An offline wine pairing and serving search engine",
            "start_url": "./index.html",
            "scope": "./",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#EDEEE9",
            "theme_color": "#7E2B3C",
            "icons": [
                {
                    "src": "assets/icons/icon-192.png",
                    "size": "192x192",
                    "type": "image/png",
                },
                {
                    "src": "assets/icons/icon-512.png",
                    "size": "512x512",
                    "type": "image/png",
                },
                {
                    "src": "assets/icons/icon-maskable-512.png",
                    "size": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
        }
        (self.out_dir / "manifest.webmanifest").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    def service_worker(self) -> None:
        """Written to the SITE ROOT so its scope covers every page."""
        candidates = [
            "index.html",
            "wines.html",
            "foods.html",
            "regions.html",
            "flavours.html",
            "journal.html",
            "assets/style.css",
            "assets/app.js",
            "assets/notes.js",
            "assets/register-sw.js",
            "assets/icons/icon-192.png",
            "data/search-doc.json",
            "data/index.json",
        ]
        shell = ["./"] + [f"./{c}" for c in candidates if (self.out_dir / c).exists()]

        tempalte = (TEMPLATES / "assets" / "sw.js.template").read_text(encoding="utf-8")
        source = tempalte.replace("__VERSION__", self._version()).replace(
            "__SHELL__", json.dumps(shell)
        )
        (self.out_dir / "sw.js").write_text(source, encoding="utf-8")

    #### EXECUTE
    def run(self) -> dict[str, int]:
        for stale in ("wines", "foods", "regions"):
            shutil.rmtree(self.out_dir / stale, ignore_errors=True)

        assets = self.out_dir / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        for asset in ("style.css", "app.js", "notes.js", "register-sw.js"):
            shutil.copy(TEMPLATES / "assets" / asset, assets / asset)
        shutil.copytree(
            TEMPLATES / "assets" / "icons",
            assets / "icons",
            dirs_exist_ok=True,
        )

        shutil.copy(
            TEMPLATES / "assets" / "icons" / "favicon.ico",
            self.out_dir / "favicon.ico",
        )
        (self.out_dir / ".nojekyll").touch()

        self.home()
        self.journal()

        counts = {
            "wines": self.wine_page(),
            "foods": self.food_page(),
            "regions": self.region_page(),
        }

        self.manifest()
        self.service_worker()
        self.wine_index()
        self.food_index()
        self.region_index()
        self.flavour_index()
        return counts
