/* WineFolly — search and pairing.  (templates/assets/app.js)
 *
 * This file is a CONTRACT, not the place search decisions get made. Field
 * weights and stopwords live in src/engine.py and are shipped here as plain
 * data (site/data/search-config.json) — see export_search_config() in
 * src/export.py. Descriptive words like "sweet"/"tannic"/"strong" need no
 * runtime logic at all: export.py's taste_tags() already turns a wine's own
 * scale values into literal searchable words at build time, so the browser
 * just searches them like any other field. Tuning search means editing
 * Python; this file just wires the config into MiniSearch (vendored in
 * assets/minisearch.js — MIT, no CDN, works in airplane mode like
 * everything else here) and renders DOM. buildIndex()/search() are the two
 * functions doing that wiring; tests can require() this file in Node to
 * exercise them the same way the browser does.
 *
 * The merge in mergePairings() is the one piece of real logic still native
 * to this file, because it combines several PRECOMPUTED per-tag JSON files
 * at read time (which tags to combine is only known once a query resolves
 * in the browser) rather than ranking free text — it MUST match
 * engine.pair_food():
 *   score  = sum of strength scores across matched tags
 *          + 0.5 per additional distinct tag covered
 *   drop   any wine listed in avoided_by for ANY matched tag
 *   sort   by score descending, then name
 */
(function (root) {
  "use strict";

  var MiniSearch = (typeof window !== "undefined" && window.MiniSearch) ||
    (typeof require !== "undefined" && require("./minisearch.js"));

  var STRENGTH_SCORE = { perfect: 3, great: 2, good: 1 };
  var KIND_ORDER = { food: 0, wine: 1, region: 2 };

  /* Strip accents so "comte" finds "comté" — the one piece of text
     processing still written here, because it has to run on whatever the
     user types, and MiniSearch's processTerm is the single place (index
     AND query time, automatically) it needs to exist. */
  function foldAccents(term) {
    return String(term).toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "");
  }

  /* Builds a MiniSearch index from the exported doc corpus, configured
     entirely from search-config.json (field weights + stopwords) —
     nothing here is a hardcoded number or word list. Descriptive words
     like "sweet"/"tannic"/"strong" need no special handling: export.py's
     taste_tags() already turned them into literal words in each wine's
     `taste_tags` field at build time, so they're just an ordinary field
     to search like any other. */
  function buildIndex(docs, config) {
    var weights = (config && config.weights) || {};
    var stopwords = {};
    ((config && config.stopwords) || []).forEach(function (w) { stopwords[w] = true; });

    function processTerm(term) {
      term = foldAccents(term).replace(/[^a-z0-9]/g, "");
      if (!term) return null;
      return stopwords[term] ? null : term;
    }

    var mini = new MiniSearch({
      fields: Object.keys(weights),
      storeFields: ["kind", "ref", "title"],
      idField: "id",
      processTerm: processTerm,
      searchOptions: { boost: weights, fuzzy: 0.2, prefix: true, combineWith: "OR" }
    });
    mini.addAll(docs);
    return mini;
  }

  /* Runs a query against an index built by buildIndex(), applying the one
     bit of tie-breaking MiniSearch doesn't know about: dishes read above
     wines above regions when scores land equal. */
  function search(index, query, limit) {
    var hits = index.search(query);
    hits.sort(function (a, b) {
      if (b.score !== a.score) return b.score - a.score;
      var ka = KIND_ORDER[a.kind], kb = KIND_ORDER[b.kind];
      if (ka !== kb) return ka - kb;
      return a.title < b.title ? -1 : a.title > b.title ? 1 : 0;
    });
    return hits.slice(0, limit || 10);
  }

  /* §5.3 — merge per-tag pairing files client-side. `payloads` is an
     array of loaded pairings/<tag>.json objects. */
  function mergePairings(payloads) {
    var avoided = {}, acc = {}, i, j, k;

    for (i = 0; i < payloads.length; i++) {
      var list = payloads[i].avoid || [];
      for (j = 0; j < list.length; j++) avoided[list[j]] = true;
    }

    for (i = 0; i < payloads.length; i++) {
      var payload = payloads[i];
      for (j = 0; j < payload.wines.length; j++) {
        var wine = payload.wines[j];
        if (avoided[wine.wine_id]) continue;
        var entry = acc[wine.wine_id];
        if (!entry) {
          entry = acc[wine.wine_id] = {
            wine_id: wine.wine_id, name: wine.name, score: 0,
            strength: wine.strength, why: [], matched_tags: [],
            regions: wine.regions || [], flavours: wine.flavours || []
          };
        }
        entry.score += STRENGTH_SCORE[wine.strength] || 0;
        entry.matched_tags.push(payload.tag);
        for (k = 0; k < wine.why.length; k++) entry.why.push(wine.why[k]);
        if ((STRENGTH_SCORE[wine.strength] || 0) > (STRENGTH_SCORE[entry.strength] || 0)) {
          entry.strength = wine.strength;
        }
      }
    }

    var out = [];
    for (var id in acc) {
      var e = acc[id], distinct = {};
      for (i = 0; i < e.matched_tags.length; i++) distinct[e.matched_tags[i]] = true;
      e.score += 0.5 * (Object.keys(distinct).length - 1);   // coverage bonus
      out.push(e);
    }
    out.sort(function (a, b) {
      if (b.score !== a.score) return b.score - a.score;
      return a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
    });
    return out;
  }

  root.WineFolly = {
    buildIndex: buildIndex, search: search,
    mergePairings: mergePairings, STRENGTH_SCORE: STRENGTH_SCORE
  };
  if (typeof module !== "undefined" && module.exports) module.exports = root.WineFolly;
})(typeof window !== "undefined" ? window : globalThis);


/* ------------------------------------------------------------------ DOM */

if (typeof document !== "undefined") (function () {
  "use strict";

  var S = window.WineFolly;
  var ROOT = window.SITE_ROOT || "";
  var input = document.getElementById("q");
  var output = document.getElementById("results");
  var panel = document.getElementById("pairings");
  if (!input || !output) return;

  var index = null, cache = {};

  function href(hit) {
    var folder = hit.kind === "food" ? "foods" : hit.kind === "wine" ? "wines" : "regions";
    return ROOT + folder + "/" + hit.ref + ".html";
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderResults(hits, query) {
    output.innerHTML = "";
    if (!query.trim()) return;
    if (!hits.length) {
      output.appendChild(el("li", "empty", "Nothing matches — try a grape, a region or a flavour."));
      return;
    }
    hits.forEach(function (hit) {
      var li = el("li"), a = document.createElement("a");
      a.href = href(hit);
      a.appendChild(el("span", null, hit.title));
      li.appendChild(a);
      output.appendChild(li);
    });
  }

  function loadJSON(path) {
    if (cache[path]) return Promise.resolve(cache[path]);
    return fetch(ROOT + path)
      .then(function (r) { return r.json(); })
      .then(function (payload) { cache[path] = payload; return payload; });
  }

  function loadTag(tagId) { return loadJSON("data/pairings/" + tagId + ".json"); }

  /* dish -> wines: merge every matched food tag's ranked wines (§5.3). */
  function renderFoodPairings(foodHits) {
    var tags = foodHits.slice(0, 3).map(function (h) { return h.ref; });
    Promise.all(tags.map(loadTag)).then(function (payloads) {
      var merged = S.mergePairings(payloads);
      panel.innerHTML = "";
      panel.appendChild(el("h3", null, "What to drink"));
      panel.appendChild(el("p", "subtitle", "Reading that as: " +
        payloads.map(function (p) { return p.label; }).join(" + ")));

      if (!merged.length) {
        panel.appendChild(el("p", "empty", "No wine in my notes pairs with this yet."));
        return;
      }
      var list = el("ul", "pairs");
      merged.slice(0, 8).forEach(function (wine) {
        var li = el("li"), head = el("div", "head");
        var a = document.createElement("a");
        a.href = ROOT + "wines/" + wine.wine_id + ".html";
        a.textContent = wine.name;
        head.appendChild(a);
        var badge = el("span", "strength", wine.strength);
        badge.setAttribute("data-s", wine.strength);
        head.appendChild(badge);
        li.appendChild(head);
        wine.why.forEach(function (why) { li.appendChild(el("p", "why", why)); });
        list.appendChild(li);
      });
      panel.appendChild(list);
    });
  }

  /* wine -> dishes: the reverse direction. A query like "chardonnay good
     match food" resolves to the wine first, so show what it already pairs
     with (data/wines/<id>.json, precomputed by engine.wine_detail()) rather
     than requiring the phrase to be parsed. */
  function renderWinePairings(wineHit) {
    loadJSON("data/wines/" + wineHit.ref + ".json").then(function (detail) {
      panel.innerHTML = "";
      panel.appendChild(el("h3", null, "Pairs well with"));
      panel.appendChild(el("p", "subtitle", detail.wine.name));

      if (!detail.pairs_with.length) {
        panel.appendChild(el("p", "empty", "No food pairing written down for this one yet."));
        return;
      }
      var list = el("ul", "pairs");
      detail.pairs_with.slice(0, 8).forEach(function (pair) {
        var li = el("li"), head = el("div", "head");
        var a = document.createElement("a");
        a.href = ROOT + "foods/" + pair.tag_id + ".html";
        a.textContent = pair.label;
        head.appendChild(a);
        var badge = el("span", "strength", pair.strength);
        badge.setAttribute("data-s", pair.strength);
        head.appendChild(badge);
        li.appendChild(head);
        li.appendChild(el("p", "why", pair.why));
        list.appendChild(li);
      });
      panel.appendChild(list);
    });
  }

  /* region -> wines: "good wines in Burgundy" resolves to the region
     first, same reasoning as the wine -> food direction above - show what
     the region already has (data/regions/<id>.json, from
     engine.region_detail()) instead of making the user click through. */
  function renderRegionWines(regionHit) {
    loadJSON("data/regions/" + regionHit.ref + ".json").then(function (detail) {
      panel.innerHTML = "";
      panel.appendChild(el("h3", null, "Wines from here"));
      panel.appendChild(el("p", "subtitle", detail.region.name));

      if (!detail.wines.length) {
        panel.appendChild(el("p", "empty", "No wine in my notes from here yet."));
        return;
      }
      var list = el("ul", "wines");
      detail.wines.slice(0, 8).forEach(function (wine) {
        var li = el("li");
        li.setAttribute("data-type", (wine.wine_type || []).join(" "));
        var a = document.createElement("a");
        a.href = ROOT + "wines/" + wine.id + ".html";
        a.appendChild(el("span", "wine-name", wine.name));
        if (wine.flavours && wine.flavours.length) {
          a.appendChild(el("div", "wine-meta", wine.flavours.slice(0, 4).join(", ")));
        }
        li.appendChild(a);
        list.appendChild(li);
      });
      panel.appendChild(list);
    });
  }

  function renderPairings(hits) {
    if (!panel) return;
    if (!hits.length) { panel.innerHTML = ""; return; }

    var top = hits[0];
    if (top.kind === "wine") {
      renderWinePairings(top);
    } else if (top.kind === "region") {
      renderRegionWines(top);
    } else {
      var foodHits = hits.filter(function (h) { return h.kind === "food"; });
      if (foodHits.length) renderFoodPairings(foodHits);
      else panel.innerHTML = "";
    }
  }

  function run() {
    var query = input.value;
    var hits = index ? S.search(index, query, 10) : [];
    renderResults(hits, query);
    renderPairings(hits);
  }

  var timer = null;
  input.addEventListener("input", function () {
    clearTimeout(timer);
    timer = setTimeout(run, 120);
  });

  input.addEventListener("keydown", function (e) {
    var links = output.querySelectorAll("a");
    if (e.key === "Escape") { input.value = ""; run(); }
    else if (e.key === "ArrowDown" && links.length) { e.preventDefault(); links[0].focus(); }
    else if (e.key === "Enter" && links.length) { e.preventDefault(); window.location.href = links[0].href; }
  });

  /* The suggestions list floats over the page (so it doesn't shove
     content down while typing), which means it must also know how to get
     out of the way: close it once focus leaves the search box entirely,
     not just on blur (blur alone would fire — and wrongly close it — the
     moment ArrowDown moves focus from the input into the list itself). */
  var searchBox = input.closest(".search") || input.parentElement;
  searchBox.addEventListener("focusout", function (e) {
    if (!searchBox.contains(e.relatedTarget)) output.innerHTML = "";
  });
  input.addEventListener("focus", function () {
    if (input.value.trim()) run();
  });

  input.disabled = true;
  Promise.all([
    fetch(ROOT + "data/search-docs.json").then(function (r) { return r.json(); }),
    fetch(ROOT + "data/search-config.json").then(function (r) { return r.json(); })
  ]).then(function (results) {
    var docs = results[0], config = results[1];
    index = S.buildIndex(docs, config);
    input.disabled = false;
    input.placeholder = "Search " + docs.length + " wines, dishes and regions";
    if (input.value) run();
  }).catch(function () {
    input.placeholder = "Search needs http:// — run: cd site && python3 -m http.server";
  });
})();
