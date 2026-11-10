// A deck: Web Audio playback of a (pre-rendered) track variant + waveform view + controls.
import { api } from "./api.js";
import { h, $, popover, closePopover, toast, fmtTime, keyBadge, transpose } from "./ui.js";
import { Channel, knob, SourceGroup, STEMS4, STEM4_NAMES, STEM4_COLORS } from "./mixer.js";

export const ctx = new (window.AudioContext || window.webkitAudioContext)();
const ZOOM_BARS = [0, 64, 32, 16, 8, 4, 2];
const STEM_NAMES = { full: "Cały utwór", vocals: "Acapella", instrumental: "Instrumental", stems: "4 ścieżki" };

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
    this.rate = 1;
    this.vari = 1; // varispeed while the time-stretched version is still being prepared
    this.pendingPlay = false;
    this.beatmap = null;
    this.userOffset = 0; // phase the DJ set by ear with the nudge buttons while synced (s of track time)
    this.nudging = false;
    this.pausedOrig = 0;
    this.loop = { on: false, beats: 16, start: 0, end: 0 };
    this.zoom = 0;
    this.selCue = 0;
    this.sync = name === "B";
    this.loadToken = 0;
    this.busy = "";
    this.out = ctx.createGain();
    this.out.connect(ctx.destination);
    // channel strip: (stem gains) -> EQ isolator -> filter -> crossfader gain
    this.channel = new Channel(ctx, this.out);
    this.stemGains = Object.fromEntries(STEMS4.map((st) => { const g = ctx.createGain(); g.connect(this.channel.input); return [st, g]; }));
    this.mutes = new Set();
    this.stemBuffers = null;
    this.stemWave = null;
    this.keepDrums = true; // key change: shift vocals/bass/melody only, drums stay natural
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
    this.waveBox.addEventListener("pointerdown", (e) => this.onWaveDown(e));
    this.waveBox.addEventListener("contextmenu", (e) => {
      if (!this.track) return;
      e.preventDefault();
      const rect = this.canvas.getBoundingClientRect();
      const [v0, v1] = this.view();
      const t = v0 + ((e.clientX - rect.left) / rect.width) * (v1 - v0);
      const pxPerSec = rect.width / (v1 - v0);
      this.cues().forEach((c, i) => { const d = (t - c) * pxPerSec; if (d >= -4 && d < 48) this.selCue = i; });
      this.draw();
      this.gridPopover(this.gridBtn);
    });
    this.waveBox.addEventListener("wheel", (e) => this.onWaveWheel(e), { passive: false });

    this.playBtn = h("button", { class: "play", title: "Odtwórz / pauza (Q / W)", onpointerdown: (e) => { if (e.button === 0) this.toggle(); } }, "▶"); // on press, not release: like a hardware button
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
    this.exportBtn = h("button", { class: "btn right", title: "Eksport z aktualną tonacją / stemem", onclick: (e) => this.exportPopover(e.currentTarget) }, "⤓");
    this.gridBtn = h("button", { class: "btn", title: "Popraw siatkę beatów, BPM i cue pointy", onclick: (e) => this.gridPopover(e.currentTarget) }, "▦ Siatka");
    const nudge = (label, r, title) => {
      const b = h("button", { class: "btn nudge", title }, label);
      const on = (e) => { e.preventDefault(); this.nudging = true; this.setRate(r); b.classList.add("on"); };
      const off = () => {
        if (!this.nudging) return;
        this.nudging = false;
        if (this.rate !== 1) this.setRate(1);
        b.classList.remove("on");
        const e = this.lockError(true);
        if (e != null) this.userOffset = e; // keep the phase the DJ chose by ear
      };
      b.addEventListener("pointerdown", on);
      ["pointerup", "pointerleave", "pointercancel"].forEach((ev) => b.addEventListener(ev, off));
      return b;
    };
    this.nudgeEl = h("span", { class: "nudge-group", title: "Przytrzymaj, aby chwilowo zwolnić / przyspieszyć i dograć fazę na ucho" },
      nudge("‹", 0.96, "Przytrzymaj: zwolnij"), nudge("›", 1.04, "Przytrzymaj: przyspiesz"));

    r.append(
      this.waveBox,
      h("div", { class: "deck-title" }, this.playBtn, this.nameEl, this.swapBtn),
      h("div", { class: "deck-info" }, this.keyEl, this.keyName, h("span", { class: "sep" }), this.bpmEl, h("span", { class: "sep" }), this.energyEl, this.timeEl),
      this.padsEl = h("div", { class: "cue-pads" }),
      this.buildMixer(),
      h("div", { class: "deck-ctrl" }, this.keyBtn, this.stemBtn, h("span", { style: { display: "inline-flex" } }, this.loopBtn, this.loopSize), this.syncBtn, this.nudgeEl, this.gridBtn, this.exportBtn),
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
    this.bpmEl.replaceChildren("Tempo ", h("b", {}, bpm ? (+bpm.toFixed(2)).toString() : "–"),
      Math.abs(this.params.tempo - 1) > 1e-4 ? h("span", { class: "shifted" }, ` → ${this.bpm.toFixed(2)}`) : "",
      t && t.tempo_steady === 0 ? h("span", { class: "tempo-warn", title: "Tempo w tym utworze się zmienia (np. nagranie na żywo). Stała siatka i Sync mogą się z czasem rozjeżdżać — używaj ‹ › do korekty." }, " ⚠ zmienne") : "");
    this.energyEl.replaceChildren("Energia ", h("b", {}, t?.energy ?? "–"));
    this.keyBtn.classList.toggle("on", !!this.params.semitones);
    this.stemBtn.replaceChildren("≋ " + (this.params.stem === "full" ? "Stemy" : STEM_NAMES[this.params.stem]));
    this.stemBtn.classList.toggle("on", this.params.stem !== "full");
    this.loopBtn.classList.toggle("loop-on", this.loop.on);
    this.renderStemBtns?.();
    this.loopSize.textContent = String(this.loop.beats);
    this.syncBtn.classList.toggle("on", this.sync);
    this.playBtn.classList.toggle("on", this.playing);
    this.playBtn.classList.toggle("wait", this.pendingPlay && !this.playing);
    this.playBtn.textContent = this.playing ? "❚❚" : "▶";
    this.emptyEl.classList.toggle("hidden", !!t);
    this.renderPads();
    this.draw();
  }

  // ------------------------------------------------------------------ loading
  async load(track, { semitones = 0, stem = "full", cue } = {}) {
    this.stop();
    this.track = track;
    // forget the previous track's audio right away, so Play / cues during loading can never play it
    this.buffer = null;
    this.stemBuffers = null;
    this.bufTempo = 1;
    this.vari = 1;
    this.pendingPlay = false;
    this.mutes.clear();
    this.params = { stem: track.stems && track.stems !== "none" && track.stems !== "running" ? stem : "full", semitones, tempo: 1 };
    if (this.params.stem.startsWith("mute:")) { // saved mashup: 4 stems with some muted
      if (track.stems === "demucs4") this.params.stem.slice(5).split(",").filter(Boolean).forEach((m) => this.mutes.add(m));
      this.params.stem = track.stems === "demucs4" ? "stems" : "full";
    }
    this.loop.on = false;
    if (cue == null || cue < 0) cue = this.defaultCue();
    this.selCue = cue;
    this.pausedOrig = this.cueTime(cue);
    this.wave = null;
    this.vocals = null;
    this.stemWave = null;
    this.stemBuffers = null;
    this.applyMutes();
    this.zoom = 0;
    this.updateInfo();
    api.get(`/api/tracks/${track.id}/waveform`).then((w) => { if (this.track?.id === track.id) { this.wave = w; this.draw(); } }).catch(() => {});
    this.loadVocals();
    this.loadStemWave();
    this.beatmap = null;
    this.userOffset = 0;
    this.loadBeatmap();
    this.computeSyncTempo();
    await this.loadVariant();
  }

  // ------------------------------------------------------------------ beat map: where the real kicks are
  loadBeatmap() {
    const id = this.track?.id, bpm = this.track?.bpm;
    if (!id || !bpm || this.beatmapLoading === `${id}@${bpm}`) return;
    this.beatmapLoading = `${id}@${bpm}`;
    api.get(`/api/tracks/${id}/beatmap`).then((bm) => { if (this.track?.id === id) this.beatmap = bm; })
      .catch(() => {}).finally(() => { this.beatmapLoading = null; });
  }

  // offset (s) of the real kick from the beat grid at this position
  devAt(pos) {
    const bm = this.beatmap, t = this.track;
    if (!bm || !t?.bpm || !bm.dev?.length) return 0;
    if (Math.abs(bm.bpm - t.bpm) > 1e-4) { this.loadBeatmap(); return 0; } // grid was edited
    const x = (pos - (t.beat_offset || 0)) / (60 / t.bpm);
    const n = bm.dev.length;
    if (x <= 0) return bm.dev[0];
    if (x >= n - 1) return bm.dev[n - 1];
    const i = Math.floor(x), f = x - i;
    return bm.dev[i] * (1 - f) + bm.dev[i + 1] * f;
  }

  // How far (s of this track) this deck must move so its real beats hit the partner's real beats.
  // raw = true ignores the DJ's own offset (used to capture it after a nudge). null = not synced/playing.
  lockError(raw = false, pos = this.position(), lead = 0) {
    const p = this.app.partner(this);
    if (!this.sync || !p.playing || !p.track?.bpm || !this.track?.bpm) return null;
    const posP = p.position() + lead * p.speed;
    const myBeat = 60 / this.track.bpm, pBeat = 60 / p.track.bpm;
    const frac = (x) => x - Math.floor(x);
    let d = frac((posP - (p.track.beat_offset || 0)) / pBeat) - frac((pos - (this.track.beat_offset || 0)) / myBeat);
    d -= Math.round(d);
    const err = d * myBeat + this.devAt(pos) - p.devAt(posP) * (this.speed / p.speed);
    return raw ? err : err - this.userOffset;
  }

  // Sync that follows the music, not only the grid: tiny speed corrections (max 0.4 %, inaudible)
  // keep the kicks of both decks together even where a track's tempo wanders a bit.
  phaseLock() {
    if (this.nudging || !this.playing) return;
    const e = this.lockError();
    let r = 1;
    if (e != null && Math.abs(e) < 0.25) {
      const real = e / ((this.bufTempo || 1) * this.vari); // seconds of real time
      if (Math.abs(real) > 0.0007) r = 1 + Math.max(-0.004, Math.min(0.004, real * 0.8));
      else if (this.rate !== 1 && Math.abs(real) > 0.0002) r = this.rate; // keep going until it is really in phase
    }
    if (Math.abs(r - this.rate) > 1e-6) this.setRate(r);
  }

  // ------------------------------------------------------------------ 4 stems (drums / bass / melody / vocals)
  get has4() { return this.track?.stems === "demucs4"; }

  loadStemWave() {
    const id = this.track?.id;
    if (!this.has4) return;
    api.get(`/api/tracks/${id}/stemwave`).then((w) => { if (this.track?.id === id) { this.stemWave = w; this.draw(); } }).catch(() => {});
  }

  applyMutes() {
    for (const st of STEMS4) this.stemGains[st].gain.setTargetAtTime(this.mutes.has(st) ? 0 : 1, ctx.currentTime, 0.006);
    this.renderStemBtns?.();
    this.draw?.();
  }

  // click: mute / unmute; shift+click: solo
  async toggleMute(st, solo = false) {
    if (!this.track) return;
    if (!this.has4) {
      if (this.app.status?.demucs) {
        if (this.track.stems === "running") return toast("Separacja tego utworu już trwa");
        toast(`Deck ${this.name}: rozdzielam na 4 ścieżki (1–3 min)…`);
        await this.runStems(this.track.stems && this.track.stems !== "none");
        if (!this.has4) return;
      } else return toast("Rozdzielanie na 4 ścieżki wymaga wersji Blendora Pro (Demucs).", { error: true });
    }
    if (solo) {
      const only = STEMS4.every((x) => x === st || this.mutes.has(x)) && !this.mutes.has(st);
      this.mutes = new Set(only ? [] : STEMS4.filter((x) => x !== st));
    } else if (this.mutes.has(st)) this.mutes.delete(st);
    else this.mutes.add(st);
    this.applyMutes();
    if (this.params.stem !== "stems") await this.setStem("stems");
  }

  muteString() { return this.params.stem === "stems" ? "mute:" + [...this.mutes].join(",") : this.params.stem; }

  buildMixer() {
    const ch = this.channel;
    const fmtDb = (v) => (v === 0 ? "0 dB" : `${v > 0 ? "+" : ""}${Math.round(v < 0 ? v * 26 : v * 6)} dB`);
    this.knobs = {};
    this.killBtns = {};
    const band = (b, label) => {
      const k = knob({ label, title: `EQ ${label} (deck ${this.name})`, onChange: (v) => ch.setBand(b, v), format: fmtDb });
      const kill = h("button", { class: "kill", title: `Wytnij ${label} całkowicie (${{ low: "A", mid: "S", high: "D" }[b]}${this.name === "B" ? " z Shift" : ""})`, onclick: () => this.toggleKill(b) }, "kill");
      this.knobs[b] = k;
      this.killBtns[b] = kill;
      return h("div", { class: "band" }, k, kill);
    };
    const filt = knob({ label: "FILTR", title: "W lewo: low-pass (ciemniej), w prawo: high-pass (bez basu)", onChange: (v) => ch.setFilter(v),
      format: (v) => (v === 0 ? "—" : v < 0 ? "LP" : "HP") });
    this.knobs.filter = filt;
    this.stemBtnsEl = h("div", { class: "stem-btns" });
    this.renderStemBtns = () => {
      const t = this.track;
      if (!t || (!this.has4 && !this.app.status?.demucs)) { this.stemBtnsEl.replaceChildren(); return; }
      this.stemBtnsEl.replaceChildren(...STEMS4.map((st) => h("button", {
        class: "stem-btn" + (this.mutes.has(st) ? " muted" : "") + (this.has4 ? "" : " pending"),
        title: this.has4 ? `${STEM4_NAMES[st]}: klik = wycisz / włącz, Shift + klik = solo` : "Kliknij, aby rozdzielić utwór na 4 ścieżki (Demucs)",
        onclick: (e) => this.toggleMute(st, e.shiftKey),
      }, STEM4_NAMES[st])));
      for (const [i, b] of [...this.stemBtnsEl.children].entries()) b.style.setProperty("--c", STEM4_COLORS[STEMS4[i]]);
    };
    return h("div", { class: "deck-mix" },
      h("div", { class: "eq" }, band("high", "HI"), band("mid", "MID"), band("low", "LOW"), h("div", { class: "band" }, filt)),
      this.stemBtnsEl);
  }

  toggleKill(b) {
    const on = !this.channel.state.kill[b];
    this.channel.setKill(b, on);
    this.killBtns[b].classList.toggle("on", on);
  }

  loadVocals() {
    const id = this.track?.id;
    if (!id || !this.track.stems || this.track.stems === "none") return;
    api.get(`/api/tracks/${id}/vocals`).then((v) => { if (this.track?.id === id) { this.vocals = v; this.draw(); } }).catch(() => {});
  }

  // render neighbouring keys in the background so the next key change is instant
  prefetchNeighbours() {
    const { stem, semitones, tempo } = this.params;
    if (stem === "stems") { // three melodic stems: only the nearest keys
      const want = [1, -1].map((d) => semitones + d).filter((s) => s >= -4 && s <= 4);
      for (const st of ["vocals", "bass", "other"]) api.post(`/api/tracks/${this.track.id}/prefetch`, { stem: st, semitones: want, tempo, deck: this.name + st }).catch(() => {});
      return;
    }
    const want = [1, -1, 2, -2].map((d) => semitones + d).filter((s) => s >= -4 && s <= 4);
    api.post(`/api/tracks/${this.track.id}/prefetch`, { stem, semitones: want, tempo, deck: this.name }).catch(() => {});
  }

  async loadVariant() {
    if (!this.track) return;
    const token = ++this.loadToken;
    const track = this.track;
    const { stem, semitones, tempo } = this.params;
    const what = stem !== "full" ? STEM_NAMES[stem] : semitones ? "zmiana tonacji" : Math.abs(tempo - 1) > 1e-4 ? "dopasowanie tempa" : "wczytywanie";
    this.setBusy(`⏳ ${what}…`);
    const get = async (st, semi, tmp) => {
      const r = await fetch(`/api/tracks/${track.id}/audio?stem=${st}&semitones=${semi}&tempo=${tmp.toFixed(5)}`);
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      return ctx.decodeAudioData(await r.arrayBuffer());
    };
    const fetchSet = async (semi, tmp) => {
      if (stem === "stems") {
        const list = await Promise.all(STEMS4.map((st) => get(st, st === "drums" && this.keepDrums ? 0 : semi, tmp)));
        const stemBufs = Object.fromEntries(STEMS4.map((st, i) => [st, list[i]]));
        return { buf: stemBufs.other, stemBufs };
      }
      return { buf: await get(stem, semi, tmp), stemBufs: null };
    };
    const install = ({ buf, stemBufs }, bufTempo, vari) => {
      if (this.playing && this.buffer) {
        // seamless swap: keep the musical position (and so the phase against the other deck)
        const lead = 0.04;
        const posAt = this.position() + lead * this.speed; // measured on the OLD buffer
        this.buffer = buf;
        this.stemBuffers = stemBufs;
        this.bufTempo = bufTempo;
        this.vari = vari;
        this.play(posAt, ctx.currentTime + lead);
      } else {
        this.buffer = buf;
        this.stemBuffers = stemBufs;
        this.bufTempo = bufTempo;
        this.vari = vari;
      }
      if (this.pendingPlay && !this.playing) { this.pendingPlay = false; this.playAligned(this.pausedOrig, "bar"); }
      this.updateInfo();
    };
    try {
      const final = fetchSet(semitones, tempo);
      // A fresh track that needs time-stretching: play the original right away at a matched speed
      // (like a turntable, key moves a little) and switch to the stretched version when it is ready.
      if (!this.buffer && (Math.abs(tempo - 1) > 1e-4 || semitones)) {
        const quick = await Promise.race([fetchSet(0, 1), final.then(() => null)]).catch(() => null);
        if (token !== this.loadToken) return;
        if (quick && !this.buffer) {
          install(quick, 1, tempo);
          this.setBusy(`⏳ ${what}… (gra już, tonacja jeszcze bez korekty)`);
        }
      }
      const set = await final;
      if (token !== this.loadToken) return;
      install(set, tempo, 1);
      this.prefetchNeighbours();
      this.setBusy("");
    } catch (err) {
      if (token === this.loadToken) { this.setBusy(""); this.pendingPlay = false; toast(`Deck ${this.name}: ${err.message}`, { error: true }); }
    }
    this.updateInfo();
    this.app.onDeckChange(this);
  }

  // ------------------------------------------------------------------ transport
  toOrig(b) { return b * (this.bufTempo || 1); }
  toBuf(o) { return o / (this.bufTempo || 1); }

  position() {
    if (!this.playing || !this.buffer) return this.pausedOrig;
    let b = this.startBuf + Math.max(0, ctx.currentTime - this.startCtx) * this.rate * this.vari;
    if (this.source?.loop) {
      const ls = this.source.loopStart, le = this.source.loopEnd;
      if (b >= le && le > ls) b = ls + ((b - ls) % (le - ls));
    }
    return this.toOrig(Math.min(b, this.buffer.duration));
  }

  // seconds of original track time per second of real time
  get speed() { return (this.bufTempo || 1) * this.vari * this.rate; }

  play(fromOrig = this.position(), when = 0) {
    if (!this.buffer) return;
    if (ctx.state === "suspended") ctx.resume();
    const at = Math.max(ctx.currentTime, when || ctx.currentTime);
    const old = this.source;
    if (old) { // let the old source run until the new one takes over (no gap, no double audio)
      old.onended = null;
      try { old.stop(at); } catch {}
      setTimeout(() => old.disconnect(), (at - ctx.currentTime) * 1000 + 300);
    }
    let src;
    if (this.stemBuffers) src = new SourceGroup(ctx, STEMS4.map((st) => ({ buffer: this.stemBuffers[st], dest: this.stemGains[st] })));
    else { src = ctx.createBufferSource(); src.buffer = this.buffer; src.connect(this.channel.input); }
    src.playbackRate.value = this.rate * this.vari;
    const startB = Math.max(0, Math.min(this.toBuf(fromOrig), this.buffer.duration - 0.01));
    if (this.loop.on) this.applyLoop(src);
    src.start(at, startB);
    src.onended = () => { if (this.source === src && !src.loop) { this.playing = false; this.pausedOrig = 0; this.updateInfo(); } };
    this.source = src;
    this.startCtx = at;
    this.startBuf = startB;
    this.playing = true;
    this.updateInfo();
  }

  // pitch-bend nudge: temporarily speed up / slow down to fix the phase by ear
  setRate(r) {
    if (this.playing && this.source) {
      let b = this.startBuf + Math.max(0, ctx.currentTime - this.startCtx) * this.rate * this.vari;
      const ls = this.source.loopStart, le = this.source.loopEnd;
      if (this.source.loop && b >= le && le > ls) b = ls + ((b - ls) % (le - ls));
      this.startBuf = b;
      this.startCtx = Math.max(ctx.currentTime, this.startCtx);
      this.source.playbackRate.setValueAtTime(r * this.vari, ctx.currentTime);
    }
    this.rate = r;
  }

  killSource() {
    if (this.source) { this.source.onended = null; try { this.source.stop(); } catch {} this.source.disconnect(); this.source = null; }
  }

  pause() {
    this.pausedOrig = this.position();
    this.killSource();
    this.rate = 1;
    this.playing = false;
    this.updateInfo();
  }

  stop() { this.killSource(); this.playing = false; this.rate = 1; }

  toggle() {
    if (!this.track) return;
    if (!this.buffer) { // still loading: start as soon as the audio is there
      this.pendingPlay = !this.pendingPlay;
      this.updateInfo();
      return;
    }
    if (this.playing) this.pause();
    else this.playAligned(this.pausedOrig, "bar");
  }

  seek(orig, unit = "beat") {
    orig = Math.max(0, Math.min(orig, this.track?.duration || 0));
    if (this.playing) this.playAligned(orig, unit);
    else { this.pausedOrig = orig; this.draw(); }
  }

  // Start (or jump) right away, without waiting for the next bar: the position is moved by less than half
  // a bar/beat so that this deck keeps the phase. With Sync the reference is the other deck (bars line up,
  // so a cue on a drop lands exactly on the partner's next downbeat); playing alone it is this deck's own
  // beat before the jump. Like Traktor's quantize, but instant.
  alignedPos(orig, unit = "bar", lead = 0.03) {
    const p = this.app.partner(this);
    if (!this.track?.bpm) return orig;
    const synced = (this.sync || p.sync) && p.playing && p.track?.bpm;
    const ref = synced ? p : this.playing ? this : null;
    if (!ref) return orig;
    if (!synced) unit = "beat";
    const beats = unit === "bar" ? 4 : 1;
    const anchor = (t) => (unit === "bar" ? t.first_downbeat ?? t.beat_offset : t.beat_offset) || 0;
    const refLen = (60 * beats) / ref.track.bpm;
    const refPos = ref.position() + lead * ref.speed;
    const myLen = (60 * beats) / this.track.bpm;
    const frac = (x) => x - Math.floor(x);
    let d = frac((refPos - anchor(ref.track)) / refLen) - frac((orig - anchor(this.track)) / myLen);
    d -= Math.round(d); // nearest: at most half a bar/beat earlier or later
    let pos = orig + d * myLen;
    if (pos < 0) pos += myLen;
    if (synced && this.sync) { // also line up the real kicks (beat map) and the DJ's own offset
      const e = this.lockError(false, pos, lead);
      if (e != null && Math.abs(e) < 0.1) pos += e;
    }
    return Math.max(0, pos);
  }

  playAligned(orig, unit = "bar") {
    const lead = 0.03;
    this.play(this.alignedPos(orig, unit, lead), ctx.currentTime + lead);
  }

  cues() { return this.track?.cues || []; }
  cueTime(i) { const c = this.cues(); return c.length ? c[Math.min(i, c.length - 1)] : 0; }

  jumpCue(i) {
    if (!this.track || i >= this.cues().length) return;
    this.selCue = i;
    this.renderPads();
    const t = this.cueTime(i);
    if (this.loop.on) this.setLoopAt(t);
    if (this.playing) this.playAligned(t, "bar");
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
        content.append(h("div", { class: "pop-row" }, ["full", "vocals", "instrumental", ...(this.has4 ? ["stems"] : [])].map((s) =>
          h("button", { class: "btn" + (this.params.stem === s ? " accent" : ""), onclick: () => { this.setStem(s); closePopover(); } }, STEM_NAMES[s]))));
        content.append(h("p", { class: "muted", style: { fontSize: "11px", margin: "8px 0 0" } },
          t.stems === "demucs4" ? "Separacja: Demucs, 4 ścieżki (perkusja, bas, melodia, wokal). Wyciszasz je przyciskami pod deckiem." : t.stems === "demucs" ? "Separacja: Demucs, 2 ścieżki. Rozdziel ponownie, aby dostać 4 ścieżki." : "Separacja: szybki podgląd. Zainstaluj Demucs, aby uzyskać wysoką jakość, i rozdziel ponownie.",
          t.vocal_ratio != null ? ` Wokal w ~${Math.round(t.vocal_ratio * 100)}% utworu.` : ""));
        if (t.stems !== "demucs4" && this.app.status?.demucs) content.append(h("button", { class: "btn", style: { marginTop: "6px" }, onclick: () => this.runStems(true) }, "Rozdziel ponownie (Demucs, 4 ścieżki)"));
        if (this.has4) content.append(h("button", { class: "btn", style: { marginTop: "6px" }, title: "Plik .stem.mp4 do decków stemowych w Traktorze", onclick: () => { closePopover(); this.app.stemFiles([t.id]); } }, "🎛 Zapisz plik Stem dla Traktora"));
        if (this.has4) content.append(h("label", { class: "pop-row", style: { marginTop: "8px", fontSize: "12px" } },
          h("input", { type: "checkbox", checked: this.keepDrums, onchange: (e) => { this.keepDrums = e.target.checked; if (this.params.stem === "stems" && this.params.semitones) this.loadVariant(); } }),
          "Przy zmianie tonacji nie ruszaj perkusji (brzmi naturalniej)"));
      } else {
        const prog = h("div", { class: "progress" }, h("div"));
        content.append(
          h("h4", {}, "Ten utwór nie ma jeszcze stemów"),
          h("button", { class: "btn accent wide", onclick: (e) => { e.currentTarget.disabled = true; this.runStems(false, prog.firstChild); } }, this.app.status?.demucs ? "Rozdziel: perkusja / bas / melodia / wokal" : "Rozdziel: wokal / instrumental"),
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
      if (this.track?.id === t.id) { this.track = fresh; this.loadVocals(); this.loadStemWave(); }
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

  // ------------------------------------------------------------------ cue pads & section names
  // Name each cue from the energy of the section it starts: Intro, Build, Break, Drop, Main, Outro.
  cueInfo() {
    const t = this.track;
    if (!t) return [];
    const cues = this.cues(), segs = t.segments || [], E = t.energy || 5;
    const energyAt = (x) => {
      const sg = segs.find((g) => x >= g.start - 0.05 && x < g.end - 0.05) || segs[segs.length - 1];
      return sg ? sg.energy : E;
    };
    const es = cues.map(energyAt);
    const KIND = { intro: "Intro", start: "Start", main: "Main", build: "Build", break: "Break", drop: "Drop", outro: "Outro" };
    return cues.map((c, i) => {
      const e = es[i], pe = es[i - 1], ne = es[i + 1];
      // section type found by the analysis (kick out = break, kick back = drop)
      const seg = segs.find((g) => g.kind && Math.abs(g.start - c) < 0.06);
      if (seg && KIND[seg.kind]) return { time: c, energy: e, label: KIND[seg.kind] };
      let label = "Main";
      if (i === 0) label = e < E ? "Intro" : "Start";
      else if (i === cues.length - 1 && e < E && (t.duration || 0) - c < 90) label = "Outro";
      else if (e <= E - 2) label = "Break";
      else if (pe != null && e >= E && pe <= e - 2) label = "Drop";
      else if (ne != null && ne >= e + 2) label = "Build";
      return { time: c, energy: e, label };
    });
  }

  // Where a mashup test should start by default: the first drop (or the first full-energy part).
  defaultCue() {
    const info = this.cueInfo();
    let i = info.findIndex((c) => c.label === "Drop");
    if (i < 0) i = info.findIndex((c) => c.energy >= (this.track?.energy || 5) && c.label !== "Intro" && c.label !== "Start");
    return Math.max(0, i);
  }

  renderPads() {
    if (!this.padsEl) return;
    const info = this.cueInfo();
    const pads = [];
    for (let i = 0; i < 8; i++) {
      const c = info[i];
      if (!c) {
        pads.push(h("button", { class: "pad empty", title: "Dodaj cue w miejscu głowicy (najbliższy beat)", disabled: !this.track, onclick: () => this.addCueHere() }, "+"));
        continue;
      }
      const b = h("button", {
        class: "pad" + (i === this.selCue ? " sel" : "") + ` lab-${c.label.toLowerCase()}`,
        title: `Cue ${i + 1}: ${c.label}, energia ${c.energy}, ${fmtTime(c.time)}\nKlik: skocz (w rytm drugiego decku). Prawy klik: przesuń / usuń.`,
        onpointerdown: (e) => { if (e.button === 0) this.jumpCue(i); },
        oncontextmenu: (e) => { e.preventDefault(); this.selCue = i; this.renderPads(); this.draw(); this.gridPopover(b); },
      }, h("b", {}, String(i + 1)), h("span", {}, c.label), h("small", {}, fmtTime(c.time)));
      b.style.setProperty("--e", c.energy / 10);
      pads.push(b);
    }
    this.padsEl.replaceChildren(...pads);
  }

  // ------------------------------------------------------------------ manual beat grid / cue editing
  async saveGrid(fields, msg, { undo = true } = {}) {
    const t = this.track;
    if (undo) {
      const old = {};
      for (const k of Object.keys(fields)) old[k] = Array.isArray(t[k]) ? [...t[k]] : t[k];
      this.app.pushUndo({ id: t.id, fields: old, label: msg || "Zmiana siatki" });
    }
    try {
      const fresh = await api.patch(`/api/tracks/${t.id}`, fields);
      this.app.updateTrack(fresh);
      this.track = { ...fresh };
      if (this.computeSyncTempo()) await this.loadVariant();
      const p = this.app.partner(this);
      if (p.track) await p.refreshSync();
      this.app.onDeckChange(this);
      this.updateInfo();
      if (msg) toast(msg);
    } catch (err) { toast(err.message, { error: true }); }
  }

  // shift the whole grid (beats, downbeat and cues) by dt seconds
  shiftGrid(dt, msg) {
    const t = this.track;
    return this.saveGrid({
      beat_offset: Math.max(0, (t.beat_offset || 0) + dt),
      first_downbeat: Math.max(0, (t.first_downbeat || 0) + dt),
      cues: (t.cues || []).map((c) => Math.max(0, c + dt)),
      // keep the section names attached to their (moved) cues
      segments: (t.segments || []).map((g) => ({ ...g, start: Math.round((g.start + dt) * 1000) / 1000, end: Math.round((g.end + dt) * 1000) / 1000 })),
    }, msg);
  }

  nearestBeat(pos) {
    const b = this.beatLen(), off = this.track.beat_offset || 0;
    return off + Math.round((pos - off) / b) * b;
  }

  gridPopover(anchor) {
    if (!this.track) return;
    const t = this.track;
    const bpmIn = h("input", { class: "field", type: "number", step: "0.01", min: 40, max: 250, value: (+t.bpm).toFixed(2), style: { width: "90px" } });
    const setBpm = (v) => { closePopover(); this.saveGrid({ bpm: Math.round(v * 1000) / 1000 }, `Deck ${this.name}: BPM ${(+v).toFixed(2)}`); };
    const pos = () => this.position();
    popover(anchor, [
      h("h4", {}, "Tempo"),
      h("div", { class: "pop-row" }, bpmIn,
        h("button", { class: "btn", onclick: () => setBpm(+bpmIn.value) }, "Zapisz"),
        h("button", { class: "btn", title: "Podwój (np. 87 → 174)", onclick: () => setBpm(t.bpm * 2) }, "×2"),
        h("button", { class: "btn", title: "Połowa (np. 174 → 87)", onclick: () => setBpm(t.bpm / 2) }, "÷2")),
      h("h4", { style: { marginTop: "12px" } }, "Siatka (zatrzymaj na uderzeniu stopy)"),
      h("div", { class: "pop-row" },
        h("button", { class: "btn", title: "Przesuń siatkę tak, by beat był dokładnie w miejscu głowicy", onclick: () => { closePopover(); this.shiftGrid(pos() - this.nearestBeat(pos())); } }, "Beat tutaj"),
        h("button", { class: "btn", title: "Ten beat to „raz” — pierwszy beat taktu", onclick: () => {
          closePopover();
          const bar = 4 * this.beatLen();
          const nb = this.nearestBeat(pos());
          const fd = ((nb % bar) + bar) % bar;
          this.saveGrid({ first_downbeat: fd, cues: (t.cues || []).map((c) => Math.max(0, fd + Math.round((c - fd) / bar) * bar)) }, "Pierwszy beat taktu ustawiony");
        } }, "„Raz” tutaj"),
        h("button", { class: "btn", title: "Siatka 5 ms wcześniej", onclick: () => this.shiftGrid(-0.005) }, "◀ 5 ms"),
        h("button", { class: "btn", title: "Siatka 5 ms później", onclick: () => this.shiftGrid(0.005) }, "5 ms ▶")),
      h("div", { class: "pop-row" },
        h("button", { class: "btn" + (this.gridEdit ? " accent" : ""), title: "Przeciągaj siatkę myszą po waveformie (albo przytrzymaj Shift). Kółko myszy = 1 ms.", onclick: () => {
          closePopover();
          this.gridEdit = !this.gridEdit;
          this.waveBox.classList.toggle("grid-edit", this.gridEdit);
          if (this.gridEdit && this.zoom < 3) this.setZoom(3); else this.draw();
          toast(this.gridEdit ? `Deck ${this.name}: przeciągnij waveform, żeby przesunąć siatkę (kółko = 1 ms). Kliknij ponownie, aby wyjść.` : "Edycja siatki zakończona");
        } }, this.gridEdit ? "✓ Koniec przesuwania siatki" : "✥ Przesuwaj siatkę myszą")),
      h("h4", { style: { marginTop: "12px" } }, "Cue pointy"),
      h("div", { class: "pop-row" },
        h("button", { class: "btn", title: "Dodaj cue w najbliższym beacie (C)", onclick: () => { closePopover(); this.addCueHere(); } }, "+ Cue tutaj"),
        h("button", { class: "btn", title: `Przesuń Cue ${this.selCue + 1} w miejsce głowicy`, onclick: () => { closePopover(); this.moveCueHere(); } }, `Przesuń Cue ${this.selCue + 1} tutaj`),
        h("button", { class: "btn danger", onclick: () => { closePopover(); this.deleteCue(this.selCue); } }, `Usuń Cue ${this.selCue + 1}`)),
      h("p", { class: "muted", style: { fontSize: "11px", maxWidth: "330px", margin: "8px 0 0" } }, "Zmiany zapisują się w bibliotece. Prawy klik na etykiecie cue też otwiera to menu."),
    ]);
  }

  addCueHere() {
    const c = [...this.cues()];
    const at = this.nearestBeat(this.position());
    if (c.some((x) => Math.abs(x - at) < 0.05)) return;
    if (c.length >= 8) { toast("Jest już 8 cue. Przesuń albo usuń któryś.", { error: true }); return; }
    c.push(at); c.sort((a, b) => a - b);
    this.selCue = c.indexOf(at);
    this.saveGrid({ cues: c }, `Dodano Cue ${this.selCue + 1}`);
  }

  moveCueHere() {
    const c = [...this.cues()];
    if (!c.length) return this.addCueHere();
    const at = this.nearestBeat(this.position());
    c[this.selCue] = at; c.sort((a, b) => a - b);
    this.selCue = c.indexOf(at);
    this.saveGrid({ cues: c }, `Cue ${this.selCue + 1} przesunięty`);
  }

  deleteCue(i) {
    const c = [...this.cues()];
    if (i >= c.length) return;
    c.splice(i, 1);
    this.selCue = Math.max(0, Math.min(this.selCue, c.length - 1));
    return this.saveGrid({ cues: c }, "Cue usunięty");
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

  // Traktor-style mouse handling: drag the waveform to scroll through the track smoothly,
  // Shift+drag (or "Przesuwaj siatkę" mode) drags the beat grid with cues, click = seek.
  onWaveDown(e) {
    if (!this.track || e.button !== 0 || e.target.closest(".wave-zoom")) return;
    const rect = this.canvas.getBoundingClientRect();
    const [v0, v1] = this.view();
    const secPerPx = (v1 - v0) / rect.width;
    const grid = e.shiftKey || this.gridEdit;
    const t = this.track;
    const orig = { beat_offset: t.beat_offset || 0, first_downbeat: t.first_downbeat || 0, cues: [...(t.cues || [])], segments: t.segments };
    const pos0 = this.position(), x0 = e.clientX, wasPlaying = this.playing;
    let dragging = false, dt = 0;
    this.waveBox.setPointerCapture(e.pointerId);
    const move = (ev) => {
      const dx = ev.clientX - x0;
      if (!dragging && Math.abs(dx) < 4) return;
      if (!dragging) {
        dragging = true;
        this.gridDragging = grid;
        if (!grid && wasPlaying) this.pause();
      }
      if (grid) {
        dt = dx * secPerPx;
        t.beat_offset = Math.max(0, orig.beat_offset + dt);
        t.first_downbeat = Math.max(0, orig.first_downbeat + dt);
        t.cues = orig.cues.map((c) => Math.max(0, c + dt));
        t.segments = (orig.segments || []).map((g) => ({ ...g, start: g.start + dt, end: g.end + dt }));
        this.renderPads();
      } else {
        // zoomed: the waveform moves under the playhead; overview: the playhead follows the mouse
        this.pausedOrig = Math.max(0, Math.min(t.duration || 0, pos0 + (this.zoom ? -dx : dx) * secPerPx));
      }
      this.draw();
    };
    const up = (ev) => {
      this.waveBox.removeEventListener("pointermove", move);
      this.waveBox.removeEventListener("pointerup", up);
      this.waveBox.removeEventListener("pointercancel", up);
      this.gridDragging = false;
      if (!dragging) { this.onWaveClick(ev); return; }
      if (grid) {
        Object.assign(t, orig);
        if (Math.abs(dt) > 0.0005) this.shiftGrid(dt, `Siatka przesunięta o ${(dt * 1000).toFixed(0)} ms`);
        else this.draw();
      } else if (wasPlaying) this.playAligned(this.pausedOrig, "beat");
    };
    this.waveBox.addEventListener("pointermove", move);
    this.waveBox.addEventListener("pointerup", up);
    this.waveBox.addEventListener("pointercancel", up);
  }

  // wheel: zoom; Shift+wheel: nudge the grid by 1 ms; Ctrl/Alt+wheel: scroll by one beat
  onWaveWheel(e) {
    if (!this.track) return;
    e.preventDefault();
    const dir = e.deltaY < 0 ? 1 : -1;
    if (e.shiftKey || this.gridEdit) {
      this.wheelShift = (this.wheelShift || 0) + dir * 0.001;
      const t = this.track;
      t.beat_offset = (t.beat_offset || 0) + dir * 0.001;
      t.first_downbeat = (t.first_downbeat || 0) + dir * 0.001;
      this.draw();
      clearTimeout(this.wheelTimer);
      this.wheelTimer = setTimeout(() => {
        const d = this.wheelShift;
        this.wheelShift = 0;
        t.beat_offset -= d; t.first_downbeat -= d;
        this.shiftGrid(d, `Siatka przesunięta o ${(d * 1000).toFixed(0)} ms`);
      }, 500);
    } else if (e.ctrlKey || e.altKey) {
      this.seek(this.position() - dir * this.beatLen());
    } else this.setZoom(this.zoom + dir);
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
    if ((this.zoom || this.gridDragging || this.gridEdit) && t.bpm) {
      const beat = 60 / t.bpm, fd = t.first_downbeat || 0;
      let k = Math.ceil((v0 - fd) / beat);
      for (let bt = fd + k * beat; bt < v1; bt += beat, k++) {
        g.fillStyle = k % 4 === 0 ? "rgba(255,255,255,.16)" : "rgba(255,255,255,.05)";
        g.fillRect(Math.round(X(bt)), 0, 1, H);
      }
    }
    // waveform: four stem lanes in stem mode (muted lanes greyed out), otherwise the full mix
    const w = this.wave, sw = this.stemWave;
    if (sw && this.params.stem === "stems") {
      const top = 18 * dpr, laneH = (H - top - 6 * dpr) / 4, pps = sw.pps;
      STEMS4.forEach((st, li) => {
        const arr = sw[st] || [], n = arr.length, muted = this.mutes.has(st);
        const cy = top + laneH * (li + 0.5), a2 = laneH / 2 - 1;
        const col = hexRgb(STEM4_COLORS[st]);
        for (let x = 0; x < W; x++) {
          const ta = v0 + (x / W) * span, tb = v0 + ((x + 1) / W) * span;
          let i0 = Math.floor(ta * pps), i1 = Math.max(i0 + 1, Math.floor(tb * pps));
          if (i0 >= n) break;
          i1 = Math.min(i1, n);
          let pk = 0;
          for (let i = i0; i < i1; i++) if (arr[i] > pk) pk = arr[i];
          const hh = (pk / 255) * a2;
          g.fillStyle = muted ? "rgba(120,120,130,.25)" : `rgba(${col[0]},${col[1]},${col[2]},${ta < pos ? 0.45 : 0.9})`;
          g.fillRect(x, cy - hh, 1, hh * 2 || 1);
        }
        g.fillStyle = muted ? "rgba(255,255,255,.3)" : `rgb(${col[0]},${col[1]},${col[2]})`;
        g.font = `${9 * dpr}px ${css("--font") || "sans-serif"}`;
        g.textBaseline = "middle";
        g.fillText(STEM4_NAMES[st] + (muted ? " (wycisz.)" : ""), 4 * dpr, cy - a2 + 6 * dpr);
      });
    } else if (w) {
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
    const info = this.cueInfo();
    this.cues().forEach((cue, i) => {
      const x = X(cue);
      if (x < -50 || x > W) return;
      const sel = i === this.selCue;
      g.fillStyle = sel ? deckColor : "rgba(255,255,255,.75)";
      g.fillRect(x, 0, 1.5 * dpr, H);
      const name = info[i]?.label || "";
      const label = span > 400 && W / dpr < 520 ? String(i + 1) : (W / dpr) * (60 / span) > 40 ? `${i + 1} ${name}` : `Cue ${i + 1}`;
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
