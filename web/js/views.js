// Secondary views (saved mashups, tag editor, settings) and modals (import, idea filter, help).
import { api } from "./api.js";
import { h, $, modal, closeModal, toast, keyBadge, confirmBox, transpose } from "./ui.js";

const STEM_NAMES = { full: "Cały", vocals: "Acapella", instrumental: "Instrumental" };

// ---------------------------------------------------------------- saved mashups
export async function renderMashups(app) {
  const root = $("#view-mashups");
  const list = await api.get("/api/mashups");
  app.mashupCount = list.length;
  app.renderPlaylists();
  const stars = (m) => h("span", { class: "stars" }, [1, 2, 3, 4, 5].map((n) => h("i", {
    class: n <= m.rating ? "on" : "",
    onclick: async () => { await api.patch(`/api/mashups/${m.id}`, { rating: n === m.rating ? 0 : n }); renderMashups(app); },
  })));
  const side = (m, s) => h("div", {},
    h("div", {}, m[`artist_${s}`], " – ", h("b", {}, m[`title_${s}`])),
    h("div", { class: "muted", style: { fontSize: "11px" } }, keyBadge(transpose(m[`key_${s}`], m[`shift_${s}`])), m[`shift_${s}`] ? ` (${m[`shift_${s}`] > 0 ? "+" : ""}${m[`shift_${s}`]} półt.)` : "", ` · ${STEM_NAMES[m[`stem_${s}`]] || ""} · ${Math.round(m[`bpm_${s}`] || 0)} BPM`));
  const rows = list.map((m) => h("tr", {},
    h("td", {}, side(m, "a")),
    h("td", { class: "muted" }, "×"),
    h("td", {}, side(m, "b")),
    h("td", {}, new Date(m.created_at * 1000).toLocaleDateString("pl-PL")),
    h("td", {}, stars(m)),
    h("td", {}, h("input", { class: "tag-input", value: m.notes || "", placeholder: "notatka…", onchange: (e) => api.patch(`/api/mashups/${m.id}`, { notes: e.target.value }) })),
    h("td", {},
      h("button", { class: "btn", onclick: () => app.loadMashup(m) }, "Wczytaj"), " ",
      h("button", { class: "btn", onclick: () => exportMashupModal(app, m) }, "⤓ Mix"), " ",
      h("button", { class: "btn icon danger", title: "Usuń", onclick: async () => { if (await confirmBox("Usunąć mashup?", "Utwory zostaną w bibliotece.")) { await api.del(`/api/mashups/${m.id}`); renderMashups(app); } } }, "✕"))));
  root.replaceChildren(
    h("div", { class: "toolbar" }, h("span", { class: "lib-title" }, `Zapisane mashupy (${list.length})`), h("span", { class: "spacer" }),
      h("span", { class: "muted" }, "Zapisz bieżącą parę przyciskiem „★ Zapisz mashup”.")),
    h("div", { class: "table-wrap" }, list.length
      ? h("table", { class: "grid" }, h("thead", {}, h("tr", {}, ["Deck A", "", "Deck B", "Data", "Ocena", "Notatka", ""].map((x) => h("th", {}, x)))), h("tbody", {}, rows))
      : h("div", { class: "empty" }, h("h3", {}, "Brak zapisanych mashupów"), h("p", {}, "Wczytaj utwory do decków, przetestuj i kliknij „★ Zapisz mashup”."))));
}

export function exportMashupModal(app, m) {
  const sel = (val) => h("select", { class: "field" }, Object.entries(STEM_NAMES).map(([k, v]) => h("option", { value: k, selected: k === val }, v)));
  const stemA = sel(m.stem_a === "full" ? "vocals" : m.stem_a), stemB = sel(m.stem_b === "full" ? "instrumental" : m.stem_b);
  const bars = h("select", { class: "field" }, [8, 16, 32, 64, 128].map((n) => h("option", { value: n, selected: n === 32 }, `${n} taktów`)));
  const fmt = h("select", { class: "field" }, ["wav", "flac", "mp3"].map((f) => h("option", { value: f, selected: f === app.settings.export_format }, f.toUpperCase())));
  const go = h("button", { class: "btn primary" }, "Eksportuj mix");
  go.addEventListener("click", async () => {
    go.disabled = true;
    try {
      for (const [id, st] of [[m.track_a, stemA.value], [m.track_b, stemB.value]]) {
        const t = app.byId.get(id);
        if (st !== "full" && (!t.stems || t.stems === "none")) {
          go.textContent = "Rozdzielam stemy…";
          await api.waitJob(await api.post(`/api/tracks/${id}/stems`));
          app.updateTrack(await api.get(`/api/tracks/${id}`));
        }
      }
      go.textContent = "Renderuję…";
      const job = await api.waitJob(await api.post("/api/export/mashup", {
        track_a: m.track_a, track_b: m.track_b, shift_a: m.shift_a, shift_b: m.shift_b, cue_a: m.cue_a, cue_b: m.cue_b,
        stem_a: stemA.value, stem_b: stemB.value, bars: +bars.value, format: fmt.value,
      }));
      closeModal();
      toast(`Zapisano mix: ${job.result.path}`, { ms: 9000, action: { label: "Otwórz folder", fn: () => api.post("/api/open-folder", { path: job.result.path }) } });
    } catch (err) { toast(err.message, { error: true }); go.disabled = false; go.textContent = "Eksportuj mix"; }
  });
  modal([
    h("h3", {}, "Eksport mashupu do jednego pliku"),
    h("p", { class: "sub" }, "Deck B zostanie dopasowany do tempa decku A, z zapisanymi zmianami tonacji, od zapisanych cue pointów."),
    h("div", { class: "row" }, h("label", { class: "f" }, "Deck A", stemA), h("label", { class: "f" }, "Deck B", stemB)),
    h("div", { class: "row" }, h("label", { class: "f" }, "Długość", bars), h("label", { class: "f" }, "Format", fmt)),
    h("div", { class: "actions" }, h("button", { class: "btn", onclick: closeModal }, "Anuluj"), go),
  ]);
}

// ---------------------------------------------------------------- tag editor
const TAG_FIELDS = [["artist", "Wykonawca", 160], ["title", "Tytuł", 220], ["album", "Album", 140], ["genre", "Gatunek", 110], ["year", "Rok", 60], ["comment", "Komentarz", 180], ["key", "Tonacja", 60], ["bpm", "BPM", 60]];

export function renderTags(app) {
  const root = $("#view-tags");
  const dirty = new Map();
  const search = h("input", { type: "search", placeholder: "Szukaj…", class: "field", style: { width: "240px" } });
  const writeFile = h("input", { type: "checkbox", checked: true });
  const tbody = h("tbody");
  const save = h("button", { class: "btn primary", disabled: true }, "Zapisz zmiany");
  const count = h("span", { class: "muted" });
  const mark = () => { save.disabled = dirty.size === 0; count.textContent = dirty.size ? `Zmienione: ${dirty.size}` : ""; };
  const draw = () => {
    const q = search.value.toLowerCase();
    const rows = app.tracks.filter((t) => !q || `${t.artist} ${t.title} ${t.genre} ${t.album}`.toLowerCase().includes(q)).slice(0, 1000);
    tbody.replaceChildren(...rows.map((t) => h("tr", {}, TAG_FIELDS.map(([f, , w]) => {
      const inp = h("input", { class: "tag-input", value: t[f] ?? "", style: { width: w + "px" } });
      inp.addEventListener("input", () => {
        const d = dirty.get(t.id) || {};
        d[f] = f === "bpm" ? parseFloat(inp.value) || null : inp.value;
        dirty.set(t.id, d);
        inp.classList.add("dirty");
        mark();
      });
      return h("td", { class: "edit" }, inp);
    }))));
  };
  search.addEventListener("input", draw);
  save.addEventListener("click", async () => {
    save.disabled = true;
    let ok = 0;
    for (const [id, fields] of dirty) {
      try { app.updateTrack(await api.patch(`/api/tracks/${id}`, { ...fields, write_file: writeFile.checked })); ok++; }
      catch (err) { toast(`${app.byId.get(id)?.title}: ${err.message}`, { error: true }); }
    }
    dirty.clear(); mark(); draw();
    toast(`Zapisano tagi: ${ok}`);
  });
  const bulkComment = h("button", { class: "btn", onclick: async () => {
    const ids = app.tracks.filter((t) => t.status === "done").map((t) => t.id);
    if (!(await confirmBox("Wpisać tonację i energię do komentarza?", `Dotyczy ${ids.length} utworów, zapis także w plikach (np. „8A - Energy 7”). Wzór zmienisz w ustawieniach.`))) return;
    await app.bulk(ids, "comment_key");
    draw();
  } }, "Tonacja + energia → komentarz (wszystkie)");
  root.replaceChildren(
    h("div", { class: "toolbar" }, h("span", { class: "lib-title" }, "Edytor tagów"), search, h("label", { style: { display: "flex", gap: "6px", alignItems: "center" } }, writeFile, "zapisuj też w plikach"),
      h("span", { class: "spacer" }), count, bulkComment, save),
    h("div", { class: "table-wrap" }, h("table", { class: "grid" }, h("thead", {}, h("tr", {}, TAG_FIELDS.map(([, l]) => h("th", {}, l)))), tbody)));
  draw();
}

// ---------------------------------------------------------------- settings
export function renderSettings(app) {
  const s = app.settings;
  const st = app.status || {};
  const f = {
    export_dir: h("input", { class: "field", value: s.export_dir }),
    export_format: h("select", { class: "field" }, ["wav", "flac", "mp3"].map((x) => h("option", { value: x, selected: x === s.export_format }, x.toUpperCase()))),
    write_tags_after_analysis: h("input", { type: "checkbox", checked: !!s.write_tags_after_analysis }),
    comment_template: h("input", { class: "field", value: s.comment_template }),
    analysis_workers: h("select", { class: "field" }, [[0, "Automatycznie (rdzenie − 1)"], [1, "1"], [2, "2"], [4, "4"], [6, "6"], [8, "8"]].map(([v, l]) => h("option", { value: v, selected: +s.analysis_workers === v }, l))),
    bpm_range: h("select", { class: "field" }, [["78-180", "78–180 (domyślnie)"], ["88-176", "88–176 (drum & bass jako 174)"], ["70-140", "70–140 (hip-hop, downtempo)"], ["100-200", "100–200 (hardcore, techno)"]].map(([v, l]) => h("option", { value: v, selected: s.bpm_range === v }, l))),
    stem_model: h("select", { class: "field" }, [["htdemucs", "htdemucs (szybszy)"], ["htdemucs_ft", "htdemucs_ft (najlepsza jakość, 4× wolniej)"], ["mdx_extra", "mdx_extra"]].map(([v, l]) => h("option", { value: v, selected: v === s.stem_model }, l))),
  };
  const save = h("button", { class: "btn primary", onclick: async () => {
    const vals = {};
    for (const [k, el] of Object.entries(f)) vals[k] = el.type === "checkbox" ? el.checked : el.value;
    vals.analysis_workers = +vals.analysis_workers;
    app.settings = await api.put("/api/settings", vals);
    await app.refresh();
    toast("Ustawienia zapisane");
  } }, "Zapisz ustawienia");
  const row = (label, el, hint) => [h("label", {}, label), el, hint ? h("div", { class: "hint" }, hint) : null];
  $("#view-settings").replaceChildren(h("div", { class: "pad" },
    h("h2", { style: { marginTop: 0 } }, "Ustawienia"),
    h("div", { class: "settings-grid" },
      row("Folder eksportu (np. folder projektu DAW)", h("div", { style: { display: "flex", gap: "6px" } }, f.export_dir, h("button", { class: "btn", onclick: () => api.post("/api/open-folder", { path: f.export_dir.value }) }, "Otwórz"))),
      row("Format eksportu", f.export_format),
      row("Wpisuj tonację do tagów po analizie", f.write_tags_after_analysis, "Zapisuje TKEY/BPM i komentarz według wzoru poniżej."),
      row("Wzór komentarza", f.comment_template, "Dostępne pola: {key}, {energy}, {bpm}"),
      row("Model separacji stemów", f.stem_model),
      row("Równoległe analizy", f.analysis_workers, "Więcej = szybciej, ale większe obciążenie komputera."),
      row("Zakres tempa", f.bpm_range, "Tempo jest podwajane / dzielone na pół, żeby trafić w ten zakres. Zmiana działa od razu, bez ponownej analizy."),
      h("span"), h("div", {}, save)),
    h("h3", {}, "Eksport do innych programów DJ"),
    h("div", { class: "settings-grid" },
      row("Rekordbox", h("div", {}, h("button", { class: "btn", onclick: async (e) => {
        e.currentTarget.disabled = true;
        try {
          const r = await api.post("/api/export/rekordbox");
          toast(`Zapisano ${r.tracks} utworów i ${r.playlists} playlist: ${r.path}`, { ms: 9000 });
        } catch (err) { toast(err.message, { error: true }); }
        e.target.disabled = false;
      } }, "Zapisz bibliotekę jako rekordbox XML")),
        "BPM z siatką, tonacja, ocena, komentarz, hot cue A–H i playlisty. W Rekordbox: Preferencje → Zaawansowane → Baza danych → rekordbox xml, wskaż plik, potem w drzewie „rekordbox xml” przeciągnij utwory do kolekcji."),
      row("Serato, Traktor, VirtualDJ", h("span", {}, "Włącz „Wpisuj tonację do tagów po analizie”. Te programy czytają tonację i BPM z tagów pliku."))),
    h("h3", {}, "Silniki"),
    h("div", { class: "settings-grid" },
      row("Zmiana tonacji / tempa", h("span", {}, st.rubberband ? "Rubber Band (wysoka jakość)" : "ffmpeg asetrate/atempo (zainstaluj ffmpeg z Rubber Band dla lepszej jakości)")),
      row("Separacja stemów", h("span", {}, st.demucs ? "Demucs (wysoka jakość)" : "Szybki podgląd (niska jakość). Pełną jakość daje wersja Blendora Pro albo: pip install -r requirements-stems.txt")),
      row("Folder danych", h("span", { class: "muted" }, st.home || ""))),
  ));
}

// ---------------------------------------------------------------- modals
export function importModal(app) {
  const path = h("input", { class: "field", placeholder: "np. C:\\Users\\Damian\\Music  albo  /Users/damian/Music" });
  const fileIn = h("input", { type: "file", multiple: true, accept: "audio/*", class: "hidden" });
  const bar = h("div");
  const prog = h("div", { class: "progress hidden", style: { marginTop: "10px" } }, bar);
  const scan = h("button", { class: "btn primary" }, "Skanuj folder");
  scan.addEventListener("click", async () => {
    if (!path.value.trim()) return;
    scan.disabled = true;
    try {
      const r = await api.post("/api/import/folder", { path: path.value.trim() });
      closeModal();
      toast(`Znaleziono ${r.found} plików, dodano ${r.added}. Analiza działa w tle.`);
      app.refresh();
    } catch (err) { toast(err.message, { error: true }); scan.disabled = false; }
  });
  path.addEventListener("keydown", (e) => { if (e.key === "Enter") scan.click(); });
  fileIn.addEventListener("change", () => uploadFiles(app, [...fileIn.files], bar, prog));
  modal([
    h("h3", {}, "Dodaj muzykę"),
    h("p", { class: "sub" }, "Podaj folder z muzyką na tym komputerze. Pliki zostają tam, gdzie są; Blendora tylko je indeksuje i analizuje."),
    h("div", { style: { display: "flex", gap: "8px" } }, path,
      window.pywebview?.api?.pick_folder ? h("button", { class: "btn", onclick: async () => { const d = await window.pywebview.api.pick_folder(); if (d) { path.value = d; scan.click(); } } }, "Wybierz…") : null,
      scan),
    h("div", { class: "dropzone", style: { marginTop: "14px" } }, "albo przeciągnij pliki w dowolne miejsce okna, albo ",
      h("button", { class: "btn", onclick: () => fileIn.click() }, "wybierz pliki"), fileIn),
    prog,
    h("p", { class: "muted", style: { fontSize: "12px" } }, "Formaty: MP3, WAV, FLAC, AIFF, M4A/AAC, OGG, OPUS."),
  ]);
}

export function uploadFiles(app, files, bar, prog) {
  files = files.filter((f) => /\.(mp3|wav|flac|aiff?|m4a|aac|ogg|opus|wma)$/i.test(f.name));
  if (!files.length) { toast("Brak plików audio", { error: true }); return; }
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f));
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/import/upload");
  prog?.classList.remove("hidden");
  const t = h("div", { class: "toast" }, `Wysyłanie ${files.length} plików…`);
  document.getElementById("toasts").append(t);
  xhr.upload.onprogress = (e) => { const p = Math.round((e.loaded / e.total) * 100); if (bar) bar.style.width = p + "%"; t.textContent = `Wysyłanie ${files.length} plików… ${p}%`; };
  xhr.onload = () => {
    t.remove();
    closeModal();
    if (xhr.status === 200) { const r = JSON.parse(xhr.responseText); toast(`Dodano ${r.added} utworów. Analiza działa w tle.`); app.refresh(); }
    else toast("Nie udało się wysłać plików", { error: true });
  };
  xhr.send(fd);
}

export function ideaFilterModal(app) {
  const f = app.ideaFilter;
  const energy = h("div", { class: "check-list", style: { flexDirection: "row", flexWrap: "wrap" } }, Array.from({ length: 10 }, (_, i) => i + 1).map((n) =>
    h("label", {}, h("input", { type: "checkbox", value: n, checked: f.energy.includes(n) }), String(n))));
  const tempo = h("select", { class: "field" }, [["", "Dowolne (z dopasowaniem tempa)"], ["3", "±3%"], ["6", "±6%"], ["10", "±10%"], ["16", "±16%"]].map(([v, l]) => h("option", { value: v, selected: String(f.tempo_tol ?? "") === v }, l)));
  const pls = h("div", { class: "check-list" }, app.playlists.length ? app.playlists.map((p) => h("label", {}, h("input", { type: "checkbox", value: p.id, checked: f.playlists.includes(p.id) }), `${p.name} (${p.count})`)) : h("span", { class: "muted" }, "Brak playlist: szukam w całej bibliotece"));
  const genres = h("div", { class: "check-list" }, app.genres().length ? app.genres().map((g) => h("label", {}, h("input", { type: "checkbox", value: g, checked: f.genres.includes(g) }), g)) : h("span", { class: "muted" }, "Brak gatunków w tagach"));
  const shiftB = h("input", { type: "checkbox", checked: f.allow_shift_b !== false });
  const half = h("input", { type: "checkbox", checked: f.half_double !== false });
  const vals = (root) => [...root.querySelectorAll("input:checked")].map((i) => i.value);
  modal([
    h("h3", {}, "Filtr pomysłów"),
    h("p", { class: "sub" }, "Zawęź sugestie mashupów do wybranych playlist, gatunków, energii i tempa."),
    h("div", { class: "row" }, h("label", { class: "f" }, "Energia (puste = wszystkie)", energy), h("label", { class: "f" }, "Tempo", tempo)),
    h("div", { class: "row" }, h("label", { class: "f" }, "Playlisty źródłowe", pls), h("label", { class: "f" }, "Gatunki", genres)),
    h("label", { style: { display: "flex", gap: "8px", alignItems: "center", marginBottom: "6px" } }, shiftB, "Szukaj też po transpozycji decku B (±4 półtony): więcej pomysłów"),
    h("label", { style: { display: "flex", gap: "8px", alignItems: "center" } }, half, "Uwzględniaj half-time / double-time (np. 87 ↔ 174 BPM)"),
    h("div", { class: "actions" },
      h("button", { class: "btn", onclick: () => { app.ideaFilter = app.defaultIdeaFilter(); closeModal(); app.refreshIdeas(); } }, "Wyczyść filtr"),
      h("button", { class: "btn primary", onclick: () => {
        app.ideaFilter = { energy: vals(energy).map(Number), tempo_tol: tempo.value ? +tempo.value : null, playlists: vals(pls).map(Number), genres: vals(genres), allow_shift_b: shiftB.checked, half_double: half.checked };
        closeModal(); app.refreshIdeas();
      } }, "Zastosuj")),
  ]);
}

export function helpModal() {
  const row = (k, d) => h("tr", {}, h("td", {}, h("b", {}, k)), h("td", {}, d));
  modal([
    h("h3", {}, "Skróty i wskazówki"),
    h("table", { class: "grid", style: { margin: "10px 0" } }, h("tbody", {},
      row("Spacja", "Testuj mashup / zatrzymaj oba decki"),
      row("Q / W", "Play/pauza deck A / deck B"),
      row("← / →", "Poprzedni / następny pomysł"),
      row("Przyciski 1 – 8 pod deckiem", "Klik: skok do cue (w rytm drugiego decku). Prawy klik: przesuń / usuń. „+”: dodaj cue"),
      row("1 – 8", "Skok do cue na decku A"),
      row("Shift + 1 – 8", "Skok do cue na decku B"),
      row("L / K", "Pętla na decku A / B"),
      row("C / Shift + C", "Dodaj cue w najbliższym beacie na decku A / B"),
      row("Ctrl + Z", "Cofnij ostatnią zmianę cue, siatki lub tonacji"),
      row("‹ ›  (przytrzymaj)", "Chwilowo zwolnij / przyspiesz deck, żeby dograć fazę na ucho"),
      row("▦ Siatka", "Popraw BPM (×2, ÷2), „Beat tutaj”, „Raz tutaj”, przesuń lub usuń cue"),
      row("Dwuklik", "Wczytaj do decku A (z Shift: do decku B)"))),
    h("p", { class: "sub" }, "Skok do cue przy włączonym Sync startuje od następnego taktu drugiego decku, więc oba utwory zostają w rytmie. Czerwony pasek pod waveformem pokazuje, gdzie jest wokal (po separacji stemów)."),
    h("div", { class: "actions" }, h("button", { class: "btn primary", onclick: closeModal }, "OK")),
  ]);
}
