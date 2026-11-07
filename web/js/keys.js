// Key check view: compare detected keys with keys already in file tags (Mixed In Key, Rekordbox...),
// learn from the user's library, and fix single tracks quickly.
import { api } from "./api.js";
import { h, $, toast, keyBadge, popover, closePopover, confirmBox, prompt } from "./ui.js";

const REL = { same: "zgodne", relative: "równoległa dur/moll", fifth: "sąsiednia (kwinta)", parallel: "ta sama tonika", other: "inna" };

function conf(c) { return h("span", { class: "conf", title: `Pewność ${Math.round((c || 0) * 100)}%` }, h("i", { style: { width: `${Math.round((c || 0) * 100)}%` } })); }

async function setKey(app, id, key) {
  const t = app.byId.get(id);
  if (t) app.pushUndo({ id, fields: { key: t.key, key_manual: t.key_manual || 0 }, label: `tonacja ${key}` });
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

  const tempoPart = await tempoBlock(app);
  root.replaceChildren(h("div", { class: "pad" }, tempoPart,
    h("h2", { style: { marginTop: "28px" } }, "Tonacje"),
    h("p", { class: "muted", style: { maxWidth: "760px" } },
      "Porównuję tonacje wykryte przez Blendorę z tymi, które inne programy (Mixed In Key, Rekordbox) zapisały w tagach plików, albo z kolekcją Traktora. ",
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


const KIND = { half_double: "połowa / podwójne", close: "różnica < 1 BPM", other: "inne BPM", same: "siatka przesunięta" };

async function tempoBlock(app) {
  const d = await api.get("/api/tempocheck");
  const box = h("div");
  const busy = (btn, fn) => async () => { btn.disabled = true; try { await fn(); } catch (err) { toast(err.message, { error: true }); } btn.disabled = false; };
  const cmpBtn = h("button", { class: "btn primary" }, d.traktor ? "Porównaj ponownie z Traktorem" : "Porównaj z kolekcją Traktora");
  cmpBtn.addEventListener("click", busy(cmpBtn, async () => {
    let r;
    try { r = await api.post("/api/traktor/compare", {}); }
    catch (err) {
      const path = await prompt("Gdzie jest collection.nml Traktora?", d.traktor || "", "Zwykle: Dokumenty\\Native Instruments\\Traktor 3.x.x\\collection.nml");
      if (!path) return;
      r = await api.post("/api/traktor/compare", { path });
    }
    toast(`Kolekcja Traktora: ${r.entries} utworów, ${r.matched} pasuje do biblioteki Blendory.`, { ms: 8000 });
    await app.refresh();
    renderKeys(app);
  }));
  const c = d.counts || {};
  const same = c.same || 0;
  const pct = d.total ? Math.round((same / d.total) * 100) : 0;
  const offs = Object.entries(d.median_offset_ms || {});
  const rows = d.diffs.map((r) => {
    const tr = h("tr", {},
      h("td", {}, r.artist), h("td", {}, r.title),
      h("td", {}, (+r.bpm).toFixed(2), r.steady === 0 ? h("span", { class: "tempo-warn" }, " ⚠") : ""),
      h("td", {}, `${(+r.ref_bpm).toFixed(2)} (${r.source})`),
      h("td", { class: "muted" }, KIND[r.kind] || r.kind),
      h("td", {}, r.kind === "same" ? `${r.phase_ms > 0 ? "+" : ""}${r.phase_ms} ms` : r.drift_ms ? `${r.drift_ms} ms na końcu` : ""),
      h("td", { class: "acts" },
        r.source === "Traktor" ? h("button", { class: "btn", title: "Ustaw BPM i siatkę jak w Traktorze (cue przesuną się razem z siatką)", onclick: async (e) => {
          e.currentTarget.disabled = true;
          await api.post("/api/tempocheck/use-traktor", { ids: [r.id] });
          app.updateTrack(await api.get(`/api/tracks/${r.id}`));
          tr.remove();
        } }, "Użyj z Traktora") : null, " ",
        h("button", { class: "btn", onclick: () => app.loadDeck("A", r.id) }, "Odsłuchaj"), " ",
        h("button", { class: "btn", title: "Paczka z danymi analizy do wysłania", onclick: () => app.reportProblem(r.id) }, "Zgłoś")));
    return tr;
  });
  box.append(
    h("h2", { style: { marginTop: 0 } }, "Tempo i siatka"),
    h("p", { class: "muted", style: { maxWidth: "760px" } },
      "Porównuję BPM i siatkę Blendory z kolekcją Traktora (jeśli siatka w Traktorze jest dobra, to najlepszy wzorzec) albo z BPM zapisanym w tagach. ",
      "„Przesunięcie” to różnica położenia beatu w milisekundach: do ok. 10 ms nie słychać różnicy."),
    d.total ? h("div", { class: "stat-row" },
      h("div", { class: "stat" }, h("b", {}, `${pct}%`), h("span", {}, `zgodnych (${same} z ${d.total})`)),
      h("div", { class: "stat" }, h("b", {}, c.half_double || 0), h("span", {}, "połowa / podwójne tempo")),
      h("div", { class: "stat" }, h("b", {}, (c.close || 0) + (c.other || 0)), h("span", {}, "inne BPM")),
      ...offs.map(([ext, ms]) => h("div", { class: "stat", title: "Mediana przesunięcia siatki względem Traktora dla tego typu plików" }, h("b", {}, `${ms > 0 ? "+" : ""}${ms} ms`), h("span", {}, `przesunięcie ${ext}`))))
      : h("p", {}, "Brak danych do porównania: kliknij „Porównaj z kolekcją Traktora” albo dodaj utwory z BPM w tagach."),
    h("div", { class: "pop-row", style: { marginBottom: "14px" } }, cmpBtn,
      d.diffs.some((r) => r.source === "Traktor") ? (() => {
        const all = h("button", { class: "btn" }, "Użyj siatki Traktora dla wszystkich z listy");
        all.addEventListener("click", busy(all, async () => {
          if (!(await confirmBox("Użyć BPM i siatki z Traktora?", "Dla wszystkich utworów z listy poniżej. Można cofnąć tylko ponowną analizą."))) return;
          const r = await api.post("/api/tempocheck/use-traktor", { ids: d.diffs.filter((x) => x.source === "Traktor").map((x) => x.id) });
          toast(`Zmieniono ${r.changed} utw.`);
          await app.refresh();
          renderKeys(app);
        }));
        return all;
      })() : null),
    rows.length ? h("table", { class: "grid" },
      h("thead", {}, h("tr", {}, ["Wykonawca", "Tytuł", "Blendora", "Wzorzec", "Różnica", "Przesunięcie / rozjazd", ""].map((x) => h("th", {}, x)))),
      h("tbody", {}, rows)) : "");
  return box;
}
