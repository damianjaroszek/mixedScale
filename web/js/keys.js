// Key check view: compare detected keys with keys already in file tags (Mixed In Key, Rekordbox...),
// learn from the user's library, and fix single tracks quickly.
import { api } from "./api.js";
import { h, $, toast, keyBadge, popover, closePopover, confirmBox } from "./ui.js";

const REL = { same: "zgodne", relative: "równoległa dur/moll", fifth: "sąsiednia (kwinta)", parallel: "ta sama tonika", other: "inna" };

function conf(c) { return h("span", { class: "conf", title: `Pewność ${Math.round((c || 0) * 100)}%` }, h("i", { style: { width: `${Math.round((c || 0) * 100)}%` } })); }

async function setKey(app, id, key) {
  app.updateTrack(await api.patch(`/api/tracks/${id}`, { key }));
}

export function keyPopover(app, anchor, id) {
  const t = app.byId.get(id);
  if (!t) return;
  const choice = (label, code) => code ? h("div", { class: "pop-row" }, h("span", { class: "muted", style: { width: "110px" } }, label), keyBadge(code), app.keyInfo(code).name,
    code !== t.key ? h("button", { class: "btn", onclick: async () => { closePopover(); await setKey(app, id, code); toast(`Tonacja: ${code}`); } }, "Użyj") : h("span", { class: "muted" }, "✓")) : null;
  const all = h("div", { class: "key-grid", style: { marginTop: "8px" } });
  for (const l of ["A", "B"]) for (let n = 1; n <= 12; n++) {
    const k = `${n}${l}`;
    const b = keyBadge(k, k === t.key ? "sel" : "");
    b.addEventListener("click", async () => { closePopover(); await setKey(app, id, k); });
    all.append(b);
  }
  popover(anchor, [
    h("h4", {}, [t.artist, t.title].filter(Boolean).join(" – ")),
    h("div", { class: "pop-row" }, h("span", { class: "muted", style: { width: "110px" } }, "Pewność"), conf(t.key_confidence), `${Math.round((t.key_confidence || 0) * 100)}%`, t.key_manual ? h("span", { class: "muted" }, "(ustawiona ręcznie)") : null),
    choice("Blendora", t.key),
    choice("Alternatywa", t.key2),
    choice("Z tagów pliku", t.tag_key),
    h("h4", { style: { marginTop: "10px" } }, "Albo wybierz ręcznie"), all,
  ]);
}

export async function renderKeys(app) {
  const root = $("#view-keys");
  root.replaceChildren(h("div", { class: "pad muted" }, "Ładowanie…"));
  const d = await api.get("/api/keycheck");
  const pct = (n) => (d.total ? Math.round((n / d.total) * 100) : 0);
  const busy = (btn, fn) => async () => { btn.disabled = true; try { await fn(); } catch (err) { toast(err.message, { error: true }); } btn.disabled = false; };
  const learnBtn = h("button", { class: "btn primary" }, "Ucz się z moich tagów");
  learnBtn.addEventListener("click", busy(learnBtn, async () => {
    const r = await api.post("/api/keycheck/learn");
    const b = Math.round(r.before * 100), a = Math.round(r.after * 100);
    toast(r.applied ? `Zastosowano profile z Twojej biblioteki: zgodność ${b}% → ${a}% (test krzyżowy). Zmieniono ${r.changed} tonacji.` : `Uczenie nie poprawiło wyników (${b}% → ${a}%), zostawiam domyślne.`, { ms: 9000 });
    await app.refresh(); renderKeys(app);
  }));
  const resetBtn = h("button", { class: "btn" }, "Przywróć domyślny algorytm");
  resetBtn.addEventListener("click", busy(resetBtn, async () => { const r = await api.post("/api/keycheck/reset"); toast(`Zmieniono ${r.changed} tonacji`); await app.refresh(); renderKeys(app); }));
  const rescanBtn = h("button", { class: "btn" }, "Wczytaj tonacje z tagów ponownie");
  rescanBtn.addEventListener("click", busy(rescanBtn, async () => { const r = await api.post("/api/tracks/rescan-tags"); toast(`Utwory z tonacją w tagach: ${r.with_key}`); await app.refresh(); renderKeys(app); }));
  const reBtn = d.need_reanalysis ? h("button", { class: "btn accent" }, `Przeanalizuj ${d.need_reanalysis} starszych utworów`) : null;
  reBtn?.addEventListener("click", busy(reBtn, async () => {
    const ids = app.tracks.filter((t) => t.status === "done" && !t.key2).map((t) => t.id);
    await app.bulk(ids, "reanalyze");
  }));

  const rows = d.diffs.map((r) => h("tr", {},
    h("td", {}, r.artist), h("td", {}, r.title),
    h("td", {}, keyBadge(r.key), " ", conf(r.key_confidence)),
    h("td", {}, r.key2 ? keyBadge(r.key2) : ""),
    h("td", {}, keyBadge(r.tag_key)),
    h("td", { class: "muted" }, REL[r.relation]),
    h("td", {},
      h("button", { class: "btn", onclick: async (e) => { e.currentTarget.disabled = true; await setKey(app, r.id, r.tag_key); e.target.closest("tr").remove(); } }, "Użyj z tagów"), " ",
      r.key2 && r.key2 !== r.key ? h("button", { class: "btn", onclick: async (e) => { e.currentTarget.disabled = true; await setKey(app, r.id, r.key2); e.target.closest("tr").remove(); } }, "Użyj alternatywy") : null, " ",
      h("button", { class: "btn", onclick: () => app.loadDeck("A", r.id) }, "Odsłuchaj"))));

  root.replaceChildren(h("div", { class: "pad" },
    h("h2", { style: { marginTop: 0 } }, "Kontrola tonacji"),
    h("p", { class: "muted", style: { maxWidth: "760px" } },
      "Porównuję tonacje wykryte przez Blendorę z tymi, które inne programy (Mixed In Key, Rekordbox, Traktor) zapisały w tagach plików. ",
      "„Ucz się z moich tagów” dopasowuje algorytm do Twojej muzyki. Zanim cokolwiek zmieni, sprawdza na części utworów, czy to faktycznie pomaga. ",
      "Tonacje ustawione ręcznie nigdy nie są nadpisywane."),
    d.total ? h("div", { class: "stat-row" },
      h("div", { class: "stat" }, h("b", {}, `${pct(d.counts.same)}%`), h("span", {}, `zgodnych (${d.counts.same} z ${d.total})`)),
      h("div", { class: "stat" }, h("b", {}, d.counts.relative), h("span", {}, "równoległa dur/moll")),
      h("div", { class: "stat" }, h("b", {}, d.counts.fifth), h("span", {}, "sąsiednia na kole")),
      h("div", { class: "stat" }, h("b", {}, d.counts.parallel + d.counts.other), h("span", {}, "inne różnice")),
      h("div", { class: "stat" }, h("b", {}, d.learned ? "tak" : "nie"), h("span", {}, "profile z Twojej biblioteki")))
      : h("p", {}, h("b", {}, "Żaden przeanalizowany utwór nie ma tonacji w tagach. "), "Jeśli Twoje pliki analizował Mixed In Key, kliknij „Wczytaj tonacje z tagów ponownie”."),
    h("div", { class: "pop-row", style: { marginBottom: "14px" } }, reBtn, learnBtn, resetBtn, rescanBtn),
    d.diffs.length ? h("table", { class: "grid" },
      h("thead", {}, h("tr", {}, ["Wykonawca", "Tytuł", "Blendora", "Alternatywa", "W tagach", "Różnica", ""].map((x) => h("th", {}, x)))),
      h("tbody", {}, rows)) : null,
  ));
}
