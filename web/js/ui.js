// Small UI helpers: DOM builder, popovers, modals, toasts, formatting.

export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

export const $ = (s, root = document) => root.querySelector(s);
export const $$ = (s, root = document) => [...root.querySelectorAll(s)];

let openPop = null;
export function closePopover() {
  if (openPop) { openPop.el.remove(); openPop.onClose?.(); openPop = null; }
}
export function popover(anchor, content, { onClose, align = "left" } = {}) {
  if (openPop && openPop.anchor === anchor) { closePopover(); return null; }
  closePopover();
  const el = h("div", { class: "popover" }, content);
  document.body.append(el);
  const r = anchor.getBoundingClientRect();
  const w = el.offsetWidth, hgt = el.offsetHeight;
  let left = align === "right" ? r.right - w : r.left;
  left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
  let top = r.bottom + 6;
  if (top + hgt > window.innerHeight - 8) top = Math.max(8, r.top - hgt - 6);
  el.style.left = left + "px";
  el.style.top = top + "px";
  openPop = { el, anchor, onClose };
  return el;
}
document.addEventListener("mousedown", (e) => {
  if (openPop && !openPop.el.contains(e.target) && !openPop.anchor.contains(e.target)) closePopover();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closePopover(); closeModal(); } });

let openModalEl = null;
export function closeModal() { if (openModalEl) { openModalEl.remove(); openModalEl = null; } }
export function modal(content) {
  closeModal();
  const back = h("div", { class: "modal-back", onmousedown: (e) => { if (e.target === back) closeModal(); } }, h("div", { class: "modal" }, content));
  document.body.append(back);
  openModalEl = back;
  back.querySelector("input,select,textarea")?.focus();
  return back;
}

export function prompt(title, value = "", label = "") {
  return new Promise((resolve) => {
    const input = h("input", { class: "field", value });
    const done = (v) => { closeModal(); resolve(v); };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") done(input.value.trim()); });
    modal([
      h("h3", {}, title), label ? h("p", { class: "sub" }, label) : null, input,
      h("div", { class: "actions" },
        h("button", { class: "btn", onclick: () => done(null) }, "Anuluj"),
        h("button", { class: "btn primary", onclick: () => done(input.value.trim()) }, "OK")),
    ]);
    input.select();
  });
}

export function confirmBox(title, text) {
  return new Promise((resolve) => {
    const done = (v) => { closeModal(); resolve(v); };
    modal([h("h3", {}, title), h("p", { class: "sub" }, text),
      h("div", { class: "actions" },
        h("button", { class: "btn", onclick: () => done(false) }, "Anuluj"),
        h("button", { class: "btn primary", onclick: () => done(true) }, "Tak"))]);
  });
}

export function toast(msg, { error = false, ms = 4000, action } = {}) {
  const el = h("div", { class: "toast" + (error ? " err" : "") }, msg);
  if (action) el.append(" ", h("a", { onclick: action.fn }, action.label));
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), ms);
}

export function fmtTime(s) {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
}

// Camelot colours: hue walks around the wheel, minor (A) slightly darker than major (B).
export function keyColor(code) {
  if (!code) return "#3a4155";
  const n = parseInt(code, 10);
  const minor = code.endsWith("A");
  const hue = (n * 30 + 150) % 360;
  return `hsl(${hue} ${minor ? 70 : 75}% ${minor ? 70 : 62}%)`;
}

export function keyBadge(code, extraClass = "") {
  return h("span", { class: "key " + extraClass, style: { background: keyColor(code) } }, code || "–");
}

export function transpose(code, semis) {
  if (!code) return code;
  const n = parseInt(code, 10), l = code.slice(-1);
  return `${(((n - 1 + 7 * semis) % 12) + 12) % 12 + 1}${l}`;
}
