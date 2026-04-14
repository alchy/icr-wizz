# Piano Soundbank Editor — uživatelské rozhraní

> Verze: 0.1-draft  
> Stav: pracovní dokument, průběžně aktualizováno

> Changelog:  
> 2025-04-14 v0.1  — initial draft  
> 2025-04-14 v0.2  — FileSelector: chybové taby, initial fit po načtení  
> 2025-04-14 v0.3  — AnchorPanel: API volání, suggest_anchors, vel=-1 wildcard  
> 2025-04-14 v0.4  — DiffPreview: CorrectionSet místo list, CSV report button

---

## Principy návrhu UI

**Fyzikálně motivované projekce, ne obecný prostor.** Každý graf ukazuje jeden konkrétní fyzikální vztah. 3D scatter bez kontextu nepřináší výsledek — nahrazujeme ho sérií 2D projekcí, kde každá má jasnou interpretaci.

**Diagnostika jako primární navigace.** Uživatel neprochází seznam čísel — vidí klávesnici zbarvenou podle míry problému a naviguje od největšího problému k nejmenšímu.

**Live feedback.** Změna anchor score nebo drag spline uzlu se okamžitě projeví ve všech relevantních grafech přes WebSocket. Export je až poslední krok po vizuální verifikaci.

**Non-destruktivní editace.** Originální banka se nikdy nemodifikuje. Undo stack na frontendu. Export vždy explicitní akcí uživatele.

---

## Layout — dvoupanelový

```
┌─────────────────────────────────────────────────────────────────┐
│  [FileSelector]  banka: ks-grand-f44  |  anchor DB: ks-grand-v1 │  ← header bar
├───────────────────────┬─────────────────────────────────────────┤
│                       │                                         │
│   KeyboardMap         │   kontextový panel                      │
│   (stálý, vlevo)      │   (mění se dle výběru)                  │
│                       │                                         │
│   88 kláves           │   → RelationView (výchozí)              │
│   barva = outlier     │   → NoteDetail (po kliknutí na notu)    │
│   výška = skóre       │   → VelocityEditor (po kliknutí vel)    │
│                       │   → AnchorPanel (tlačítko v headeru)    │
│                       │   → DiffPreview (před exportem)         │
│                       │   → MidiPanel (SysEx)                   │
├───────────────────────┴─────────────────────────────────────────┤
│  [status bar]  fit quality · outlierů: 7 · anchor: 12/88       │
└─────────────────────────────────────────────────────────────────┘
```

---

## FileSelector

**Účel:** výběr pracovního adresáře a konkrétních JSON souborů banky.

**Chování:**
- Vstup: cesta k adresáři (textové pole nebo native file dialog)
- Volá `GET /bank/list?directory=...` — zobrazí seznam JSON souborů s metadaty: `instrument_name`, `midi_range`, `sr`, počet not, velikost souboru
- Multi-select: uživatel může načíst více souborů najednou — volá `POST /bank/load` s listem cest
- Backend vrací `{loaded: [...], errors: [...]}` — každý načtený soubor se zobrazí jako tab v headeru, soubory s chybou jako tab s červenou ikonou a tooltip chybové zprávy
- Přepínání tabů mění aktivní banku bez opětovného načítání

**Anchor database selector:**
- Dropdown vedle file selectoru: výběr existující anchor DB (`GET /anchors/list`) nebo "nová databáze"
- Při vytvoření nové: inline dialog pro pojmenování a volitelný popis (`ks-grand-recording-session-1`)
- Anchor DB je nezávislá na konkrétním souboru banky — lze aplikovat stejnou DB na různé extrakce téhož nástroje
- Aktuální anchor DB a banka jsou zobrazeny v header baru; při nesouladu `instrument_hint` se zobrazí varování

**Stav po načtení:**
- Spustí se automatický initial fit (`POST /fit` s uniform váhami)
- KeyboardMap se zbarví podle `FitResult.outlier_scores`
- Status bar zobrazí `outlierů: N · anchor: 0/88`

---

## KeyboardMap

**Účel:** primární navigační a diagnostický pohled.

**Vizuální kódování:**

| Vlastnost | Kódování |
|-----------|----------|
| Outlier skóre | výška sloupce (vysoké = velký problém) |
| Kumulativní chybovost | barva: zelená → žlutá → červená |
| Register | odstín: tmavší = bass, světlejší = výšky |
| Anchor nota | zlatý rámeček |
| Aktuálně vybraná | modrý highlight |
| Chybějící nota (extrakce selhala) | šrafování |

**Interakce:**
- Klik = výběr noty → NoteDetail v pravém panelu
- Shift+klik = přidání noty do anchor setu (otevře score dialog)
- Hover = tooltip: MIDI, f0, n_partials, outlier skóre
- Drag po klávesnici = výběr rozsahu pro hromadné operace

**Velocity indikátor:** pod každou klávesou 8 malých čtverečků (vel 0–7), zbarvených podle fit kvality pro danou velocity vrstvu. Klik na čtvereček = přechod do VelocityEditor pro tu vrstvu.

---

## RelationView

**Účel:** vizualizace fyzikálních vztahů přes celou klávesnici.

**Čtyři subploty vedle sebe (Plotly subplot grid):**

**Plot 1 — B-curve (log B vs log f0)**
- Body: každá nota (barva = register, symbol = velocity median)
- Overlay: segmentovaná regresní přímka + ±2σ pásmo
- Outlier noty: červené s výraznějším symbolem
- Klik na bod = přechod na NoteDetail

**Plot 2 — Spektrální tvar (heatmapa MIDI × k)**
- Osa X: MIDI nota (21–108)
- Osa Y: harmonický index k (1–60)
- Barva: A0(k)/A0(1) v dB, divergentní colormap (modrá = slabší než průměr, červená = silnější)
- Overlay: spline fit jako iso-kontura

**Plot 3 — τ profil (τ1 a τ2 vs MIDI)**
- Dvě čáry na ose Y (log-scale): τ1 a τ2 pro každou notu
- Barva čar: vel 0, 4, 7 jako tři různé čáry
- Svislé čáry: pozice přechodů strun (editovatelné drahem)
- Shaded area: τ2/τ1 pásmo pro každý cluster

**Plot 4 — Residuály fitů (overview)**
- Bar chart: per-nota kumulativní residuál (součet přes všechny Typ A vztahy)
- Barva: stejná jako KeyboardMap (zelená → červená)
- Horizontální linie: práh pro označení outlieru

---

## NoteDetail

**Účel:** detailní pohled na jednu notu (všechny velocity vrstvy).

**Sekce:**

**Harmonic spectrum**
- Sloupcový graf A0(k) v dB, overlay pro každou velocity vrstvu (vel 0 = průhledná, vel 7 = plná)
- Overlay: spline fit spektrálního tvaru z RelationFitter
- Klik na sloupec k = zobrazit τ1/τ2 pro tento parciál

**Decay envelope**
- Bi-exp obálka v dB pro vybraný parciál
- Overlay: mono-exp pro srovnání, svislá čára = knee bod
- Selector parciálu: dropdown nebo klik v harmonic spectrum

**Damping law**
- Scatter plot 1/τ1(k) vs f_k²
- Overlay: lineární fit (R + η·f²)
- Červené body = outlier parciály (> 3σ od fitu)

**Beating map**
- Malá heatmapa: k × vel, barva = beat_hz
- Odhalí parciály s neobvyklým beatem

---

## VelocityEditor

**Účel:** editace velocity závislostí pro vybranou notu.

**Hlavní plocha — γ_k křivka:**
- Osa X: harmonický index k (1–60)
- Osa Y: exponent γ_k
- Editovatelný spline: drag uzlových bodů
- Tlačítko "reset na fit" — vrátí na hodnoty z RelationFitter
- Tlačítko "kopírovat z noty X" — přebrat γ_k z jiné noty

**Live preview — velocity cross-section:**
- Pro vybraný harmonický index k: scatter A0 vs vel (body = extrakce, čára = model)
- Aktualizuje se v reálném čase při drag γ_k

**Attack panel:**
- Power-law fit attack_tau vs vel (s viditelným stropem 0.10s)
- Dvě čáry: attack_tau (levá osa) a A_noise (pravá osa)
- Slider pro manuální override τ_ref a α exponentu
- Integrovaná energie impulzu `A_noise · attack_tau` jako barevný bar

---

## AnchorPanel

**Účel:** správa databáze "správných" extrakčních bodů.

**Sekce:**

**Aktuální anchor set:**
- Tabulka: nota | note_name | velocity | score (0–9) | f0 [Hz] | n_partials | fit_quality median | poznámka
- Inline editace score: klik na číslo → slider 0–9 nebo numerický vstup
- Řádek s `vel = -1` zobrazí "všechny vel" v sloupci velocity — zlatá ikonka indikuje wildcard
- Tlačítko "odebrat" per řádek — volá `PUT /anchors/{name}` (update DB bez záznamu)
- Barevné kódování score: červená (0–3) → žlutá (4–6) → zelená (7–9)
- Každá změna score okamžitě pošle WS zprávu `update_anchor` → live update RelationView

**Score assignment dialog (při shift+kliku na KeyboardMap nebo kliknutí čtverce velocity):**
- Modalless panel ve spodní části AnchorPanelu — neblokuje zbytek UI
- Zobrazuje MIDI notu, note_name, f0, n_partials, aktuální fit_quality
- Výběr: konkrétní velocity vrstva (radio 0–7) nebo "všechny vrstvy" (`vel = -1`)
- Score slider 0–9 s textovým popisem vedlejšího sloupce:
  - 0 = ignorovat (nízké SNR, krátká nahrávka)
  - 3 = použít s nízkou důvěrou
  - 6 = standardní kvalita
  - 9 = referenční bod (nejlepší extrakce)
- Volitelná poznámka (text input, max 80 znaků)
- Potvrzení: Enter nebo klik "Přidat" → `POST /anchors/save` s aktualizovanou DB

**Anchor database management:**
- Název databáze (editovatelný inline — `POST /anchors/save` při každé změně)
- Datum poslední úpravy (`modified` z `AnchorDatabase`)
- Tlačítka: Uložit | Exportovat JSON | Importovat JSON
- Smazat databázi: `DELETE /anchors/{name}` s potvrzovacím dialogem
- Přepínač databází: dropdown `GET /anchors/list`

**Coverage visualizer:**
- Mini klávesnice (zjednodušená) s barevně označenými anchor notami (barva = score)
- Tři čísla: bass / mid / treble — počet anchor not v každém registru
- Varování pod mini klávesnicí pokud je pokrytí pod `COVERAGE_THRESHOLDS`:
  - Bass < 3, Mid < 6, Treble < 3, vel_low < 2, vel_high < 2, total < 10
- Tlačítko "Navrhnout anchor noty" → `POST /anchors/suggest` → zobrazí seznam doporučení s reason a priority

---

## DiffPreview

**Účel:** zobrazit navržené korekce před exportem. Data pocházejí z `CorrectionSet` vraceného `POST /corrections/propose`.

**Tabulka korekcí:**
- Sloupce: nota | vel | parametr | originál | navržená korekce | zdroj | delta %
- Seřazena defaultně podle `|delta_pct|` sestupně
- Filtr: zdroj (`CorrectionSource` enum) | práh delta (zobrazit jen > N %)
- Barevné kódování delta: modré = `|delta| < 5%`, oranžové = `5–20%`, červené = `> 20%`
- Checkbox per řádek: odškrtnout = vyloučit tuto korekci z exportu (`selected_fields` v `POST /corrections/apply`)
- Klik na řádek = navigace na NoteDetail pro danou notu

**Summary panel:**
- `CorrectionSet.summary()` — počet korekcí, dotčených not, max delta %
- Histogram delta % (distribuce velikostí změn)
- Tlačítko "Aplikovat vybrané" → `POST /corrections/apply` → nový `BankState` s `is_modified=True`

**Export options:**
- Diff only (jen změněné noty) / Full bank
- Výstupní cesta (defaultně `soundbanks-corrected/{original_name}-corrected.json`)
- Checkbox: přidat `_editor_metadata` do JSON (anchor DB název, export timestamp, corrections_count)
- Tlačítko "Exportovat" → `POST /export` → stažení souboru
- Tlačítko "CSV report" → `POST /export/diff-report` → CSV audit log

---

## MidiPanel

**Účel:** SysEx patch interface pro cílový syntetizér.

> Detaily SysEx protokolu budou doplněny v samostatné specifikaci.

**Základní prvky (MVP):**
- Výběr MIDI portu (dropdown ze systémových portů)
- Status indikátor: připojen / nepřipojen
- Test tlačítko: odeslat identity request
- Patch button: odeslat SysEx patch pro aktuálně opravenou banku
- Progress bar: průběh patchování (nota po notě)
- Log panel: posledních N SysEx zpráv (hex + parsed)

---

## Klávesové zkratky

| Zkratka | Akce |
|---------|------|
| Šipky ← → | přechod na předchozí / následující notu |
| Shift+klik | přidat notu do anchor setu |
| Ctrl+Z | undo poslední korekce |
| Ctrl+S | uložit anchor databázi |
| Ctrl+E | otevřít DiffPreview / Export |
| Escape | zrušit výběr / zavřít dialog |
| Space | přepnout play preview tónu (pokud je k dispozici audio engine) |
