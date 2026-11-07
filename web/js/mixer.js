// Per-deck channel strip: stem gains -> 3-band isolator EQ (with kills) -> one-knob filter.
// Plus SourceGroup: several AudioBufferSourceNodes (stems) that behave like one source.
import { h } from "./ui.js";

const BUTTER_Q_DB = -3.0103; // Web Audio lowpass/highpass Q is in dB; Butterworth = 1/sqrt(2)
export const STEMS4 = ["drums", "bass", "other", "vocals"];
export const STEM4_NAMES = { drums: "Perkusja", bass: "Bas", other: "Melodia", vocals: "Wokal" };
export const STEM4_COLORS = { drums: "#ffb547", bass: "#ff5d73", other: "#2de0a7", vocals: "#8b7bff" };

function lr4(ctx, type, freq) {
  // Linkwitz-Riley 24 dB/oct = two cascaded Butterworth biquads; LR bands sum back flat
  const a = ctx.createBiquadFilter(), b = ctx.createBiquadFilter();
  for (const f of [a, b]) { f.type = type; f.frequency.value = freq; f.Q.value = BUTTER_Q_DB; }
  a.connect(b);
  return { input: a, output: b };
}

const dbToGain = (db) => Math.pow(10, db / 20);

export class Channel {
  constructor(ctx, out) {
    this.ctx = ctx;
    this.input = ctx.createGain();
    // isolator: low < 250 Hz < mid < 3 kHz < high
    const LO = 250, HI = 3000;
    const low = lr4(ctx, "lowpass", LO);
    const lowAp = [lr4(ctx, "lowpass", HI), lr4(ctx, "highpass", HI)]; // keeps the low band in phase with the others
    const midHp = lr4(ctx, "highpass", LO), midLp = lr4(ctx, "lowpass", HI);
    const high = lr4(ctx, "highpass", HI);
    this.gains = { low: ctx.createGain(), mid: ctx.createGain(), high: ctx.createGain() };
    const sum = ctx.createGain();
    this.input.connect(low.input);
    for (const ap of lowAp) { low.output.connect(ap.input); ap.output.connect(this.gains.low); }
    this.input.connect(midHp.input); midHp.output.connect(midLp.input); midLp.output.connect(this.gains.mid);
    this.input.connect(high.input); high.output.connect(this.gains.high);
    for (const g of Object.values(this.gains)) g.connect(sum);
    // filter knob: low-pass to the left, high-pass to the right
    this.lp = ctx.createBiquadFilter(); this.lp.type = "lowpass"; this.lp.frequency.value = 22000; this.lp.Q.value = BUTTER_Q_DB;
    this.hp = ctx.createBiquadFilter(); this.hp.type = "highpass"; this.hp.frequency.value = 10; this.hp.Q.value = BUTTER_Q_DB;
    sum.connect(this.lp); this.lp.connect(this.hp); this.hp.connect(out);
    this.state = { low: 0, mid: 0, high: 0, filter: 0, kill: { low: false, mid: false, high: false } };
  }

  // v in [-1, 1]: -1 = -26 dB, 0 = flat, +1 = +6 dB
  setBand(band, v) {
    this.state[band] = v;
    this.applyBand(band);
  }

  setKill(band, on) {
    this.state.kill[band] = on;
    this.applyBand(band);
  }

  applyBand(band) {
    const v = this.state[band];
    const g = this.state.kill[band] ? 0 : dbToGain(v < 0 ? v * 26 : v * 6);
    this.gains[band].gain.setTargetAtTime(g, this.ctx.currentTime, 0.008);
  }

  // v in [-1, 1]: left = low-pass down to 80 Hz, right = high-pass up to 6 kHz
  setFilter(v) {
    this.state.filter = v;
    const t = this.ctx.currentTime, a = Math.abs(v);
    const lpF = v < -0.02 ? 22000 * Math.pow(80 / 22000, a) : 22000;
    const hpF = v > 0.02 ? 20 * Math.pow(6000 / 20, a) : 10;
    const q = a > 0.02 ? 4 : BUTTER_Q_DB; // a little resonance when the filter is in use
    this.lp.frequency.setTargetAtTime(lpF, t, 0.01);
    this.hp.frequency.setTargetAtTime(hpF, t, 0.01);
    this.lp.Q.setTargetAtTime(v < -0.02 ? q : BUTTER_Q_DB, t, 0.01);
    this.hp.Q.setTargetAtTime(v > 0.02 ? q : BUTTER_Q_DB, t, 0.01);
  }

  reset() {
    for (const b of ["low", "mid", "high"]) { this.state.kill[b] = false; this.setBand(b, 0); }
    this.setFilter(0);
  }
}

// A rotary knob: drag up/down (or wheel) to turn, double click to centre.
export function knob({ label, value = 0, min = -1, max = 1, title = "", onChange, format }) {
  const ind = h("i");
  const val = h("small");
  const el = h("div", { class: "knob", title: title + "\nPrzeciągnij w górę/dół, kółko myszy, dwuklik = środek" }, h("div", { class: "knob-dial" }, ind), h("span", {}, label), val);
  let v = value;
  const set = (x, emit = true) => {
    v = Math.max(min, Math.min(max, x));
    if (Math.abs(v) < 0.025) v = 0; // detent in the middle
    const ang = -135 + ((v - min) / (max - min)) * 270;
    ind.style.transform = `rotate(${ang}deg)`;
    el.classList.toggle("off-centre", v !== 0);
    val.textContent = format ? format(v) : "";
    if (emit) onChange?.(v);
  };
  el.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    el.setPointerCapture(e.pointerId);
    const y0 = e.clientY, v0 = v;
    const move = (ev) => set(v0 + ((y0 - ev.clientY) / 150) * (max - min) * (ev.shiftKey ? 0.25 : 1));
    const up = () => { el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up); };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
  });
  el.addEventListener("wheel", (e) => { e.preventDefault(); set(v + (e.deltaY < 0 ? 1 : -1) * (max - min) / 40); }, { passive: false });
  el.addEventListener("dblclick", () => set(0));
  el.setValue = (x) => set(x, false);
  set(v, false);
  return el;
}

// Several buffers (stems) started together, behaving like one AudioBufferSourceNode.
export class SourceGroup {
  constructor(ctx, parts) { // parts: [{ buffer, dest }]
    this.nodes = parts.map(({ buffer, dest }) => {
      const s = ctx.createBufferSource();
      s.buffer = buffer;
      s.connect(dest);
      return s;
    });
    this.buffer = this.nodes[0].buffer;
    const nodes = this.nodes;
    this.playbackRate = {
      set value(r) { nodes.forEach((n) => (n.playbackRate.value = r)); },
      get value() { return nodes[0].playbackRate.value; },
      setValueAtTime(r, t) { nodes.forEach((n) => n.playbackRate.setValueAtTime(r, t)); },
    };
  }
  get loop() { return this.nodes[0].loop; }
  set loop(x) { this.nodes.forEach((n) => (n.loop = x)); }
  get loopStart() { return this.nodes[0].loopStart; }
  set loopStart(x) { this.nodes.forEach((n) => (n.loopStart = x)); }
  get loopEnd() { return this.nodes[0].loopEnd; }
  set loopEnd(x) { this.nodes.forEach((n) => (n.loopEnd = x)); }
  set onended(fn) { this.nodes[0].onended = fn; }
  get onended() { return this.nodes[0].onended; }
  start(at, off) { this.nodes.forEach((n) => n.start(at, off)); }
  stop(at) { this.nodes.forEach((n) => { try { n.stop(at); } catch {} }); }
  disconnect() { this.nodes.forEach((n) => n.disconnect()); }
}
