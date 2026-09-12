/* WineFolly — personal tasting notes.  (templates/assets/notes.js)
 *
 * Fully private, fully client-side: notes (and their photos) live in this
 * browser's IndexedDB, never sent anywhere, never part of the build. That
 * is a *different* mechanism from the service worker cache that makes the
 * rest of the site work offline — the service worker only ever stores
 * static files (HTML/CSS/JS), never your notes. IndexedDB is what makes
 * "add a note today, still see it next week" work: it survives a reload
 * and closing/reopening the site, and only disappears if this browser's
 * site data for Wine Folly is explicitly cleared — which is exactly why
 * the Journal page's Export button exists, as a backup you keep yourself.
 *
 * One object store, "notes", one record per note:
 *   { id (auto), wine_id, wine_name, date, mood, text, photo (Blob|null),
 *     created_at }
 */
(function () {
  "use strict";

  if (typeof document === "undefined") return;

  var DB_NAME = "winefolly-notes";
  var DB_VERSION = 1;
  var STORE = "notes";
  var MOOD_EMOJI = { loved: "😍", good: "🙂", meh: "😐", skip: "👎" };
  var PHOTO_MAX_SIZE = 1200;
  var PHOTO_QUALITY = 0.8;

  /* ------------------------------------------------------------ storage */

  function openDB() {
    return new Promise(function (resolve, reject) {
      if (!window.indexedDB) { reject(new Error("indexedDB unavailable")); return; }
      var req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = function () {
        var db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          var store = db.createObjectStore(STORE, { keyPath: "id", autoIncrement: true });
          store.createIndex("by_wine", "wine_id", { unique: false });
        }
      };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
  }

  function reqToPromise(req) {
    return new Promise(function (resolve, reject) {
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
  }

  function addNote(record) {
    return openDB().then(function (db) {
      return reqToPromise(db.transaction(STORE, "readwrite").objectStore(STORE).add(record));
    });
  }

  function deleteNote(id) {
    return openDB().then(function (db) {
      return reqToPromise(db.transaction(STORE, "readwrite").objectStore(STORE).delete(id));
    });
  }

  function sortNewestFirst(list) {
    return list.slice().sort(function (a, b) {
      if (a.date !== b.date) return a.date < b.date ? 1 : -1;
      return (b.created_at || "").localeCompare(a.created_at || "");
    });
  }

  function notesForWine(wineId) {
    return openDB().then(function (db) {
      var idx = db.transaction(STORE, "readonly").objectStore(STORE).index("by_wine");
      return reqToPromise(idx.getAll(wineId));
    }).then(sortNewestFirst);
  }

  function allNotes() {
    return openDB().then(function (db) {
      return reqToPromise(db.transaction(STORE, "readonly").objectStore(STORE).getAll());
    }).then(sortNewestFirst);
  }

  /* -------------------------------------------------------------- photo */

  /* Shrinks a camera photo before it ever reaches IndexedDB — a 12MP shot
     would otherwise eat the origin's whole storage quota in a few notes. */
  function downscaleImage(file, maxSize, quality) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        var scale = Math.min(1, maxSize / Math.max(img.width, img.height));
        var canvas = document.createElement("canvas");
        canvas.width = Math.round(img.width * scale);
        canvas.height = Math.round(img.height * scale);
        canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(function (blob) {
          URL.revokeObjectURL(url);
          resolve(blob);
        }, "image/jpeg", quality);
      };
      img.onerror = function (e) { URL.revokeObjectURL(url); reject(e); };
      img.src = url;
    });
  }

  function blobToDataURL(blob) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(reader.result); };
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
  }

  function dataURLToBlob(dataURL) {
    return fetch(dataURL).then(function (r) { return r.blob(); });
  }

  /* --------------------------------------------------- export / import */

  function exportAll() {
    return allNotes().then(function (notes) {
      return Promise.all(notes.map(function (n) {
        if (!n.photo) return Object.assign({}, n, { photo: null });
        return blobToDataURL(n.photo).then(function (dataURL) {
          return Object.assign({}, n, { photo: dataURL });
        });
      }));
    });
  }

  /* Fresh id per record, so importing the same backup twice just leaves
     two copies rather than corrupting anything. */
  function importAll(records) {
    return Promise.all(records.map(function (n) {
      var rec = Object.assign({}, n);
      delete rec.id;
      if (rec.photo && typeof rec.photo === "string") {
        return dataURLToBlob(rec.photo).then(function (blob) {
          rec.photo = blob;
          return addNote(rec);
        });
      }
      rec.photo = null;
      return addNote(rec);
    }));
  }

  /* ------------------------------------------------------------ shared UI */

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderNoteCard(note, onChange) {
    var card = el("div", "note-card");
    var head = el("div", "note-head");
    head.appendChild(el("span", "note-mood", MOOD_EMOJI[note.mood] || ""));
    head.appendChild(el("span", "note-date", note.date));
    var del = el("button", "note-delete", "Delete");
    del.type = "button";
    del.addEventListener("click", function () { deleteNote(note.id).then(onChange); });
    head.appendChild(del);
    card.appendChild(head);
    if (note.text) card.appendChild(el("p", "note-text", note.text));
    if (note.photo) {
      var img = document.createElement("img");
      img.className = "note-photo";
      img.alt = "";
      img.src = URL.createObjectURL(note.photo);
      card.appendChild(img);
    }
    return card;
  }

  /* ---------------------------------------------------- wine.html section */

  function initWineNotes() {
    var section = document.getElementById("notes");
    if (!section || !window.indexedDB) return;

    var wineId = section.dataset.wineId;
    var wineName = section.dataset.wineName;
    var list = section.querySelector(".notes-list");
    var addBtn = section.querySelector(".add-note-btn");
    var form = section.querySelector(".note-form");
    var cancelBtn = section.querySelector(".cancel-note");

    function render() {
      notesForWine(wineId).then(function (notes) {
        list.innerHTML = "";
        if (!notes.length) { list.appendChild(el("p", "empty", "No notes yet for this wine.")); return; }
        notes.forEach(function (note) { list.appendChild(renderNoteCard(note, render)); });
      });
    }

    addBtn.addEventListener("click", function () {
      form.hidden = !form.hidden;
      if (!form.hidden) form.querySelector('input[name="date"]').valueAsDate = new Date();
    });
    cancelBtn.addEventListener("click", function () { form.hidden = true; form.reset(); });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var fd = new FormData(form);
      var file = fd.get("photo");
      var record = {
        wine_id: wineId,
        wine_name: wineName,
        date: fd.get("date") || new Date().toISOString().slice(0, 10),
        mood: fd.get("mood") || "good",
        text: fd.get("text") || "",
        photo: null,
        created_at: new Date().toISOString()
      };
      var ready = (file && file.size) ? downscaleImage(file, PHOTO_MAX_SIZE, PHOTO_QUALITY) : Promise.resolve(null);
      ready.then(function (blob) {
        record.photo = blob;
        return addNote(record);
      }).then(function () {
        form.reset();
        form.hidden = true;
        render();
      });
    });

    section.hidden = false;
    render();
  }

  /* ------------------------------------------------------- journal page */

  function initJournal() {
    var container = document.getElementById("journal-list");
    if (!container) return;

    var emptyMsg = document.getElementById("journal-empty");
    var exportBtn = document.getElementById("export-notes");
    var importInput = document.getElementById("import-notes");
    var root = window.SITE_ROOT || "";

    if (!window.indexedDB) {
      container.appendChild(el("p", "empty", "This browser doesn't support saving notes."));
      return;
    }

    function render() {
      allNotes().then(function (notes) {
        container.innerHTML = "";
        if (emptyMsg) emptyMsg.hidden = notes.length > 0;
        notes.forEach(function (note) {
          var card = renderNoteCard(note, render);
          var wineLink = document.createElement("a");
          wineLink.className = "note-wine-link";
          wineLink.href = root + "wines/" + note.wine_id + ".html";
          wineLink.textContent = note.wine_name || note.wine_id;
          card.insertBefore(wineLink, card.firstChild);
          container.appendChild(card);
        });
      });
    }

    if (exportBtn) {
      exportBtn.addEventListener("click", function () {
        exportAll().then(function (data) {
          var blob = new Blob([JSON.stringify(data)], { type: "application/json" });
          var url = URL.createObjectURL(blob);
          var a = document.createElement("a");
          a.href = url;
          a.download = "wine-folly-notes-" + new Date().toISOString().slice(0, 10) + ".json";
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          URL.revokeObjectURL(url);
        });
      });
    }

    if (importInput) {
      importInput.addEventListener("change", function () {
        var file = importInput.files[0];
        if (!file) return;
        file.text().then(function (text) { return importAll(JSON.parse(text)); })
          .then(function () { importInput.value = ""; render(); });
      });
    }

    render();
  }

  initWineNotes();
  initJournal();
})();
