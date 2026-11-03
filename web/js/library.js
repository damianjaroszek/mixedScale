// Library table: filtering, sorting, selection, drag & drop, ratings, context menu.
import { api } from "./api.js";
import { h, $, popover, closePopover, toast, keyBadge, keyColor, fmtTime, confirmBox } from "./ui.js";

export class Library {
  constructor(app) {
    this.app = app;
    this.tbody = $("#libTable tbody");
    this.filters = { search: "", keys: new Set(), tempoMin: null, tempoMax: null, energy: new Set(), genres: new Set(), matchA: false };
    this.sort = { col: null, dir: 1 };
    this.selected = new Set();
    this.lastClicked = null;
    this.rows = [];
    this.bind();
  }

  bind() {
    $("#search").addEventListener("input", (e) => { this.filters.search = e.target.value.toLowerCase(); this.render(); });
    $("#fKey").addEventListener("click", (e) => this.keyFilter(e.currentTarget));
    $("#fTempo").addEventListener("click", (e) => this.tempoFilter(e.currentTarget));
    $("#fEnergy").addEventListener("click", (e) => this.checkFilter(e.currentTarget, "energy", Array.from({ length: 10 }, (_, i) => i + 1), (v) => `Energia ${v}`));
    $("#fGenre").addEventListener("click", (e) => this.checkFilter(e.currentTarget, "genres", this.app.genres(), (v) => v));
    $("#fMatch").addEventListener("click", () => { this.filters.matchA = !this.filters.matchA; this.render(); });
    $("#btnReset").addEventListener("click", () => this.reset());
    document.querySelectorAll("#libTable th[data-sort]").forEach((th) => th.addEventListener("click", () => {
      const col = th.dataset.sort;
      if (this.sort.col === col) this.sort.dir = -this.sort.dir; else { this.sort.col = col; this.sort.dir = 1; }
      this.render();
    }));
    this.tbody.addEventListener("click", (e) => this.onClick(e));
    this.tbody.addEventListener("dblclick", (e) => {
      const tr = e.target.closest("tr");
      if (tr && !e.target.closest(".stars,.act")) this.app.loadDeck(e.shiftKey || e.altKey ? "B" : "A", Number(tr.dataset.id));
    });
    this.tbody.addEventListener("dragstart", (e) => {
      const tr = e.target.closest("tr");
      if (!tr) return;
      const id = Number(tr.dataset.id);
      if (!this.selected.has(id)) { this.selected = new Set([id]); this.paintSelection(); }
      e.dataTransfer.setData("text/x-track", [...this.selected].join(","));
      e.dataTransfer.effectAllowed = "copy";
    });
  }

  reset() {
    this.filters = { search: "", keys: new Set(), tempoMin: null, tempoMax: null, energy: new Set(), genres: new Set(), matchA: false };
    $("#search").value = "";
    this.sort = { col: null, dir: 1 };
    this.render();
  }

  keyFilter(anchor) {
    const grid = h("div", { class: "key-grid" });
    for (const l of ["A", "B"]) for (let n = 1; n <= 12; n++) {
      const k = `${n}${l}`;
      const b = keyBadge(k, this.filters.keys.has(k) ? "sel" : "");
      b.title = this.app.keyInfo(k).name;
      b.addEventListener("click", () => { this.filters.keys.has(k) ? this.filters.keys.delete(k) : this.filters.keys.add(k); b.classList.toggle("sel"); this.render(); });
      grid.append(b);
    }
    popover(anchor, [h("h4", {}, "Tonacje (Camelot)"), grid,
      h("div", { class: "pop-row" }, h("button", { class: "btn link", onclick: () => { this.filters.keys.clear(); grid.querySelectorAll(".sel").forEach((x) => x.classList.remove("sel")); this.render(); } }, "Wszystkie"))]);
  }

  setKeyFilter(keys) { this.filters.keys = new Set(keys); this.render(); }

  tempoFilter(anchor) {
    const min = h("input", { class: "field", type: "number", placeholder: "od", value: this.filters.tempoMin ?? "", style: { width: "80px" } });
    const max = h("input", { class: "field", type: "number", placeholder: "do", value: this.filters.tempoMax ?? "", style: { width: "80px" } });
    const apply = () => { this.filters.tempoMin = min.value ? +min.value : null; this.filters.tempoMax = max.value ? +max.value : null; this.render(); };
    min.addEventListener("input", apply); max.addEventListener("input", apply);
    const a = this.app.decks.A;
    const preset = (pct) => { if (!a.track) return; min.value = Math.floor(a.bpm * (1 - pct / 100)); max.value = Math.ceil(a.bpm * (1 + pct / 100)); apply(); };
    popover(anchor, [h("h4", {}, "Tempo (BPM)"), h("div", { class: "pop-row" }, min, "–", max),
      h("div", { class: "pop-row" }, h("span", { class: "muted" }, "Wokół decku A:"),
        [3, 6, 10].map((p) => h("button", { class: "btn chip", onclick: () => preset(p) }, `±${p}%`)),
        h("button", { class: "btn link", onclick: () => { min.value = max.value = ""; apply(); } }, "Wszystkie"))]);
  }

  checkFilter(anchor, field, values, label) {
    const list = h("div", { class: "check-list" }, values.length ? values.map((v) => {
      const cb = h("input", { type: "checkbox", checked: this.filters[field].has(v) });
      cb.addEventListener("change", () => { cb.checked ? this.filters[field].add(v) : this.filters[field].delete(v); this.render(); });
      return h("label", {}, cb, label(v));
    }) : h("span", { class: "muted" }, "Brak danych"));
    popover(anchor, [list]);
  }

  // ------------------------------------------------------------------ data
  source() {
    const app = this.app;
    const pl = app.currentPlaylist;
    if (pl === "queue") return app.tracks.filter((t) => t.status !== "done");
    if (typeof pl === "number") return app.plTrackIds.map((id) => app.byId.get(id)).filter(Boolean);
    return app.tracks;
  }

  filtered() {
    const f = this.filters;
    const aKey = this.app.decks.A.key;
    const compat = f.matchA && aKey ? new Set(this.app.keyInfo(aKey).compatible) : null;
    let rows = this.source().filter((t) => {
      if (f.search) {
        const hay = `${t.artist} ${t.title} ${t.genre} ${t.comment} ${t.album}`.toLowerCase();
        if (!f.search.split(/\s+/).every((w) => hay.includes(w))) return false;
      }
      if (f.keys.size && !f.keys.has(t.key)) return false;
      if (f.tempoMin != null && !(t.bpm >= f.tempoMin)) return false;
      if (f.tempoMax != null && !(t.bpm <= f.tempoMax)) return false;
      if (f.energy.size && !f.energy.has(t.energy)) return false;
      if (f.genres.size && !f.genres.has(t.genre)) return false;
      if (compat && !compat.has(t.key)) return false;
      return true;
    });
    if (this.sort.col) {
      const c = this.sort.col, d = this.sort.dir;
      const keyNum = (k) => (k ? parseInt(k, 10) * 2 + (k.endsWith("B") ? 1 : 0) : 99);
      rows = [...rows].sort((a, b) => {
        let x = a[c], y = b[c];
        if (c === "key") { x = keyNum(x); y = keyNum(y); }
        if (typeof x === "string" || typeof y === "string") return String(x || "").localeCompare(String(y || ""), "pl", { sensitivity: "base" }) * d;
        return ((x ?? -1) - (y ?? -1)) * d;
      });
    }
    return rows;
  }

  render() {
    const f = this.filters;
    $("#fKey").classList.toggle("on", f.keys.size > 0);
    $("#fTempo").classList.toggle("on", f.tempoMin != null || f.tempoMax != null);
    $("#fEnergy").classList.toggle("on", f.energy.size > 0);
    $("#fGenre").classList.toggle("on", f.genres.size > 0);
    $("#fMatch").classList.toggle("on", f.matchA);
    document.querySelectorAll("#libTable th[data-sort]").forEach((th) => {
      th.classList.toggle("sorted", th.dataset.sort === this.sort.col);
      th.dataset.arrow = th.dataset.sort === this.sort.col ? (this.sort.dir > 0 ? "▲" : "▼") : "";
    });
    const rows = this.filtered();
    this.rows = rows;
    const frag = document.createDocumentFragment();
    const a = this.app.decks.A.track?.id, b = this.app.decks.B.track?.id;
    for (const t of rows) frag.append(this.row(t, a, b));
    this.tbody.replaceChildren(frag);
    $("#libEmpty").classList.toggle("hidden", this.app.tracks.length > 0);
    const name = this.app.playlistName();
    $("#libTitle").textContent = `${name} (${rows.length})`;
  }

  row(t, a, b) {
    const cover = t.has_cover
      ? h("img", { class: "cover", loading: "lazy", src: `/api/tracks/${t.id}/cover`, alt: "" })
      : h("div", { class: "cover ph", style: { background: `linear-gradient(135deg, ${keyColor(t.key)}, #20263a)` } }, t.key || "");
    let keyCell;
    if (t.status === "done") {
      const low = t.key_confidence != null && t.key_confidence < 0.35 && !t.key_manual;
      const differs = t.tag_key && t.tag_key !== t.key;
      keyCell = h("span", { class: "key-cell", "data-key": "1", title: `Pewność ${Math.round((t.key_confidence || 0) * 100)}%${t.key2 ? `, alternatywa ${t.key2}` : ""}${t.tag_key ? `, w tagach ${t.tag_key}` : ""}. Kliknij, aby zmienić.` },
        keyBadge(t.key, low ? "unsure" : ""), low ? h("span", { class: "q" }, "?") : null, differs ? h("span", { class: "tagdiff" }, "≠") : null);
    }
    else if (t.status === "error") keyCell = h("span", { class: "status-chip err", title: t.error || "" }, "błąd");
    else keyCell = h("span", { class: "status-chip" }, t.status === "analyzing" ? "analiza…" : "w kolejce");
    const stars = h("span", { class: "stars" }, [1, 2, 3, 4, 5].map((n) => h("i", { class: n <= (t.rating || 0) ? "on" : "", "data-r": n })));
    const tr = h("tr", { "data-id": t.id, draggable: "true", class: [this.selected.has(t.id) ? "sel" : "", t.id === a ? "on-a" : "", t.id === b ? "on-b" : "", t.status !== "done" ? "pending" : ""].join(" ") },
      h("td", {}, cover),
      h("td", { title: t.artist }, t.artist),
      h("td", { title: t.title }, t.title, t.stems && t.stems !== "none" && t.stems !== "running" ? h("span", { class: "muted", title: "Ma stemy" }, " ≋") : ""),
      h("td", {}, keyCell),
      h("td", {}, t.bpm ? (+t.bpm.toFixed(1)).toString() : ""),
      h("td", {}, t.key ? this.app.keyInfo(t.key).short : ""),
      h("td", {}, t.energy ?? ""),
      h("td", { title: t.genre }, t.genre),
      h("td", { title: t.comment }, t.comment),
      h("td", {}, stars),
      h("td", {}, h("button", { class: "btn icon act", title: "Więcej" }, "⋯")));
    return tr;
  }

  paintSelection() {
    this.tbody.querySelectorAll("tr").forEach((tr) => tr.classList.toggle("sel", this.selected.has(Number(tr.dataset.id))));
  }

  async onClick(e) {
    const tr = e.target.closest("tr");
    if (!tr) return;
    const id = Number(tr.dataset.id);
    const star = e.target.closest(".stars i");
    if (star) {
      const t = this.app.byId.get(id);
      const r = Number(star.dataset.r) === t.rating ? 0 : Number(star.dataset.r);
      const fresh = await api.patch(`/api/tracks/${id}`, { rating: r });
      this.app.updateTrack(fresh);
      return;
    }
    if (e.target.closest(".act")) { this.menu(e.target.closest(".act"), id); return; }
    if (e.target.closest("[data-key]")) { this.app.keyPopover(e.target.closest("[data-key]"), id); return; }
    if (e.shiftKey && this.lastClicked != null) {
      const ids = this.rows.map((r) => r.id);
      const [i, j] = [ids.indexOf(this.lastClicked), ids.indexOf(id)].sort((x, y) => x - y);
      ids.slice(i, j + 1).forEach((x) => this.selected.add(x));
    } else if (e.ctrlKey || e.metaKey) {
      this.selected.has(id) ? this.selected.delete(id) : this.selected.add(id);
      this.lastClicked = id;
    } else {
      this.selected = new Set([id]);
      this.lastClicked = id;
    }
    this.paintSelection();
  }

  menu(anchor, id) {
    if (!this.selected.has(id)) { this.selected = new Set([id]); this.paintSelection(); }
    const ids = [...this.selected];
    const app = this.app;
    const item = (label, fn) => h("button", { onclick: () => { closePopover(); fn(); } }, label);
    const pls = app.playlists.map((p) => item(`➕ Do playlisty: ${p.name}`, () => app.addToPlaylist(p.id, ids)));
    popover(anchor, h("div", { class: "menu" },
      item("Wczytaj do decku A", () => app.loadDeck("A", id)),
      item("Wczytaj do decku B", () => app.loadDeck("B", id)),
      h("hr"),
      ...pls,
      item("➕ Nowa playlista z zaznaczonych…", () => app.newPlaylist(ids)),
      typeof app.currentPlaylist === "number" ? item("➖ Usuń z tej playlisty", () => app.removeFromPlaylist(app.currentPlaylist, ids)) : null,
      h("hr"),
      item("Rozdziel na stemy", () => app.stemsFor(ids)),
      item("Wpisz tonację i energię do komentarza", () => app.bulk(ids, "comment_key")),
      item("Analizuj ponownie", () => app.bulk(ids, "reanalyze")),
      item("Usuń z biblioteki (plik zostaje na dysku)", async () => { if (await confirmBox("Usunąć z biblioteki?", `${ids.length} utw. zniknie z biblioteki. Pliki na dysku pozostaną.`)) app.bulk(ids, "remove"); }),
    ), { align: "right" });
  }
}
