// A deck: Web Audio playback of a (pre-rendered) track variant + waveform view + controls.
import { api } from "./api.js";
import { h, $, popover, closePopover, toast, fmtTime, keyBadge, transpose } from "./ui.js";

export const ctx = new (window.AudioContext || window.webkitAudioContext)();
const ZOOM_BARS = [0, 64, 32, 16, 8];
const STEM_NAMES = { full: "Cały utwór", vocals: "Acapella", instrumental: "Instrumental" };

function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function hexRgb(hex) { const n = parseInt(hex.replace("#", ""), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }

export class Deck {
  constructor(root, name, app) {
    this.root = root;
    this.name = name;
    this.app = app;
    this.track = null;
    this.wave = null;
    this.vocals = null;
    this.params = { stem: "full", semitones: 0, tempo: 1 };
    this.buffer = null;
    this.source = null;
    this.playing = false;
    this.startCtx = 0;
    this.startBuf = 0;
    this.pausedOrig = 0;
    this.loop = { on: false, beats: 16, start: 0, end: 0 };
    this.zoom = 0;
    this.selCue = 0;
    this.sync = name === "B";
    this.loadToken = 0;
    this.busy = "";
    this.out = ctx.createGain();
    this.out.connect(ctx.destination);
    this.build();
  }

  // ------------------------------------------------------------------ UI
  build() {
    const r = this.root;
    r.innerHTML = "";
    this.canvas = h("canvas");
    this.busyEl = h("div", { class: "wave-busy hidden" });
    this.emptyEl = h("div", { class: "wave-empty" }, `Deck ${this.name}: kliknij dwukrotnie utwór w bibliotece albo przeciągnij go tutaj`);
    const zoom = h("div", { class: "wave-zoom" },
      h("button", { class: "btn icon", title: "Oddal", onclick: (e) => { e.stopPropagation(); this.setZoom(this.zoom - 1); } }, "−"),
      h("button", { class: "btn icon", title: "Przybliż", onclick: (e) => { e.stopPropagation(); this.setZoom(this.zoom + 1); } }, "+"));
    this.waveBox = h("div", { class: "wave-box" }, this.canvas, this.emptyEl, zoom, this.busyEl);
    this.waveBox.addEventListener("mousedown", (e) => this.onWaveClick(e));
    this.waveBox.addEventListener("wheel", (e) => { if (!this.track) return; e.preventDefault(); this.setZoom(this.zoom + (e.deltaY < 0 ? 1 : -1)); }, { passive: false });

    this.playBtn = h("button", { class: "play", title: "Odtwórz / pauza", onclick: () => this.toggle() }, "▶");
    this.nameEl = h("div", { class: "deck-name" }, "—");
    this.swapBtn = h("button", { class: "btn", onclick: () => this.app.swapDecks() }, this.name === "A" ? "Do decku B →" : "← Do decku A");
    this.keyEl = h("span");
    this.keyName = h("span");
    this.bpmEl = h("span");
    this.energyEl = h("span");
    this.timeEl = h("span", { class: "deck-time" }, "0:00 / 0:00");

    this.keyBtn = h("button", { class: "btn", onclick: (e) => this.keyPopover(e.currentTarget) }, "⇅ Tonacja");
    this.stemBtn = h("button", { class: "btn", onclick: (e) => this.stemPopover(e.currentTarget) }, "≋ Stemy");
    this.loopBtn = h("button", { class: "btn", title: "Włącz/wyłącz pętlę (L)", onclick: () => this.toggleLoop() }, "⟳ Pętla");
    this.loopSize = h("button", { class: "btn", onclick: (e) => this.loopPopover(e.currentTarget) }, "16");
    this.syncBtn = h("button", { class: "btn" + (this.sync ? " on" : ""), title: "Dopasuj tempo do drugiego decku (bez zmiany tonacji)", onclick: () => this.toggleSync() }, "Sync");
    this.exportBtn = h("button", { class: "btn right", onclick: (e) => this.exportPopover(e.currentTarget) }, "⤓ Eksport");

    r.append(
      this.waveBox,
      h("div", { class: "deck-title" }, this.playBtn, this.nameEl, this.swapBtn),
      h("div", { class: "deck-info" }, this.keyEl, this.keyName, h("span", { class: "sep" }), this.bpmEl, h("span", { class: "sep" }), this.energyEl, this.timeEl),
      h("div", { class: "deck-ctrl" }, this.keyBtn, this.stemBtn, h("span", { style: { display: "inline-flex" } }, this.loopBtn, this.loopSize), this.syncBtn, this.exportBtn),
    );
    // drag & drop from library
    r.addEventListener("dragover", (e) => { if (e.dataTransfer.types.includes("text/x-track")) { e.preventDefault(); r.classList.add("drop-target"); } });
    r.addEventListener("dragleave", () => r.classList.remove("drop-target"));
    r.addEventListener("drop", (e) => {
      r.classList.remove("drop-target");
      const ids = e.dataTransfer.getData("text/x-track");
      if (ids) { e.preventDefault(); this.app.loadDeck(this.name, Number(ids.split(",")[0])); }
    });
    new ResizeObserver(() => this.resize()).observe(this.waveBox);
    this.updateInfo();
  }

  resize() {
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.max(10, Math.floor(this.waveBox.clientWidth * dpr));
    this.canvas.height = Math.max(10, Math.floor(this.waveBox.clientHeight * dpr));
    this.draw();
  }

  setBusy(msg) {
    this.busy = msg;
    this.busyEl.textContent = msg;
    this.busyEl.classList.toggle("hidden", !msg);
  }

  get bpm() { return this.track ? this.track.bpm * this.params.tempo : 0; }
  get key() { return this.track ? transpose(this.track.key, this.params.semitones) : null; }

  updateInfo() {
    const t = this.track;
    this.nameEl.innerHTML = "";
    this.nameEl.append(t ? t.title || "—" : "Pusty deck", h("small", {}, t ? t.artist || "" : ""));
    this.keyEl.replaceChildren(keyBadge(this.key, this.params.semitones ? "shifted" : ""));
    const kn = t && t.key ? this.app.keyInfo(this.key).name : "";
    this.keyName.replaceChildren(kn, this.params.semitones ? h("span", { class: "shifted" }, ` (${this.params.semitones > 0 ? "+" : ""}${this.params.semitones})`) : "");
    const bpm = t ? t.bpm : 0;
    this.bpmEl.replaceChildren("Tempo ", h("b", {}, bpm ? (+bpm.toFixed(1)).toString() : "–"),
      Math.abs(this.params.tempo - 1) > 1e-4 ? h("span", { class: "shifted" }, ` → ${this.bpm.toFixed(1)}`) : "");
    this.energyEl.replaceChildren("Energia ", h("b", {}, t?.energy ?? "–"));
    this.keyBtn.classList.toggle("on", !!this.params.semitones);
    this.stemBtn.replaceChildren("≋ " + (this.params.stem === "full" ? "Stemy" : STEM_NAMES[this.params.stem]));
    this.stemBtn.classList.toggle("on", this.params.stem !== "full");
    this.loopBtn.classList.toggle("loop-on", this.loop.on);
    this.loopSize.textContent = String(this.loop.beats);
    this.syncBtn.classList.toggle("on", this.sync);
    this.playBtn.classList.toggle("on", this.playing);
    this.playBtn.textContent = this.playing ? "❚❚" : "▶";
    this.emptyEl.classList.toggle("hidden", !!t);
    this.draw();
  }

  // ------------------------------------------------------------------ loading
  async load(track, { semitones = 0, stem = "full", cue = 0 } = {}) {
    this.stop();
    this.track = track;
    this.params = { stem: track.stems && track.stems !== "none" && track.stems !== "running" ? stem : "full", semitones, tempo: 1 };
    this.loop.on = false;
    this.selCue = cue;
    this.pausedOrig = this.cueTime(cue);
    this.wave = null;
    this.vocals = null;
    this.zoom = 0;
    this.updateInfo();
    api.get(`/api/tracks/${track.id}/waveform`).then((w) => { if (this.track?.id === track.id) { this.wave = w; this.draw(); } }).catch(() => {});
    this.loadVocals();
    this.computeSyncTempo();
    await this.loadVariant();
  }

  loadVocals() {
    const id = this.track?.id;
    if (!id || !this.track.stems || this.track.stems === "none") return;
    api.get(`/api/tracks/${id}/vocals`).then((v) => { if (this.track?.id === id) { this.vocals = v; this.draw(); } }).catch(() => {});
  }

  async loadVariant() {
    if (!this.track) return;
    const token = ++this.loadToken;
    const { stem, semitones, tempo } = this.params;
    const what = stem !== "full" ? STEM_NAMES[stem] : semitones ? "zmiana tonacji" : Math.abs(tempo - 1) > 1e-4 ? "dopasowanie tempa" : "wczytywanie";
    this.setBusy(`⏳ ${what}…`);
    const wasPlaying = this.playing;
    const pos = this.position();
    try {
      const url = `/api/tracks/${this.track.id}/audio?stem=${stem}&semitones=${semitones}&tempo=${tempo.toFixed(5)}`;
      const r = await fetch(url);
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      const buf = await ctx.decodeAudioData(await r.arrayBuffer());
      if (token !== this.loadToken) return;
      this.buffer = buf;
      this.bufTempo = tempo;
      if (wasPlaying && this.playing) this.play(this.position());
      else this.pausedOrig = pos;
      this.setBusy("");
    } catch (err) {
      if (token === this.loadToken) { this.setBusy(""); toast(`Deck ${this.name}: ${err.message}`, { error: true }); }
    }
    this.updateInfo();
    this.app.onDeckChange(this);
  }

  // ------------------------------------------------------------------ transport
  toOrig(b) { return b * (this.bufTempo || 1); }
  toBuf(o) { return o / (this.bufTempo || 1); }

  position() {
    if (!this.playing || !this.buffer) return this.pausedOrig;
    let b = this.startBuf + (ctx.currentTime - this.startCtx);
    if (b < this.startBuf) return this.toOrig(this.startBuf); // scheduled in the future
    if (this.source?.loop) {
      const ls = this.source.loopStart, le = this.source.loopEnd;
      if (b >= le && le > ls) b = ls + ((b - ls) % (le - ls));
    }
    return this.toOrig(Math.min(b, this.buffer.duration));
  }

  play(fromOrig = this.position(), when = 0) {
    if (!this.buffer) return;
    if (ctx.state === "suspended") ctx.resume();
    this.killSource();
    const src = ctx.createBufferSource();
    src.buffer = this.buffer;
    src.connect(this.out);
    const startB = Math.max(0, Math.min(this.toBuf(fromOrig), this.buffer.duration - 0.01));
    if (this.loop.on) this.applyLoop(src);
    const at = Math.max(ctx.currentTime, when || ctx.currentTime);
    src.start(at, startB);
    src.onended = () => { if (this.source === src && !src.loop) { this.playing = false; this.pausedOrig = 0; this.updateInfo(); } };
    this.source = src;
    this.startCtx = at;
    this.startBuf = startB;
    this.playing = true;
    this.updateInfo();
  }

  killSource() {
    if (this.source) { this.source.onended = null; try { this.source.stop(); } catch {} this.source.disconnect(); this.source = null; }
  }

  pause() {
    this.pausedOrig = this.position();
    this.killSource();
    this.playing = false;
    this.updateInfo();
  }

  stop() { this.killSource(); this.playing = false; }

  toggle() {
    if (!this.track) return;
    if (this.playing) this.pause();
    else this.play(this.pausedOrig, this.quantizedStart());
  }

  seek(orig) {
    orig = Math.max(0, Math.min(orig, this.track?.duration || 0));
    if (this.playing) this.play(orig, this.quantizedStart());
    else { this.pausedOrig = orig; this.draw(); }
  }

  // when the partner deck plays and sync is on, start on its next bar so downbeats line up
  quantizedStart() {
    const p = this.app.partner(this);
    if (!this.sync && !p.sync) return 0;
    if (!p.playing || !p.track) return 0;
    const bar = 240 / p.track.bpm;
    const pos = p.position();
    const fd = p.track.first_downbeat || 0;
    let next = fd + Math.ceil((pos - fd) / bar + 1e-6) * bar;
    let dt = (next - pos) / (p.bufTempo || 1);
    if (dt < 0.06) dt += bar / (p.bufTempo || 1);
    return ctx.currentTime + dt;
  }

  cues() { return this.track?.cues || []; }
  cueTime(i) { const c = this.cues(); return c.length ? c[Math.min(i, c.length - 1)] : 0; }

  jumpCue(i) {
    if (!this.track || i >= this.cues().length) return;
    this.selCue = i;
    const t = this.cueTime(i);
    if (this.loop.on) this.setLoopAt(t);
    if (this.playing) this.play(t, this.quantizedStart());
    else { this.pausedOrig = t; this.draw(); }
  }

  // ------------------------------------------------------------------ loop
  beatLen() { return 60 / (this.track?.bpm || 120); }
  snapBeat(t) {
    const b = this.beatLen(), off = this.track?.beat_offset || 0;
    return off + Math.floor((t - off) / b + 0.02) * b;
  }
  setLoopAt(t) {
    this.loop.start = Math.max(0, this.snapBeat(t));
    this.loop.end = this.loop.start + this.loop.beats * this.beatLen();
  }
  applyLoop(src = this.source) {
    if (!src) return;
    src.loop = this.loop.on;
    if (this.loop.on) { src.loopStart = this.toBuf(this.loop.start); src.loopEnd = this.toBuf(this.loop.end); }
  }
  toggleLoop(force) {
    if (!this.track) return;
    const on = force ?? !this.loop.on;
    if (on && !this.loop.on) this.setLoopAt(this.position());
    this.loop.on = on;
    if (this.playing) {
      const pos = this.position();
      if (on && (pos < this.loop.start || pos >= this.loop.end)) this.play(this.loop.start);
      else if (on) this.applyLoop();
      else this.play(pos); // restart without loop so position bookkeeping stays exact
    }
    this.updateInfo();
  }
  setLoopBeats(n) {
    this.loop.beats = n;
    if (this.loop.on) { this.loop.end = this.loop.start + n * this.beatLen(); if (this.playing) this.play(this.position()); }
    this.updateInfo();
  }

  loopPopover(anchor) {
    const grid = h("div", { class: "pop-grid" }, [1, 2, 4, 8, 16, 32, 64].map((n) =>
      h("button", { class: "btn" + (this.loop.beats === n ? " sel" : ""), onclick: () => { this.setLoopBeats(n); closePopover(); } }, String(n))),
      h("button", { class: "btn", title: "Pętla od wybranego cue", onclick: () => { this.setLoopAt(this.cueTime(this.selCue)); this.loop.on = true; this.play(this.loop.start); closePopover(); } }, `Cue ${this.selCue + 1}`));
    popover(anchor, [
      h("h4", {}, "Długość pętli (bity)"), grid,
      h("div", { class: "pop-row" },
        h("button", { class: "btn", onclick: () => { this.loop.start = this.snapBeat(this.position()); this.loop.inSet = true; toast(`Deck ${this.name}: początek pętli ustawiony`); } }, "Loop in"),
        h("button", { class: "btn", onclick: () => {
          const end = this.snapBeat(this.position());
          if (!this.loop.inSet || end <= this.loop.start) { toast("Najpierw ustaw „Loop in”", { error: true }); return; }
          this.loop.end = end; this.loop.on = true; this.loop.inSet = false;
          this.loop.beats = Math.round((end - this.loop.start) / this.beatLen());
          if (this.playing) this.play(this.loop.start); this.updateInfo(); closePopover();
        } }, "Loop out")),
    ]);
  }

  // ------------------------------------------------------------------ key / tempo / stems
  async setSemitones(n) {
    if (!this.track || n === this.params.semitones) return;
    this.params.semitones = n;
    this.updateInfo();
    await this.loadVariant();
  }

  keyPopover(anchor) {
    if (!this.track) return;
    const orig = this.track.key;
    const changed = h("span");
    const val = h("b", {}, "0");
    const refresh = (v) => {
      changed.replaceChildren(keyBadge(transpose(orig, v)), " ", this.app.keyInfo(transpose(orig, v)).short);
      val.textContent = (v > 0 ? "+" : "") + v;
    };
    const range = h("input", { type: "range", min: -4, max: 4, step: 1, value: this.params.semitones });
    range.addEventListener("input", () => refresh(+range.value));
    range.addEventListener("change", () => this.setSemitones(+range.value));
    refresh(this.params.semitones);
    popover(anchor, [
      h("div", { class: "pop-row" }, "Oryginał", keyBadge(orig), this.app.keyInfo(orig).short, h("span", { class: "muted" }, "→"), changed),
      h("div", { class: "slider-row", style: { marginTop: "10px" } }, "−4", range, "+4"),
      h("div", { class: "pop-row", style: { justifyContent: "space-between", marginTop: "6px" } },
        h("span", { class: "muted" }, "Półtony: ", val),
        h("button", { class: "btn link", onclick: () => { range.value = 0; refresh(0); this.setSemitones(0); } }, "Reset")),
      h("p", { class: "muted", style: { fontSize: "11px", maxWidth: "260px", margin: "8px 0 0" } }, "Zmiana tonacji odświeża listę pomysłów. Tempo zostaje bez zmian."),
    ]);
  }

  computeSyncTempo() {
    const p = this.app.partner(this);
    let tempo = 1;
    if (this.sync && this.track && p.track && p.track.bpm && this.track.bpm) {
      const target = p.track.bpm * (p.params.tempo || 1);
      const factors = [0.5, 1, 2];
      const f = factors.reduce((a, b) => Math.abs(Math.log((this.track.bpm * b) / target)) < Math.abs(Math.log((this.track.bpm * a) / target)) ? b : a);
      tempo = target / (this.track.bpm * f);
      if (tempo < 0.75 || tempo > 1.33) tempo = 1; // too far to stretch musically
    }
    tempo = Math.round(tempo * 1e5) / 1e5;
    const changed = Math.abs(tempo - this.params.tempo) > 1e-5;
    this.params.tempo = tempo;
    return changed;
  }

  async toggleSync(force) {
    this.sync = force ?? !this.sync;
    if (this.sync) { const p = this.app.partner(this); if (p.sync) { p.sync = false; p.computeSyncTempo() && (await p.loadVariant()); p.updateInfo(); } }
    if (this.computeSyncTempo()) await this.loadVariant();
    this.updateInfo();
  }

  async refreshSync() { if (this.computeSyncTempo()) await this.loadVariant(); }

  async setStem(stem) {
    if (!this.track || stem === this.params.stem) return;
    this.params.stem = stem;
    this.updateInfo();
    await this.loadVariant();
  }

  stemPopover(anchor) {
    if (!this.track) return;
    const t = this.track;
    const ready = t.stems && t.stems !== "none" && t.stems !== "running";
    const content = h("div");
    const render = () => {
      content.replaceChildren();
      if (ready) {
        content.append(h("div", { class: "pop-row" }, ["full", "vocals", "instrumental"].map((s) =>
          h("button", { class: "btn" + (this.params.stem === s ? " accent" : ""), onclick: () => { this.setStem(s); closePopover(); } }, STEM_NAMES[s]))));
        content.append(h("p", { class: "muted", style: { fontSize: "11px", margin: "8px 0 0" } },
          t.stems === "demucs" ? "Separacja: Demucs (wysoka jakość, lokalnie)." : "Separacja: szybki podgląd. Zainstaluj Demucs, aby uzyskać wysoką jakość, i rozdziel ponownie.",
          t.vocal_ratio != null ? ` Wokal w ~${Math.round(t.vocal_ratio * 100)}% utworu.` : ""));
        if (t.stems !== "demucs" && this.app.status?.demucs) content.append(h("button", { class: "btn", style: { marginTop: "6px" }, onclick: () => this.runStems(true) }, "Rozdziel ponownie (Demucs)"));
      } else {
        const prog = h("div", { class: "progress" }, h("div"));
        content.append(
          h("h4", {}, "Ten utwór nie ma jeszcze stemów"),
          h("button", { class: "btn accent wide", onclick: (e) => { e.currentTarget.disabled = true; this.runStems(false, prog.firstChild); } }, "Rozdziel: wokal / instrumental"),
          h("div", { style: { marginTop: "8px" } }, prog),
          h("p", { class: "muted", style: { fontSize: "11px", maxWidth: "260px", margin: "8px 0 0" } },
            this.app.status?.demucs ? "Demucs działa lokalnie. Na CPU zajmuje to zwykle 1–3 minuty." : "Demucs nie jest zainstalowany: użyję szybkiego podglądu niskiej jakości."));
      }
    };
    render();
    popover(anchor, content);
  }

  async runStems(force = false, bar) {
    const t = this.track;
    try {
      let job = await api.post(`/api/tracks/${t.id}/stems${force ? "?force=1" : ""}`);
      this.setBusy("⏳ separacja stemów…");
      if (job.status === "running") job = await api.waitJob(job, (p) => { if (bar) bar.style.width = `${Math.round(p * 100)}%`; this.setBusy(`⏳ separacja stemów ${Math.round(p * 100)}%`); });
      const fresh = await api.get(`/api/tracks/${t.id}`);
      this.app.updateTrack(fresh);
      if (this.track?.id === t.id) { this.track = fresh; this.loadVocals(); }
      this.setBusy("");
      closePopover();
      toast(`Stemy gotowe: ${t.title}`);
      this.updateInfo();
    } catch (err) {
      this.setBusy("");
      toast(err.message, { error: true });
    }
  }

  exportPopover(anchor) {
    if (!this.track) return;
    const fmt = h("select", { class: "field" }, ["wav", "flac", "mp3"].map((f) => h("option", { value: f, selected: f === this.app.settings?.export_format }, f.toUpperCase())));
    const stem = h("select", { class: "field" }, Object.entries(STEM_NAMES).map(([k, v]) => h("option", { value: k, selected: k === this.params.stem }, v)));
    popover(anchor, [
      h("h4", {}, `Eksport z tonacją ${this.key}${this.params.semitones ? ` (${this.params.semitones > 0 ? "+" : ""}${this.params.semitones})` : ""}`),
      h("div", { class: "pop-row" }, stem, fmt),
      h("div", { class: "pop-row" }, h("button", { class: "btn accent wide", onclick: async () => {
        closePopover();
        try {
          let job = await api.post("/api/export", { track_id: this.track.id, stem: stem.value, semitones: this.params.semitones, format: fmt.value });
          toast("Eksport w toku…");
          job = await api.waitJob(job);
          toast(`Zapisano: ${job.result.path}`, { ms: 8000, action: { label: "Otwórz folder", fn: () => api.post("/api/open-folder", { path: job.result.path }) } });
        } catch (err) { toast(err.message, { error: true }); }
      } }, "Eksportuj do folderu")),
    ], { align: "right" });
  }

  setZoom(z) { this.zoom = Math.max(0, Math.min(ZOOM_BARS.length - 1, z)); this.draw(); }

  // ------------------------------------------------------------------ waveform
  view() {
    const dur = this.track?.duration || this.wave?.duration || 1;
    if (!this.zoom || !this.track?.bpm) return [0, dur];
    const span = Math.min(dur, ZOOM_BARS[this.zoom] * 240 / this.track.bpm);
    let v0 = this.position() - span * 0.35;
    v0 = Math.max(0, Math.min(v0, dur - span));
    return [v0, v0 + span];
  }

  onWaveClick(e) {
    if (!this.track) return;
    const rect = this.canvas.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = e.clientY - rect.top;
    const [v0, v1] = this.view();
    const t = v0 + x * (v1 - v0);
    if (y < 22) { // cue labels row
      const pxPerSec = rect.width / (v1 - v0);
      let best = -1, bd = 1e9;
      this.cues().forEach((c, i) => { const d = (t - c) * pxPerSec; if (d >= -4 && d < 48 && d < bd) { bd = d; best = i; } });
      if (best >= 0) { this.jumpCue(best); return; }
    }
    this.seek(t);
  }

  draw() {
    const c = this.canvas, g = c.getContext("2d");
    const W = c.width, H = c.height;
    if (!W || !H) return;
    const dpr = window.devicePixelRatio || 1;
    g.clearRect(0, 0, W, H);
    const t = this.track;
    if (!t) return;
    const [v0, v1] = this.view();
    const span = v1 - v0;
    const X = (sec) => ((sec - v0) / span) * W;
    const pos = this.position();
    const mid = H / 2 + 4 * dpr, amp = H / 2 - 20 * dpr;
    const deckColor = css(this.name === "A" ? "--deck-a" : "--deck-b");

    // beat / bar grid when zoomed in
    if (this.zoom && t.bpm) {
      const beat = 60 / t.bpm, fd = t.first_downbeat || 0;
      let k = Math.ceil((v0 - fd) / beat);
      for (let bt = fd + k * beat; bt < v1; bt += beat, k++) {
        g.fillStyle = k % 4 === 0 ? "rgba(255,255,255,.16)" : "rgba(255,255,255,.05)";
        g.fillRect(Math.round(X(bt)), 0, 1, H);
      }
    }
    // waveform
    const w = this.wave;
    if (w) {
      const pps = w.pps, n = w.peak.length;
      const low = hexRgb(css("--wave-low")), midc = hexRgb(css("--wave-mid")), high = hexRgb(css("--wave-high"));
      for (let x = 0; x < W; x++) {
        const ta = v0 + (x / W) * span, tb = v0 + ((x + 1) / W) * span;
        let i0 = Math.floor(ta * pps), i1 = Math.max(i0 + 1, Math.floor(tb * pps));
        if (i0 >= n) break;
        i1 = Math.min(i1, n);
        let pk = 0, l = 0, m = 0, hi = 0;
        for (let i = i0; i < i1; i++) { if (w.peak[i] > pk) pk = w.peak[i]; l += w.low[i]; m += w.mid[i]; hi += w.high[i]; }
        const s = l + m + hi || 1;
        const r = (low[0] * l + midc[0] * m + high[0] * hi) / s, gg = (low[1] * l + midc[1] * m + high[1] * hi) / s, b = (low[2] * l + midc[2] * m + high[2] * hi) / s;
        const hh = (pk / 255) * amp;
        const played = ta < pos;
        g.fillStyle = `rgba(${r | 0},${gg | 0},${b | 0},${played ? 0.45 : 0.95})`;
        g.fillRect(x, mid - hh, 1, hh * 2 || 1);
      }
    } else {
      g.fillStyle = "rgba(255,255,255,.08)";
      g.fillRect(0, mid - 1, W, 2);
    }
    // vocal activity strip (from stems)
    if (this.vocals?.activity) {
      const a = this.vocals.activity;
      for (let s = Math.max(0, Math.floor(v0)); s < Math.min(a.length, v1); s++) {
        g.fillStyle = `rgba(255,93,115,${(a[s] / 255) * 0.9})`;
        g.fillRect(X(s), H - 4 * dpr, Math.max(1, X(s + 1) - X(s)), 3 * dpr);
      }
    }
    // loop region
    if (this.loop.on || this.loop.inSet) {
      const a = X(this.loop.start), b = this.loop.on ? X(this.loop.end) : a + 2;
      g.fillStyle = "rgba(255,181,71,.18)";
      g.fillRect(a, 0, b - a, H);
      g.fillStyle = css("--warn");
      g.fillRect(a, 0, 2 * dpr, H);
      if (this.loop.on) g.fillRect(b - 2 * dpr, 0, 2 * dpr, H);
    }
    // segments with energy labels
    g.font = `${10 * dpr}px ${css("--font") || "sans-serif"}`;
    g.textBaseline = "bottom";
    for (const sgm of t.segments || []) {
      const a = X(sgm.start), b = X(sgm.end);
      if (b < 0 || a > W || b - a < 26 * dpr) continue;
      const label = b - a > 70 * dpr ? `Energia ${sgm.energy}` : String(sgm.energy);
      g.fillStyle = "rgba(0,0,0,.55)";
      const tw = g.measureText(label).width + 8 * dpr;
      const cx = (Math.max(a, 0) + Math.min(b, W)) / 2;
      g.fillRect(cx - tw / 2, H - 18 * dpr, tw, 13 * dpr);
      g.fillStyle = "#d5dae6";
      g.textAlign = "center";
      g.fillText(label, cx, H - 6 * dpr);
    }
    // cue markers
    g.textAlign = "left";
    g.textBaseline = "middle";
    this.cues().forEach((cue, i) => {
      const x = X(cue);
      if (x < -50 || x > W) return;
      const sel = i === this.selCue;
      g.fillStyle = sel ? deckColor : "rgba(255,255,255,.75)";
      g.fillRect(x, 0, 1.5 * dpr, H);
      const label = span > 400 && W / dpr < 520 ? String(i + 1) : `Cue ${i + 1}`;
      const tw = g.measureText(label).width + 8 * dpr;
      g.fillRect(x, 2 * dpr, tw, 15 * dpr);
      g.fillStyle = "#0b0d12";
      g.fillText(label, x + 4 * dpr, 10 * dpr);
    });
    // playhead
    g.fillStyle = "#fff";
    g.fillRect(X(pos) - dpr, 0, 2 * dpr, H);
    if (this.track) this.timeEl.textContent = `${fmtTime(pos)} / ${fmtTime(t.duration)}`;
  }
}
