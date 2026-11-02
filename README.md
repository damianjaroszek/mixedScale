# Blendora

Lokalne studio do miksowania harmonicznego i mashupów. Analizuje bibliotekę muzyki (tonacja w notacji Camelot, tempo, energia, cue pointy), podpowiada pary utworów do mashupów, pozwala je od razu przetestować na dwóch deckach, zmienić tonację, rozdzielić utwór na wokal i instrumental oraz wyeksportować wynik.

Wszystko działa na Twoim komputerze. Pliki nie są nigdzie wysyłane.

## Instalacja na Windows (exe)

Pobierz `Blendora-Setup-<wersja>.exe` z zakładki **Releases** (albo z artefaktów ostatniego buildu w **Actions**) i uruchom. Instalator nie wymaga uprawnień administratora, tworzy skrót w menu Start i opcjonalnie na pulpicie. W paczce jest już wszystko, łącznie z ffmpeg z Rubber Band, więc nie trzeba instalować Pythona.

Program nie jest podpisany certyfikatem, więc Windows SmartScreen może pokazać ostrzeżenie: kliknij „Więcej informacji” → „Uruchom mimo to”.

Jest też wersja przenośna (`Blendora-<wersja>-portable.zip`): rozpakuj i uruchom `Blendora.exe`.

Blendora otwiera się we własnym oknie (WebView2, wbudowany w Windows 10/11). Gdyby okno się nie otworzyło, użyj skrótu „Blendora (w przeglądarce)”.

## Uruchamianie ze źródeł (Windows / macOS / Linux)

1. Zainstaluj **Python 3.11–3.13** ([python.org](https://www.python.org/downloads/), na Windows zaznacz „Add Python to PATH”).
2. Zainstaluj **ffmpeg** (z Rubber Band daje najlepszą jakość zmiany tonacji):
   - Windows: `winget install Gyan.FFmpeg`
   - macOS: `brew install ffmpeg`
   - Bez ffmpeg aplikacja też ruszy (użyje wbudowanej kopii), ale zmiana tonacji będzie gorszej jakości.
3. Uruchom:
   - Windows: dwuklik `start.bat`
   - macOS / Linux: `./start.sh`

   Za pierwszym razem skrypt zainstaluje zależności (kilka minut). Potem otworzy się przeglądarka z adresem `http://127.0.0.1:8765`.

Po starcie kliknij **+ Dodaj muzykę** i wpisz ścieżkę do folderu z muzyką (albo przeciągnij pliki do okna). Analiza idzie w tle, około 10 s na utwór.

### Stemy wysokiej jakości (Demucs)

Bez Demucsa separacja działa w trybie szybkiego podglądu (niska jakość). Żeby włączyć Demucs:

```
.venv\Scripts\pip install -r requirements-stems.txt     (Windows)
.venv/bin/pip install -r requirements-stems.txt         (macOS / Linux)
```

W wersji exe zainstaluj Demucs osobno: `pip install demucs` (wymaga Pythona). Blendora sama wykryje polecenie `demucs` w PATH.

Pobierze to PyTorch (1–2 GB). Przy pierwszej separacji Demucs ściągnie model (~80 MB). Na CPU separacja utworu trwa 1–3 minuty, z kartą NVIDIA kilkanaście sekund. Wynik jest zapisywany, więc każdy utwór rozdzielasz tylko raz.

## Co potrafi

| Funkcja | Gdzie |
|---|---|
| Analiza tonacji (Camelot + zapis standardowy), BPM, siatka beatów, energia 1–10 | automatycznie po dodaniu muzyki |
| 8 cue pointów na zmianach fraz, z energią każdego fragmentu | waveform decku |
| Dwa decki, crossfader, zoom waveformu, kolor wg pasm (bas / środek / góra) | górna część okna |
| Pomysły na mashup dla decku A, z oceną zgodności 0–100 i uzasadnieniem | środkowy panel, ◀ ▶ |
| Tryb **DJ Mix** (ścisłe tempo, bez transpozycji) i **Mashup** | przełącznik w panelu |
| Filtr pomysłów: energia, tempo, playlisty źródłowe, gatunki | „Filtr pomysłów” |
| Zmiana tonacji ±4 półtony (tempo bez zmian) i odświeżenie pomysłów | „⇅ Tonacja” |
| Auto-transpozycja decku B: aplikacja sama dobiera przesunięcie | pomysły z dopiskiem „deck B ±n półt.” |
| Sync: deck B dopasowany do tempa A bez zmiany tonacji, start w rytm taktu | „Sync”, „Testuj mashup” |
| Stemy: cały utwór / acapella / instrumental, mapa wokalu pod waveformem | „≋ Stemy” |
| Pętle 1–64 bitów, od cue, Loop in / Loop out | „⟳ Pętla” |
| Eksport utworu lub stemu z nową tonacją (WAV / FLAC / MP3) do wybranego folderu | „⤓ Eksport” |
| Eksport całego mashupu do jednego pliku (A + B zsynchronizowane) | Zapisane mashupy → „⤓ Mix” |
| Zapisywanie i ocenianie mashupów, notatki | „★ Zapisz mashup”, zakładka Zapisane mashupy |
| Playlisty (przeciąganie utworów), układanie setu harmonicznie z krzywą energii, eksport M3U | panel Playlisty |
| Edytor tagów, zapis tonacji/BPM/komentarza do plików (ID3 TKEY/TBPM) | zakładka Edytor tagów |
| Koło Camelot z podświetleniem pasujących tonacji; klik filtruje bibliotekę | środkowy panel |

### Skróty

`Spacja` testuj mashup · `Q`/`W` play deck A/B · `←`/`→` pomysły · `1–8` cue na A · `Shift+1–8` cue na B · `L`/`K` pętla A/B · dwuklik wczytuje do A (z Shift do B).

## Dane

Biblioteka, cache analiz, stemy i wyrenderowane warianty są w `~/Blendora` (na Windows `C:\Users\<Ty>\Blendora`). Zmienisz to zmienną `BLENDORA_HOME`. Eksporty domyślnie trafiają do `~/Blendora/exports` (zmiana w Ustawieniach, np. na folder projektu w DAW).

## Budowa

- `app/analysis.py`: tonacja (chroma CQT + profile Krumhansla, Temperleya i Shaatha + ważenie basu), tempo (dopasowanie siatki do obwiedni onsetów), downbeat, struktura (macierz samopodobieństwa taktów), energia.
- `app/mashup.py`: reguły koła Camelot, ocena par, auto-transpozycja, układanie setu.
- `app/ffmpeg.py`: dekodowanie, zmiana tonacji i tempa (Rubber Band), miksowanie.
- `app/stems.py`: Demucs albo szybki podgląd.
- `web/`: interfejs (czysty JavaScript + Web Audio API, bez kroku budowania).

Testy: `pip install -r requirements-dev.txt` i `python -m pytest -q tests`. Testy generują syntetyczne utwory o znanej tonacji i tempie i sprawdzają analizę, sugestie, stemy, eksport, playlisty i tagi.

## Budowanie exe

Exe buduje GitHub Actions (`.github/workflows/build-windows.yml`) przy każdym pushu do `main`: testy, PyInstaller (`packaging/blendora.spec`), autotest gotowego exe, instalator Inno Setup (`packaging/installer.iss`). Tag `v*` (np. `v0.1.0`) publikuje wydanie w Releases.

Lokalnie na Windows: `pip install -r requirements.txt pyinstaller pywebview pillow`, potem `pyinstaller packaging/blendora.spec --noconfirm`. Wynik: `dist\Blendora\Blendora.exe`.

## Dalsze kroki

- Podpis cyfrowy exe (usuwa ostrzeżenie SmartScreen).
- Wersja „Pro” instalatora z wbudowanym Demucsem (+~1 GB).
- Eksport cue pointów do Rekordbox XML / Serato.
- Obsługa kontrolera MIDI.
- Kalibracja energii na Twojej bibliotece (porównanie z Twoimi ocenami).
