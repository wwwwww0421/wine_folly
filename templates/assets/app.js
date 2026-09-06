/* Sommelier — search and pairing.  (templates/assets/app.js)
 *
 * No library, no CDN: a fetched script fails in airplane mode, and
 * offline is the point. Pure logic lives in Sommelier.* so tests can
 * require() this file in Node and check parity against the Python engine
 * (tests/test_search_parity.py). DOM wiring is at the bottom, guarded.
 *
 * The merge in mergePairings() MUST match engine.pair_food():
 *   score  = sum of strength scores across matched tags
 *          + 0.5 per additional distinct tag covered
 *   drop   any wine listed in avoided_by for ANY matched tag
 *   sort   by score descending, then name
 */
(function (root) {
  "use strict";

  var STRENGTH_SCORE = { perfect: 3, great: 2, good: 1 };
  var WEIGHTS = {
    aliases: 6, label: 4,                        // food tags
    name: 5, grapes: 3, flavours: 2, regions: 1,  // wines
    country: 2                                   // regions
  };
  var KIND_ORDER = { food: 0, wine: 1, region: 2 };

  /* Mirror of engine.normalise(): lowercase, strip accents, drop punctuation. */
  function normalise(text) {
    return String(text)
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-z0-9 ]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  /* Bounded edit distance — bails as soon as it exceeds the budget, so
     this stays cheap across a few hundred docs. */
  function editDistance(a, b, max) {
    if (Math.abs(a.length - b.length) > max) return max + 1;
    var prev = [], cur = [], i, j;
    for (j = 0; j <= b.length; j++) prev[j] = j;
    for (i = 1; i <= a.length; i++) {
      cur[0] = i;
      var best = cur[0];
      for (j = 1; j <= b.length; j++) {
        cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1,
                          prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
        if (cur[j] < best) best = cur[j];
      }
      if (best > max) return max + 1;
      prev = cur.slice();
    }
    return prev[b.length];
  }

  function fuzzyBudget(term) {
    if (term.length >= 7) return 2;
    if (term.length >= 4) return 1;
    return 0;                                    // short words: exact only
  }

  /* Terms score independently and a doc needs only ONE to land: a query
     like "oysters with salt cod" must surface BOTH dishes (and the filler
     word must not veto everything). More terms matched = higher total. */
  function scoreDoc(doc, terms, allowFuzzy) {
    var total = 0;
    for (var i = 0; i < terms.length; i++) {
      var term = terms[i], best = 0;
      for (var field in WEIGHTS) {
        var value = doc[field];
        if (!value) continue;
        var weight = WEIGHTS[field], words = value.split(" ");
        for (var w = 0; w < words.length; w++) {
          var word = words[w];
          if (word === term) best = Math.max(best, weight * 3);
          else if (word.indexOf(term) === 0) best = Math.max(best, weight * 2);
          else if (allowFuzzy) {
            var budget = fuzzyBudget(term);
            if (budget && editDistance(word, term, budget) <= budget) {
              best = Math.max(best, weight);     // typo hits score lowest
            }
          }
        }
        if (!best && value.indexOf(term) !== -1) best = Math.max(best, weight);
      }
      total += best;                             // 0 is fine: other terms may land
    }
    return total;
  }

  function collect(docs, terms, allowFuzzy) {
    var hits = [];
    for (var i = 0; i < docs.length; i++) {
      var score = scoreDoc(docs[i], terms, allowFuzzy);
      if (score > 0) hits.push({ doc: docs[i], score: score });
    }
    return hits;
  }

  /* Two passes: exact/prefix first, fuzzy only if that found nothing, so
     a typo can never outrank a real match. */
  function search(docs, query, limit) {
    var terms = normalise(query).split(" ").filter(Boolean);
    if (!terms.length) return [];
    var hits = collect(docs, terms, false);
    var fuzzy = false;
    if (!hits.length) { hits = collect(docs, terms, true); fuzzy = true; }
    hits.sort(function (a, b) {
      if (b.score !== a.score) return b.score - a.score;
      var ka = KIND_ORDER[a.doc.kind], kb = KIND_ORDER[b.doc.kind];
      if (ka !== kb) return ka - kb;             // dishes first
      // return a.doc.title.localeCompare(b.doc.title);
      return a.doc.title < b.doc.title ? -1 : a.doc.title > b.doc.title ? 1 : 0;
    });
    hits = hits.slice(0, limit || 10);
    hits.fuzzy = fuzzy;
    return hits;
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
      // return a.name.localeCompare(b.name);
      return a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
    });
    return out;
  }

  root.Sommelier = {
    normalise: normalise, editDistance: editDistance,
    search: search, mergePairings: mergePairings, STRENGTH_SCORE: STRENGTH_SCORE
  };
  if (typeof module !== "undefined" && module.exports) module.exports = root.Sommelier;
})(typeof window !== "undefined" ? window : globalThis);


/* ------------------------------------------------------------------ DOM */

if (typeof document !== "undefined") (function () {
  "use strict";

  var S = window.Sommelier;
  var ROOT = window.SITE_ROOT || "";
  var input = document.getElementById("q");
  var output = document.getElementById("results");
  var panel = document.getElementById("pairings");
  if (!input || !output) return;

  var docs = [], cache = {};
  var KIND_LABEL = { food: "dish", wine: "wine", region: "region" };

  function href(doc) {
    var folder = doc.kind === "food" ? "foods" : doc.kind === "wine" ? "wines" : "regions";
    return ROOT + folder + "/" + doc.ref + ".html";
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
    if (hits.fuzzy) output.appendChild(el("li", "empty", "No exact match. Closest I know:"));
    hits.forEach(function (hit) {
      var li = el("li"), a = document.createElement("a");
      a.href = href(hit.doc);
      a.appendChild(el("span", null, hit.doc.title));
      a.appendChild(el("span", "kind", KIND_LABEL[hit.doc.kind]));
      li.appendChild(a);
      output.appendChild(li);
    });
  }

  function loadTag(tagId) {
    if (cache[tagId]) return Promise.resolve(cache[tagId]);
    return fetch(ROOT + "data/pairings/" + tagId + ".json")
      .then(function (r) { return r.json(); })
      .then(function (payload) { cache[tagId] = payload; return payload; });
  }

  function renderPairings(foodHits) {
    if (!panel) return;
    if (!foodHits.length) { panel.innerHTML = ""; return; }

    var tags = foodHits.slice(0, 3).map(function (h) { return h.doc.ref; });
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

  function run() {
    var query = input.value;
    var hits = S.search(docs, query, 10);
    renderResults(hits, query);
    renderPairings(hits.filter(function (h) { return h.doc.kind === "food"; }));
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

  input.disabled = true;
  fetch(ROOT + "data/search-docs.json")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      docs = data;
      input.disabled = false;
      input.placeholder = "Search " + docs.length + " wines, dishes and regions";
      if (input.value) run();
    })
    .catch(function () {
      input.placeholder = "Search needs http:// — run: cd site && python3 -m http.server";
    });
})();