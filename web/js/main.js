// App wiring: decks, mashup ideas, crossfader, Camelot wheel, playlists, views, polling.
import { api } from "./api.js";
import { Deck, ctx } from "./deck.js";
import { Library } from "./library.js";
import { h, $, $$, toast, prompt, confirmBox, keyColor, closePopover, modal, closeModal } from "./ui.js";
import { renderMashups, renderTags, renderSettings, importModal, uploadFiles, ideaFilterModal, helpModal } from "./views.js";
import { renderKeys, keyPopover } from "./keys.js";

class App {
  constructor() {
    this.tracks = [];
    this.byId = new Map();
    this.playlists = [];
    this.currentPlaylist = "all";
    this.plTrackIds = [];
    this.mode = "mashup";
    this.ideas = [];
    this.ideaIdx = -1;
    this.ideaFilter = this.defaultIdeaFilter();
    this.mashupCount = 0;
    this.keys = {};
    this.settings = {};
    this.status = null;
    this.view = "library";
  }

  defaultIdeaFilter() { return { energy: [], tempo_tol: null, playlists: [], genres: [], allow_shift_b: true, half_double: true }; }

  async init() {
    [this.keys, this.settings] = await Promise.all([api.get("/api/camelot"), api.get("/api/settings")]);
    this.decks = { A: new Deck($("#deckA"), "A", this), B: new Deck($("#deckB"), "B", this) };
    this.library = new Library(this);
    this.bindCenter();
    this.bindGlobal();
    await Promise.all([this.refresh(), this.loadPlaylists(), api.get("/api/mashups").then((m) => (this.mashupCount = m.length))]);
    this.renderPlaylists();
    this.drawWheel();
    this.setCrossfader(0.5);
    this.poll();
    const loop = () => {
      for (const d of Object.values(this.decks)) if (d.playing || d.zoom) d.draw();
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }

  // ------------------------------------------------------------------ data
  keyPopover(anchor, id) { keyPopover(this, anchor, id); }

  keyInfo(code) { return this.keys[code] || { name: "", short: "", compatible: [] }; }
  genres() { return [...new Set(this.tracks.map((t) => t.genre).filter(Boolean))].sort((a, b) => a.localeCompare(b, "pl")); }

  async refresh() {
    this.tracks = await api.get("/api/tracks");
    this.byId = new Map(this.tracks.map((t) => [t.id, t]));
    this.library.render();
    this.renderPlaylists();
  }

  updateTrack(t) {
    const i = this.tracks.findIndex((x) => x.id === t.id);
    if (i >= 0) this.tracks[i] = t; else this.tracks.push(t);
    this.byId.set(t.id, t);
    for (const d of Object.values(this.decks)) if (d.track?.id === t.id) { d.track = { ...t }; d.updateInfo(); }
    this.library.render();
  }

  async poll() {
    try {
      const st = await api.get("/api/status");
      const prev = this.status;
      this.status = st;
      const c = st.counts || {};
      const waiting = (c.pending || 0) + (c.analyzing || 0);
      const pill = $("#queueStatus");
      const stemJobs = (st.jobs || []).filter((j) => j.status === "running");
      if (waiting) { pill.textContent = `Analiza: ${waiting} w kolejce`; pill.className = "pill busy"; }
      else if (stemJobs.length) { pill.textContent = `W tle: ${stemJobs.length} zad.`; pill.className = "pill busy"; }
      else { pill.textContent = `${c.done || 0} utworów gotowych`; pill.className = "pill muted"; }
      if (!prev || JSON.stringify(prev.counts) !== JSON.stringify(st.counts) || prev.analyzing !== st.analyzing) {
        await this.refresh();
        if (this.decks.A.track && !this.ideas.length) this.refreshIdeas();
      }
    } catch { $("#queueStatus").textContent = "Brak połączenia z serwerem"; }
    setTimeout(() => this.poll(), waitingMs(this.status));
  }

  // ------------------------------------------------------------------ decks
  partner(deck) { return deck.name === "A" ? this.decks.B : this.decks.A; }

  async loadDeck(name, id, opts = {}) {
    const t = this.byId.get(id);
    if (!t) return;
    if (t.status !== "done") { toast("Ten utwór nie jest jeszcze przeanalizowany", { error: true }); return; }
    const deck = this.decks[name];
    const load = deck.load({ ...t }, opts);
    this.library.render();
    if (name === "A") {
      this.decks.B.computeSyncTempo() && this.decks.B.track && this.decks.B.loadVariant();
      await this.refreshIdeas({ autoload: !opts.keepB });
    } else this.showIdea();
    await load;
  }

  async swapDecks() {
    const a = this.decks.A, b = this.decks.B;
    const ta = a.track, tb = b.track;
    const pa = { ...a.params }, pb = { ...b.params };
    a.stop(); b.stop();
    if (tb) await this.loadDeck("A", tb.id, { semitones: pb.semitones, stem: pb.stem, keepB: true }); else { a.track = null; a.updateInfo(); }
    if (ta) await this.loadDeck("B", ta.id, { semitones: pa.semitones, stem: pa.stem }); else { b.track = null; b.updateInfo(); }
  }

  onDeckChange(deck) {
    this.drawWheel();
    if (deck.name === "A") {
      const key = deck.key;
      if (key !== this._lastAKey) { this._lastAKey = key; this.refreshIdeas({ autoload: false }); }
      if (this.decks.B.track && this.decks.B.computeSyncTempo()) this.decks.B.loadVariant();
      if (this.library.filters.matchA) this.library.render();
    } else this.showIdea();
  }

  // ------------------------------------------------------------------ ideas
  async refreshIdeas({ autoload = false } = {}) {
    const a = this.decks.A;
    if (!a.track) { this.ideas = []; this.ideaIdx = -1; this.showIdea(); return; }
    try {
      this.ideas = await api.post("/api/ideas", { track_id: a.track.id, shift_a: a.params.semitones, mode: this.mode, ...this.ideaFilter, max_shift_b: 4, allow_shift_b: this.mode === "mix" ? false : this.ideaFilter.allow_shift_b });
    } catch (err) { this.ideas = []; toast(err.message, { error: true }); }
    const bId = this.decks.B.track?.id;
    this.ideaIdx = this.ideas.findIndex((i) => i.track_id === bId);
    const n = Object.values(this.ideaFilter).filter((v) => (Array.isArray(v) ? v.length : v != null && v !== true)).length;
    $("#filterBadge").textContent = n;
    $("#filterBadge").classList.toggle("hidden", !n);
    if (autoload && this.ideas.length) { await this.gotoIdea(0); return; }
    // keep the current pair harmonic: apply the suggested transposition to deck B
    const cur = this.ideas[this.ideaIdx];
    if (cur && this.decks.B.track && cur.shift_b !== this.decks.B.params.semitones) this.decks.B.setSemitones(cur.shift_b);
    this.showIdea();
  }

  showIdea() {
    const n = this.ideas.length;
    const bId = this.decks.B.track?.id;
    if (this.ideaIdx >= 0 && this.ideas[this.ideaIdx]?.track_id !== bId) this.ideaIdx = this.ideas.findIndex((i) => i.track_id === bId);
    $("#ideaCount").textContent = `${this.ideaIdx >= 0 ? this.ideaIdx + 1 : "–"} / ${n}`;
    const idea = this.ideas[this.ideaIdx];
    const box = $("#ideaScore");
    if (idea) box.replaceChildren(h("b", {}, `Zgodność ${Math.round(idea.score)}`), h("br"), idea.reasons.join(" · "));
    else if (this.decks.A.track && !n) box.textContent = "Brak pomysłów. Zmień tonację decku A albo poluzuj filtr.";
    else if (this.decks.A.track && bId) box.textContent = "Deck B spoza listy pomysłów";
    else box.textContent = this.decks.A.track ? "" : "Wczytaj utwór do decku A";
    this.drawWheel();
  }

  async gotoIdea(i) {
    if (!this.ideas.length) return;
    this.ideaIdx = (i + this.ideas.length) % this.ideas.length;
    const idea = this.ideas[this.ideaIdx];
    const b = this.decks.B;
    const wasTesting = this.decks.A.playing && b.playing;
    b.sync = true;
    await this.loadDeck("B", idea.track_id, { semitones: idea.shift_b }); // starts at its first drop
    if (wasTesting) b.play(b.cueTime(b.selCue), b.quantizedStart());
  }

  // ------------------------------------------------------------------ center panel
  bindCenter() {
    $$("#modeSeg button").forEach((b) => b.addEventListener("click", () => {
      this.mode = b.dataset.mode;
      $$("#modeSeg button").forEach((x) => x.classList.toggle("on", x === b));
      this.refreshIdeas({ autoload: false });
    }));
    $("#ideaPrev").addEventListener("click", () => this.gotoIdea(this.ideaIdx - 1));
    $("#ideaNext").addEventListener("click", () => this.gotoIdea(this.ideaIdx + 1));
    $("#btnFilter").addEventListener("click", () => ideaFilterModal(this));
    $("#btnTest").addEventListener("click", () => this.testMashup());
    $("#btnSave").addEventListener("click", () => this.saveMashup());
    $("#xfader").addEventListener("input", (e) => this.setCrossfader(+e.target.value));
    $("#xfader").addEventListener("dblclick", () => { $("#xfader").value = 0.5; this.setCrossfader(0.5); });
  }

  setCrossfader(x) {
    // mashup-style curve: both decks at full volume in the centre
    const ga = Math.min(1, 2 * (1 - x)), gb = Math.min(1, 2 * x);
    this.decks.A.out.gain.setTargetAtTime(ga * 0.8, ctx.currentTime, 0.01);
    this.decks.B.out.gain.setTargetAtTime(gb * 0.8, ctx.currentTime, 0.01);
  }

  testMashup() {
    const a = this.decks.A, b = this.decks.B;
    if (!a.track || !b.track) { toast("Wczytaj utwory do obu decków", { error: true }); return; }
    if (a.playing || b.playing) { a.pause(); b.pause(); $("#btnTest").textContent = "▶ Testuj mashup"; return; }
    if (!b.sync && !a.sync) b.toggleSync(true);
    const t0 = ctx.currentTime + 0.15;
    a.play(a.loop.on ? a.loop.start : a.cueTime(a.selCue), t0);
    b.play(b.loop.on ? b.loop.start : b.cueTime(b.selCue), t0);
    $("#btnTest").textContent = "■ Zatrzymaj";
  }

  async saveMashup() {
    const a = this.decks.A, b = this.decks.B;
    if (!a.track || !b.track) { toast("Wczytaj utwory do obu decków", { error: true }); return; }
    await api.post("/api/mashups", {
      track_a: a.track.id, track_b: b.track.id, shift_a: a.params.semitones, shift_b: b.params.semitones,
      cue_a: a.cueTime(a.selCue), cue_b: b.cueTime(b.selCue), stem_a: a.params.stem, stem_b: b.params.stem, rating: 0,
    });
    this.mashupCount++;
    this.renderPlaylists();
    toast("Mashup zapisany", { action: { label: "Pokaż", fn: () => this.showView("mashups") } });
  }

  async loadMashup(m) {
    this.showView("library");
    await this.loadDeck("A", m.track_a, { semitones: m.shift_a, stem: m.stem_a, keepB: true, cue: Math.max(0, (this.byId.get(m.track_a)?.cues || []).indexOf(m.cue_a)) });
    this.decks.B.sync = true;
    await this.loadDeck("B", m.track_b, { semitones: m.shift_b, stem: m.stem_b, cue: Math.max(0, (this.byId.get(m.track_b)?.cues || []).indexOf(m.cue_b)) });
  }

  // ------------------------------------------------------------------ Camelot wheel
  drawWheel() {
    const el = $("#wheel");
    const aKey = this.decks.A.key, bKey = this.decks.B.key;
    const compat = new Set(aKey ? this.keyInfo(aKey).compatible : []);
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "-100 -100 200 200");
    const arc = (r0, r1, a0, a1) => {
      const p = (r, a) => `${(r * Math.sin(a)).toFixed(2)} ${(-r * Math.cos(a)).toFixed(2)}`;
      return `M${p(r1, a0)} A${r1} ${r1} 0 0 1 ${p(r1, a1)} L${p(r0, a1)} A${r0} ${r0} 0 0 0 ${p(r0, a0)}Z`;
    };
    for (let n = 1; n <= 12; n++) {
      for (const [l, r0, r1] of [["B", 64, 96], ["A", 34, 64]]) {
        const code = `${n}${l}`;
        const a0 = ((n - 1.5) / 12) * 2 * Math.PI, a1 = ((n - 0.5) / 12) * 2 * Math.PI;
        const path = document.createElementNS(NS, "path");
        path.setAttribute("d", arc(r0, r1, a0, a1));
        path.setAttribute("fill", keyColor(code));
        const lit = !aKey || compat.has(code);
        path.setAttribute("fill-opacity", lit ? (code === aKey || code === bKey ? 1 : 0.75) : 0.14);
        path.setAttribute("stroke", code === aKey ? "var(--deck-a)" : code === bKey ? "var(--deck-b)" : "#13161e");
        path.setAttribute("stroke-width", code === aKey || code === bKey ? 4 : 1.2);
        path.style.cursor = "pointer";
        const title = document.createElementNS(NS, "title");
        title.textContent = `${code} · ${this.keyInfo(code).name}${aKey && compat.has(code) && code !== aKey ? " · pasuje do A" : ""}`;
        path.append(title);
        path.addEventListener("click", () => { this.showView("library"); this.library.setKeyFilter([code]); });
        svg.append(path);
        const mid = (a0 + a1) / 2, rm = (r0 + r1) / 2;
        const txt = document.createElementNS(NS, "text");
        txt.setAttribute("x", (rm * Math.sin(mid)).toFixed(1));
        txt.setAttribute("y", (-rm * Math.cos(mid) + 3.5).toFixed(1));
        txt.setAttribute("text-anchor", "middle");
        txt.setAttribute("font-size", "10");
        txt.setAttribute("font-weight", "700");
        txt.setAttribute("fill", lit ? "#0b0d12" : "#6b7387");
        txt.setAttribute("pointer-events", "none");
        txt.textContent = code;
        svg.append(txt);
      }
    }
    const c = document.createElementNS(NS, "text");
    c.setAttribute("text-anchor", "middle"); c.setAttribute("y", "5"); c.setAttribute("font-size", "13"); c.setAttribute("fill", "#e8ebf3"); c.setAttribute("font-weight", "700");
    c.textContent = aKey && bKey ? `${aKey}·${bKey}` : aKey || "";
    svg.append(c);
    el.replaceChildren(svg);
  }

  // ------------------------------------------------------------------ playlists
  async loadPlaylists() { this.playlists = await api.get("/api/playlists"); }

  playlistName() {
    const p = this.currentPlaylist;
    if (p === "all") return "Wszystkie utwory";
    if (p === "queue") return "Kolejka analizy";
    return this.playlists.find((x) => x.id === p)?.name || "Playlista";
  }

  renderPlaylists() {
    const ul = $("#plList");
    const pending = this.tracks.filter((t) => t.status !== "done").length;
    const item = (id, ico, name, count, extra = {}) => {
      const li = h("li", { class: this.currentPlaylist === id && this.view === "library" || (id === "mashups" && this.view === "mashups") ? "active" : "", ...extra },
        h("span", { class: "ico" }, ico), h("span", {}, name), h("span", { class: "count" }, count));
      li.addEventListener("click", () => this.selectPlaylist(id));
      return li;
    };
    const items = [
      item("all", "∞", "Wszystkie utwory", this.tracks.length),
      item("queue", "◔", "Kolejka analizy", pending || "pusta"),
      item("mashups", "★", "Ulubione mashupy", this.mashupCount),
      h("li", { class: "divider" }),
    ];
    for (const p of this.playlists) {
      const li = item(p.id, "♫", p.name, p.count);
      li.addEventListener("dragover", (e) => { if (e.dataTransfer.types.includes("text/x-track")) { e.preventDefault(); li.classList.add("drop"); } });
      li.addEventListener("dragleave", () => li.classList.remove("drop"));
      li.addEventListener("drop", (e) => { li.classList.remove("drop"); e.preventDefault(); this.addToPlaylist(p.id, e.dataTransfer.getData("text/x-track").split(",").map(Number)); });
      li.addEventListener("contextmenu", async (e) => {
        e.preventDefault();
        const name = await prompt("Zmień nazwę playlisty", p.name, "Zostaw puste i kliknij OK, aby usunąć playlistę.");
        if (name === null) return;
        if (name === "") { if (await confirmBox("Usunąć playlistę?", p.name)) await api.del(`/api/playlists/${p.id}`); if (this.currentPlaylist === p.id) this.currentPlaylist = "all"; }
        else await api.patch(`/api/playlists/${p.id}`, { name });
        await this.loadPlaylists(); this.renderPlaylists(); this.library.render();
      });
      li.title = "Prawy klik: zmień nazwę / usuń. Przeciągnij utwory, aby dodać.";
      items.push(li);
    }
    ul.replaceChildren(...items);
    $("#plTools").classList.toggle("hidden", typeof this.currentPlaylist !== "number");
  }

  async selectPlaylist(id) {
    if (id === "mashups") { this.showView("mashups"); return; }
    this.currentPlaylist = id;
    if (typeof id === "number") this.plTrackIds = await api.get(`/api/playlists/${id}/tracks`);
    this.showView("library");
    this.library.render();
  }

  async newPlaylist(ids = []) {
    const name = await prompt("Nowa playlista", "", "Np. „Warm-up”, „Peak time 128”");
    if (!name) return;
    const p = await api.post("/api/playlists", { name });
    if (ids.length) await api.post(`/api/playlists/${p.id}/tracks`, { track_ids: ids });
    await this.loadPlaylists();
    this.renderPlaylists();
  }

  async addToPlaylist(pid, ids) {
    await api.post(`/api/playlists/${pid}/tracks`, { track_ids: ids });
    await this.loadPlaylists();
    if (this.currentPlaylist === pid) this.plTrackIds = await api.get(`/api/playlists/${pid}/tracks`);
    this.renderPlaylists(); this.library.render();
    toast(`Dodano ${ids.length} do playlisty`);
  }

  async removeFromPlaylist(pid, ids) {
    await api.post(`/api/playlists/${pid}/remove`, { track_ids: ids });
    await this.loadPlaylists();
    this.plTrackIds = await api.get(`/api/playlists/${pid}/tracks`);
    this.renderPlaylists(); this.library.render();
  }

  async sortSet() {
    const pid = this.currentPlaylist;
    const curve = h("select", { class: "field" }, [["peak", "Rozgrzewka → szczyt → wyciszenie"], ["build", "Stały wzrost energii"], ["flat", "Równa energia"]].map(([v, l]) => h("option", { value: v }, l)));
    const ok = await new Promise((resolve) => {
      modal([
        h("h3", {}, "Ułóż set harmonicznie"),
        h("p", { class: "sub" }, "Kolejne utwory będą pasowały tonacją i tempem, a energia poprowadzi wybraną krzywą."),
        curve,
        h("div", { class: "actions" }, h("button", { class: "btn", onclick: () => { closeModal(); resolve(false); } }, "Anuluj"),
          h("button", { class: "btn primary", onclick: () => { closeModal(); resolve(true); } }, "Ułóż")),
      ]);
    });
    if (!ok) return;
    this.plTrackIds = await api.post(`/api/playlists/${pid}/harmonic-sort`, { curve: curve.value });
    this.library.sort = { col: null, dir: 1 };
    this.library.render();
    toast("Playlista ułożona");
  }

  // ------------------------------------------------------------------ bulk / stems
  async bulk(ids, action) {
    await api.post("/api/tracks/bulk", { ids, action });
    await this.refresh();
    toast(action === "remove" ? "Usunięto z biblioteki" : action === "reanalyze" ? "Dodano do kolejki analizy" : "Gotowe");
  }

  async stemsFor(ids) {
    toast(`Separacja stemów: ${ids.length} utw. (działa w tle)`);
    for (const id of ids) {
      try { await api.waitJob(await api.post(`/api/tracks/${id}/stems`)); this.updateTrack(await api.get(`/api/tracks/${id}`)); }
      catch (err) { toast(err.message, { error: true }); }
    }
    toast("Stemy gotowe");
  }

  // ------------------------------------------------------------------ views & global
  showView(v) {
    this.view = v;
    closePopover();
    $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === v));
    $$(".view").forEach((el) => el.classList.toggle("hidden", el.id !== `view-${v}`));
    if (v === "mashups") renderMashups(this);
    if (v === "tags") renderTags(this);
    if (v === "settings") renderSettings(this);
    if (v === "keys") renderKeys(this);
    this.renderPlaylists();
  }

  bindGlobal() {
    $$(".tab").forEach((t) => t.addEventListener("click", () => this.showView(t.dataset.view)));
    $("#btnImport").addEventListener("click", () => importModal(this));
    $("#btnHelp").addEventListener("click", () => helpModal());
    $("#btnNewPl").addEventListener("click", () => this.newPlaylist());
    $("#btnSortSet").addEventListener("click", () => this.sortSet());
    $("#btnM3u").addEventListener("click", async () => {
      try {
        const r = await api.post(`/api/playlists/${this.currentPlaylist}/m3u`);
        toast(`Zapisano playlistę: ${r.path}`, { ms: 8000, action: { label: "Otwórz folder", fn: () => api.post("/api/open-folder", { path: r.path }) } });
      } catch (err) { toast(err.message, { error: true }); }
    });
    // drag files from the OS
    let depth = 0;
    const ov = $("#dropOverlay");
    const isFiles = (e) => e.dataTransfer?.types?.includes("Files");
    window.addEventListener("dragenter", (e) => { if (isFiles(e)) { depth++; ov.classList.remove("hidden"); } });
    window.addEventListener("dragleave", (e) => { if (isFiles(e) && --depth <= 0) { depth = 0; ov.classList.add("hidden"); } });
    window.addEventListener("dragover", (e) => { if (isFiles(e)) e.preventDefault(); });
    window.addEventListener("drop", (e) => {
      if (!isFiles(e)) return;
      e.preventDefault(); depth = 0; ov.classList.add("hidden");
      uploadFiles(this, [...e.dataTransfer.files]);
    });
    window.addEventListener("keydown", (e) => {
      if (e.target.closest("input,textarea,select") || document.querySelector(".modal-back")) return;
      const A = this.decks.A, B = this.decks.B;
      if (e.code === "Space") { e.preventDefault(); this.testMashup(); }
      else if (e.code === "KeyQ") A.toggle();
      else if (e.code === "KeyW") B.toggle();
      else if (e.code === "ArrowRight") { e.preventDefault(); this.gotoIdea(this.ideaIdx + 1); }
      else if (e.code === "ArrowLeft") { e.preventDefault(); this.gotoIdea(this.ideaIdx - 1); }
      else if (e.code === "KeyL") A.toggleLoop();
      else if (e.code === "KeyK") B.toggleLoop();
      else if (e.code === "KeyC") (e.shiftKey ? B : A).addCueHere();
      else if (/^Digit[1-8]$/.test(e.code)) (e.shiftKey ? B : A).jumpCue(Number(e.code.slice(5)) - 1);
      else return;
      this.syncTestButton();
    });
    setInterval(() => this.syncTestButton(), 500);
  }

  syncTestButton() {
    const any = this.decks.A.playing || this.decks.B.playing;
    $("#btnTest").textContent = any ? "■ Zatrzymaj" : "▶ Testuj mashup";
  }
}

function waitingMs(st) {
  const c = st?.counts || {};
  return (c.pending || c.analyzing || (st?.jobs || []).some((j) => j.status === "running")) ? 2000 : 6000;
}

const app = new App();
window.blendora = app;
app.init().catch((err) => { console.error(err); toast(`Błąd startu: ${err.message}`, { error: true, ms: 10000 }); });
