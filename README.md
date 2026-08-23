# 🍷 Sommelier — A Personal, Offline Wine Pairing Engine

A Vivino-style companion built from my own Wine Folly study notes.
Ask it *"What wine goes with truffle cheddar, and why?"* — fully offline,
from my phone or laptop, powered by a dataset I author myself as I read.

---

## 1. What This Is

- **A wine-centric knowledge base.** Wines are the only real entities.
  Each wine YAML carries everything: taste profile, serving knowledge,
  regions, sourcing notes — and its own food pairings with hand-written
  explanations from my notes.
- **Tag-based pairing lookup.** No food database, no ML. Wines declare
  which food categories they pair with (mirroring Wine Folly's own
  category charts) and *why*. A small shared tag vocabulary makes
  free-text search ("truffle cheddar") resolve to the right tags.
- **A static, offline PWA.** All heavy lifting happens in Python at build
  time. The "app" is pre-computed JSON + HTML on GitHub Pages, installable
  to a phone home screen, working in airplane mode.

### Core use cases
1. **Food → Wine:** type "truffle cheddar" → resolves to food tags →
   ranked wine styles, each with my hand-written *why*, plus similar
   alternatives.
2. **Wine → Knowledge:** open any wine style → taste profile, serving
   temperature, glassware, decanting, cellaring, regions, price band,
   and my notes on where to buy it.
3. **Explore:** browse by region, grape, body, or flavor; full-text
   search across everything.

### Guiding principles
- Personal use only — paraphrase knowledge, never reproduce Wine Folly's
  text, charts, or infographics if this is ever shared.
- Notes stay low-friction: adding a wine = writing one YAML file.
- One command from notes → deployed app. Everything in git.

### Known trade-off (accepted)
The tool only answers for foods covered by a tag somewhere. It won't
generalize to novel dishes. Fine for a personal study tool; an
attribute-based inference engine stays in the v2 backlog.

---

## 2. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Data authoring | **YAML files** (one per wine) | Nested data, comments, git diffs, human-writable |
| Data validation | **Python + Pydantic** | Catch typos (`tanin: 4`) and unknown food tags at build time |
| Working database | **SQLite** (built, not hand-edited) | Free SQL queries + FTS5 full-text search during development |
| Pairing lookup | **Pure Python** | Tag resolution + ranking + similarity, ~150 lines |
| Testing | **pytest** | Wine Folly's own pairing charts = test cases |
| Site generation | **Jinja2 templates** | Page generation stays in Python |
| Client search | **MiniSearch or FlexSearch** (CDN) | Tiny JS full-text search over a pre-built index |
| Offline install | **PWA** (manifest + service worker) | Home-screen app, works in airplane mode |
| Hosting | **GitHub Pages** | Free, deploys on `git push` |
| Audit view | **Excel/CSV export** (generated) | Sortable grid to sanity-check attribute numbers |

> **Note on Excel:** spreadsheets are a *generated output* (for auditing),
> never the source of truth. YAML masters → build script → SQLite + JSON
> + XLSX.

---

## 3. Repository Layout

```
wine_folly/
├── data/
│   ├── wines/            # one YAML per wine style, e.g. nebbiolo.yaml
│   ├── food-tags.yaml    # small controlled vocabulary + aliases
│   └── regions.yaml      # region hierarchy
├── src/
│   ├── schema.py         # Pydantic models (validation)
│   ├── load.py           # YAML → SQLite (rejects unknown food tags)
│   ├── engine.py         # tag resolution, ranking, similarity
│   ├── query.py          # CLI: python -m src.query "truffle cheddar"
│   ├── export.py         # SQLite → JSON, search index, XLSX audit file
│   └── build_site.py     # Jinja2 → site/
├── templates/            # Jinja2 HTML templates
├── site/                 # generated output (deployed to GitHub Pages)
├── tests/
│   └── test_pairings.py  # Wine Folly chart pairings as assertions
├── build.py              # runs the whole pipeline end-to-end
└── README.md
```

---

## 4. Data Schema (v1)

All 1–5 scales mirror Wine Folly's own charts — note-taking while reading
becomes mechanical.

### Wine style — `data/wines/nebbiolo.yaml`
```yaml
id: nebbiolo
name: Nebbiolo
colour: red                     # red | white | rosé | sparkling | dessert
grapes: [nebbiolo]
# --- Wine Folly 1–5 scales ---
body: 4
sweetness: 1
tannin: 5
acidity: 4
alcohol: 4
flavours: [cherry, rose, tar, leather, anise]
regions: [piedmont]
countries: [italy]
price_band: "£££"              # £-££££
serving:
  temp_c: [16, 18]
  glass: burgundy              # burgundy | bordeaux | universal | flute | ...
  decant_minutes: 60
  cellar_years: [5, 25]
# --- pairings live ON the wine ---
pairings:
  - tags: [cheese-aged, truffle]
    strength: perfect          # perfect | great | good
    why: >
      Earthy tar-and-truffle aromas echo the cheese; massive tannin
      scrubs the fat, high acid refreshes against the salt. (WF p.142)
  - tags: [red-meat-braised]
    strength: perfect
    why: "Tannin + fat is the classic contrast; intensities match."
  - tags: [mushroom]
    strength: great
    why: "Congruent earthiness; umami softens the tannin."
avoid:
  - tags: [fish-delicate]
    why: "High tannin turns metallic against delicate fish."
notes: >
  Deceptively pale but massive tannin. Needs fat or age. (WF p.142)
also_try: [baga, brachetto]
```

### Food tag vocabulary — `data/food-tags.yaml`
One small file. Not a food database — just the search bridge between
free text and the tags wines use. Grows lazily (~20 entries to start).
```yaml
- id: cheese-aged
  label: Aged / hard cheese
  aliases: [cheddar, truffle cheddar, parmesan, gouda, comté, manchego]
- id: truffle
  label: Truffle
  aliases: [truffle, truffled, porcini]
- id: red-meat-braised
  label: Braised / roasted red meat
  aliases: [beef stew, short rib, brisket, lamb shank, osso buco]
- id: fish-delicate
  label: Delicate white fish
  aliases: [sole, cod, sea bass, plaice]
```

### Regions — `data/regions.yaml`
One file, a shallow hierarchy. Wines may reference a region **or** a
subregion id (`regions: [piedmont]` or `regions: [barolo]`).
```yaml
- id: piedmont
  name: Piedmont
  country: Italy
  climate: continental           # cool | continental | mediterranean | maritime
  known_for: [nebbiolo, barbera, moscato]
  notes: "Foggy foothills of the Alps; nebbia (fog) names the grape."
  subregions:
    - id: barolo
      name: Barolo
      known_for: [nebbiolo]
      notes: "Nebbiolo at full power; DOCG, long aging requirements."
    - id: barbaresco
      name: Barbaresco
      known_for: [nebbiolo]
      notes: "Nebbiolo's gentler, earlier-drinking side."

- id: burgundy
  name: Burgundy (Bourgogne)
  country: France
  climate: continental
  known_for: [pinot-noir, chardonnay]
  notes: "Terroir taken to its logical extreme; village > grape on labels."
  subregions:
    - id: chablis
      name: Chablis
      known_for: [chardonnay]
      notes: "Unoaked, steely Chardonnay from Kimmeridgian limestone."
```

### Engine logic
1. **Resolve:** query "truffle cheddar" → alias match → tags
   `[cheese-aged, truffle]`.
2. **Rank:** wines whose `pairings` cover those tags, scored by
   strength (perfect=3, great=2, good=1) + bonus for covering more of
   the query's tags; anything matching an `avoid` tag is excluded.
3. **Explain:** the matched pairing's hand-written `why` is shown as-is.
4. **Similar wines:** cosine distance on the
   `[body, sweetness, tannin, acidity, alcohol, intensity]` vector —
   independent of tags, works for every wine automatically.

Build-time validation: every food tag used in any wine must exist in
`food-tags.yaml`, and every region id must exist in `regions.yaml`
(region or subregion), or the build fails. Broken references are
impossible by construction.

---

## 5. Search — Detailed Specification

Search exists in two places with the same behavior: **SQLite FTS5** for
the CLI during development, and **MiniSearch** in the app. Both are fed
from the same YAML at build time.

### 5.1 What gets indexed
`export.py` flattens the dataset into search documents of three types:

| Doc type | Indexed fields (boost) | Selecting a result opens |
|---|---|---|
| `wine` | name (×3), grapes (×2), flavours (×1.5), region names | Wine detail page |
| `food` | tag label (×2), aliases (×3) — aliases joined into one string | Food-tag results page (ranked wines + whys) |
| `region` | name (×2), country, known_for grapes | Region page |

All text is normalized at index *and* query time: lowercased, accents
stripped (`comte` must find `comté`), punctuation removed. The docs are
written to `site/search-docs.json` (~100 entries — small enough that
MiniSearch builds its index in the browser on page load in milliseconds;
no pre-serialized index needed).

### 5.2 Query behavior in the app (`app.js`)
- **Search-as-you-type:** input debounced ~150 ms, then
  `miniSearch.search(q, {prefix: true, fuzzy: 0.2})` —
  `prefix` makes "neb" match *Nebbiolo*; `fuzzy` forgives typos like
  "nebiollo".
- **Grouped results:** food matches shown first (the primary use case),
  then wines, then regions — each row labeled with its type.
- **No matches:** show browse links (by region / by colour / by body)
  instead of a dead end.

### 5.3 Food query resolution (the pairing path)
Query: `"truffle cheddar"`
1. MiniSearch matches **two** food docs: `truffle` (alias *truffle*) and
   `cheese-aged` (alias *truffle cheddar*).
2. The app loads the pre-computed per-tag rankings
   (`site/pairings/<tag>.json`, generated by `export.py` from the
   engine's output).
3. Client-side merge (~30 lines of JS): sum each wine's strength scores
   across all matched tags (perfect=3, great=2, good=1), **drop** any
   wine whose `avoid` list hits a matched tag, re-rank, and show each
   wine's `why` from its highest-strength matching pairing.
4. Result: Nebbiolo top of the list, explained in my own words.

So the *only* runtime pairing logic is a score merge — everything else
was computed in Python at build time.

### 5.4 CLI equivalents (development, weeks 2–4)
```bash
python -m src.query "truffle cheddar"   # pairing lookup with whys
python -m src.query search "tar"        # FTS5 full-text: matches Nebbiolo's flavours
python -m src.query similar nebbiolo    # attribute-vector nearest neighbors
```
FTS5 setup: one virtual table `search_index(doc_type, ref_id, content)`
rebuilt on every `build.py` run. The CLI and the app should return the
same results for the same query — that's a test in itself.

---

## 6. Week-by-Week Plan (~5–8 hrs/week)

### Phase 1 — The Brain (Python only)

**Week 1 — Schema & pilot data**
- [x] Init git repo with the layout above
- [x] Write `schema.py` (Pydantic models: Wine, Pairing, FoodTag)
- [x] Hand-write 5 wines with pairings + a ~15-entry `food-tags.yaml`
- [x] `load.py`: validate YAML → SQLite; unknown tags = build error
- 🎯 *Milestone:* `python build.py` fails loudly on a typo'd tag,
  succeeds cleanly otherwise.

**Week 2 — Pairing lookup engine**
- [ ] `engine.py`: alias resolution → tag match → ranking → whys
- [ ] Similarity function on the attribute vector
- [ ] `query.py` CLI (`query "truffle cheddar"`, `similar nebbiolo`)
- 🎯 *Milestone:* `python -m src.query "truffle cheddar"` prints ranked
  wines **with my hand-written reasons**.

**Week 3 — Test against the book**
- [ ] Transcribe 15–20 pairings from Wine Folly's charts into
  `test_pairings.py` (e.g. *Cabernet ↔ ribeye must rank top-3*)
- [ ] Fill gaps the tests reveal (missing tags, missing pairings)
- 🎯 *Milestone:* green pytest run against the book's own pairings.

**Week 4 — Grow the dataset**
- [ ] Expand to ~20 wines while reading; vocabulary grows as needed
- [ ] `export.py`: JSON bundles + **XLSX audit export**; eyeball the grid
  for inconsistent attribute scores
- 🎯 *Milestone:* attribute numbers audited & consistent across dataset.

### Phase 2 — The App

**Week 5 — Static site generation**
- [ ] Jinja2 templates: home, wine detail, food-tag pages, region pages
- [ ] `build_site.py` renders everything into `site/`
- 🎯 *Milestone:* open `site/index.html` locally, click through to
  Nebbiolo's full detail page.

**Week 6 — Search & pairing UI (implements §5)**
- [ ] `export.py` emits `search-docs.json` + per-tag `pairings/<tag>.json`
- [ ] `app.js`: debounced search box, prefix + fuzzy matching, grouped
  results (foods → wines → regions)
- [ ] Multi-tag score merge + `avoid` filtering client-side (§5.3)
- [ ] Parity check: browser results match `python -m src.query` results
- 🎯 *Milestone:* type "truffle cheddar" in the browser → ranked wines
  with reasons. No server running.

**Week 7 — Deploy**
- [ ] GitHub Pages via Actions: push → build → deploy
- [ ] Polish mobile layout (this is primarily a phone tool)
- 🎯 *Milestone:* live URL, usable from phone browser.

**Week 8 — Make it installable & offline**
- [ ] Web manifest (name, icon, colours)
- [ ] Service worker caching the whole site
- 🎯 *Milestone:* installed on home screen, **works in airplane mode**.

### Phase 3 — Forever mode
- Each Wine Folly chapter → new wine YAML → `python build.py` → push.
- Backlog ideas: attribute-based pairing inference for untagged foods
  (v2 engine), personal tasting journal, "what's in my rack" inventory,
  label photo notes, quiz mode for studying.

---

## 7. Definition of Done (MVP)

- Works fully offline once installed
- Food query → ranked wines **with my hand-written explanations**,
  instantly
- Every wine has taste, serving temp, glassware, decanting, cellaring,
  regions, price band, sourcing notes
- Similar-wine suggestions on every wine page
- One command (`python build.py`) from notes → deployable site
- Pairing lookup passes the Wine Folly chart test suite
- No food tag exists in a wine file without being in `food-tags.yaml`

---

## 8. Getting Started

```bash
git init sommelier && cd sommelier
python -m venv .venv && source .venv/bin/activate
pip install pydantic pyyaml jinja2 pytest openpyxl
# Week 1 starts here: write schema.py, then your first nebbiolo.yaml
```
