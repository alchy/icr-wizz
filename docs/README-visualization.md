# Piano Soundbank Editor — vizualizační specifikace

> Verze: 0.1-draft  
> Stav: pracovní dokument, průběžně aktualizováno

> Changelog:  
> 2025-04-14 v0.1  — initial draft  
> 2025-04-14 v0.2  — KeyboardMap: oprava datového zdroje OutlierReport.scores  
> 2025-04-14 v0.3  — barevné konstanty: vazba na SOURCE_WEIGHTS logiku  
> 2025-04-14 v0.4  — výkon: backend latence, Plotly.react() pravidlo

---

## Vizualizační princip

**Fyzikálně motivované 2D projekce místo obecného vícerozměrného prostoru.**

Každý graf zobrazuje jeden konkrétní fyzikální vztah. Uživatel nikdy nevidí "data" — vidí fyzikální chování nástroje. 3D scatter bez kontextu nepřináší výsledek; nahrazujeme ho sérií grafů kde každá osa má jasnou fyzikální interpretaci.

**Hierarchie pohledů:**

```
KeyboardMap                          ← diagnostická navigace (vždy viditelná)
  └─ RegionView (hover/klik)         ← makro: přes celou klávesnici
       └─ NoteDetail (klik na notu)  ← mikro: jedna nota, všechny vrstvy
            └─ PartialDetail         ← nano: jeden parciál
```

---

## KeyboardMap — specifikace

**Datový zdroj:** `OutlierReport.scores` (dict `{note_key: float}` kde `note_key = "m060_vel4"`) + `AnchorDatabase.entries`

Frontend agreguje outlier skóre per MIDI nota pro zbarvení klávesy: `key_score(midi) = max(scores[f"m{midi:03d}_vel{v}"] for v in 0..7)`. Velocity čtverce zobrazují per-vel skóre přímo.

**Geometrie:**
- 88 kláves (MIDI 21–108), bílé a černé klávesy v reálných proporcích
- Výška sloupce pro každou klávesu: lineárně mapuje `key_score` 0.0–1.0 na výšku 0–40px
- Pod každou klávesou: 8 malých čtverečků (vel 0–7), 4×4px, barva = `fit_quality` per velocity vrstva z `NoteParams.partials`

**Barevné kódování outlier skóre:**

| Skóre | Barva | Popis |
|-------|-------|-------|
| 0.0–0.2 | zelená `#1D9E75` | v normě |
| 0.2–0.5 | žlutá `#BA7517` | mírná odchylka |
| 0.5–0.8 | oranžová `#D85A30` | výrazná odchylka (> ~2.5σ) |
| 0.8–1.0 | červená `#E24B4A` | silný outlier |

**Overlay prvky:**
- Zlatý rámeček `#BA7517` 2px: anchor nota (`AnchorDatabase.entries` kde `midi` sedí)
- Zlatý rámeček s tečkovanou čarou: anchor nota s `vel = -1` (wildcard)
- Modrý fill `#378ADD40`: aktuálně vybraná nota
- Šrafování (diagonální čáry): nota chybí v `BankState.notes`
- Svislé přerušované čáry přes celou klávesnici: pozice přechodů strun (`n_strings` změna)

**Velocity čtvercová mřížka (pod klávesami):**
- Barva čtverce: průměrný `fit_quality` přes `NoteParams.partials` pro danou vel vrstvu
  - `> 0.8`: teal `#1D9E75`
  - `0.5–0.8`: amber `#BA7517`
  - `< 0.5`: coral `#D85A30`
  - klíč neexistuje v `BankState.notes`: šedá `#888780`
- Hover: tooltip s `fit_quality`, `n_partials`, `attack_tau`, `outlier_score`
- Klik: přejít na VelocityEditor pro tuto notu+vel

**Interakce:**
- Klik = výběr noty → NoteDetail
- Shift+klik = anchor dialog (score assignment)
- Drag = výběr rozsahu not pro hromadné operace
- Hover = tooltip: `MIDI {n} — {note_name}{octave}  f0={f0:.1f} Hz  n_partials={n}  outlier={score:.2f}`

---

## RelationView — čtyři subploty

Implementace: Plotly.js `make_subplots` s `rows=1, cols=4`.

### Plot 1 — B-curve (log B vs log f0)

**Osa X:** log10(f0), rozsah 1.4–3.7 (odpovídá A0–C8)  
**Osa Y:** log10(B), rozsah -5 až -2

**Vrstvy (z-order zdola):**
1. `±σ pásmo`: filled area, `rgba(83,74,183,0.1)`, oddělené pro bass a treble segment
2. `±2σ pásmo`: filled area, `rgba(83,74,183,0.05)`
3. `regresní přímky`: dvě čáry (bass/treble), barva `#534AB7`, tloušťka 1.5px, přerušení u zlomu
4. `normální body`: scatter, barva podle registru (bass=purple, mid=teal, treble=coral), velikost 5px
5. `outlier body`: scatter, barva `#E24B4A`, velikost 8px, symbol `x`
6. `anchor body`: scatter, zlatý rámeček, symbol `circle-open`, velikost 9px

**Editovatelné prvky:**
- Drag bod zlomu (svislá přerušovaná čára) → přepočítá segmentaci
- Hover na bod: tooltip s MIDI, f0, B_extracted, B_predicted, residual_sigma

### Plot 2 — spektrální tvar (heatmapa MIDI × k)

**Osa X:** MIDI nota 21–108  
**Osa Y:** harmonický index k, 1–60 (nebo k_max_config)  
**Barva:** A0(k)/A0(1) v dB, divergentní colormap

Colorscale (symetrická kolem 0 dB):
```
-12 dB: #0C447C (tmavě modrá)
 -6 dB: #378ADD
  0 dB: #F1EFE8 (neutrální)
 +6 dB: #D85A30
+12 dB: #712B13 (tmavě červená)
```

**Overlay:**
- Bílá iso-kontura: spline fit při 0 dB
- Bílé body: anchor noty (pro vel median)
- Horizontální přerušovaná čára: aktuálně vybraný k v NoteDetail

**Interakce:**
- Klik na buňku: výběr noty + parciálu → NoteDetail s fokusem na k
- Hover: `MIDI {n} k={k}: A0(k)/A0(1) = {value:.1f} dB (fit: {fit:.1f} dB)`

### Plot 3 — τ profil (τ1, τ2 vs MIDI)

**Osa X:** MIDI nota 21–108  
**Osa Y:** τ v sekundách, log-scale, 0.05–80 s

**Vrstvy:**
1. Stínovaná area: τ2/τ1 pásmo pro každý strun-cluster (1/2/3)
2. τ1 čáry: tenké (1px), tři velocity vrstvy (vel 0, 4, 7) = tři odstíny
3. τ2 čáry: tučnější (2px), stejné tři velocity vrstvy
4. Svislé čáry: přechody strun (editovatelné drahem)
5. Body: outlier noty (τ1 nebo τ2 mimo damping law predikci)

**Barevné kódování velocity:**
- vel 0 (pp): průhledné, `alpha=0.4`
- vel 4 (mf): plné, `alpha=0.85`
- vel 7 (ff): plné, `alpha=1.0`

### Plot 4 — residuální overview

**Typ:** horizontal bar chart, jedna čára per nota  
**Osa Y:** MIDI nota (21 dole, 108 nahoře)  
**Osa X:** kumulativní outlier skóre 0.0–1.0

**Barva:** stejná škála jako KeyboardMap (zelená→červená)  
**Marker:** zlatý kosočtverec pro anchor noty  
**Horizontální čára:** aktuální sigma práh (editovatelný sliderem nad grafem)

**Interakce:**
- Klik na bar: výběr noty (synchronizace s KeyboardMap)

---

## NoteDetail — detailní pohled

### Harmonic spectrum panel

**Typ:** grouped bar chart  
**Osa X:** harmonický index k (1–k_max)  
**Osa Y:** A0 v dB (relativní k A0_k1)

**Vrstvy (per velocity, jako skupiny nebo overlay):**
- Toggle: "zobrazit všechny vel" / "zobrazit jen vybrané vel"
- Každá velocity vrstva = jeden set barů s odlišnou průhledností
- Overlay čára: spektrální tvar z SpectralShapeFitter

**Barevné kódování bars:**
- Normální parciál: barva dle velocity (pp=průhledná, ff=plná)
- Outlier parciál (damping law): červená s křížkem
- Aktuálně vybraný k: zlatý obrys

### Decay envelope panel

**Typ:** line chart, log-Y osa  
**Osa X:** čas 0–duration_s  
**Osa Y:** amplituda v dB

**Vrstvy:**
- Bi-exp obálka: plná čára, barva = velocity
- Mono-exp reference (τ1): přerušovaná šedá čára
- Knee bod: svislá přerušovaná čára + kruh
- Beating modulace: pokud beat_hz > 0, tenká sinusoidální čára superponovaná

**Selector parciálu:** dropdown nebo klik v harmonic spectrum  
**Selector velocity:** radio buttons vel 0–7

### Damping law panel

**Typ:** scatter + line  
**Osa X:** f_k² (Hz²)  
**Osa Y:** 1/τ1(k)

**Vrstvy:**
- Body: každý parciál
- Fit přímka: R + η·f²
- Červené body: outlier parciály (> 3σ od fitu)
- Šedá area: ±1σ pásmo

### Beating map

**Typ:** heatmapa k × vel  
**Barva:** beat_hz od 0 (bílá) do max (modrá)  
**Interakce:** klik na buňku → zobrazit tento parciál v decay panelu

---

## VelocityEditor — vizualizace

### γ_k křivka (hlavní plocha)

**Typ:** spline s editovatelnými uzly  
**Osa X:** harmonický index k (1–k_max)  
**Osa Y:** exponent γ_k (rozsah 0–2)

**Uzlové body:** 8–12 pevných uzlů (rovnoměrně v k-prostoru), drag vertikálně  
**Spline:** cubic interpolation mezi uzly, plynulá čára  
**Reference čára:** γ_k = 1.0 (lineární velocity závislost)  
**Hover na uzlu:** tooltip s k, γ_k, predicted A0(k) ratio ff/pp

### Velocity cross-section panel (vedlejší)

**Typ:** scatter + fit čára  
**Osa X:** velocity index 0–7 (labely: pp, p, mp, mf, mf+, f, ff-, ff)  
**Osa Y:** A0 pro vybraný k (normalizovaný)

**Vrstvy:**
- Extrahované body: scatter, barva = MIDI nota
- Power-law fit: `A0_ref · S(vel)^γ_k`
- Live update při drag γ_k uzlu (< 50ms)

### Attack panel

**Typ:** dual-axis line chart  
**Osa X:** velocity index 0–7  
**Osa Y vlevo:** attack_tau v sekundách (log)  
**Osa Y vpravo:** A_noise (relativní)

**Vrstvy:**
- Extrahované body: scatter (τ má větší rozptyl u nízkých vel → viditelné)
- Power-law fit attack_tau: plná čára, purple
- Power-law fit A_noise: plná čára, coral
- Strop 0.10s: horizontální přerušovaná šedá čára
- Barevný bar dole: `A_noise · attack_tau` (integrovaná energie impulzu)

---

## DiffPreview — vizualizace korekcí

### Summary histogram

**Typ:** histogram delta_pct hodnot  
**Osa X:** delta % (záporné = snížení hodnoty)  
**Osa Y:** počet korekcí  
**Barva:** modrá = |delta| < 10%, oranžová = 10–30%, červená = > 30%

### Corrections table

Seřazená tabulka (defaultně podle |delta_pct| sestupně):

| Nota | Vel | Parametr | Originál | Opraveno | Zdroj | Delta |
|------|-----|----------|----------|----------|-------|-------|

**Barevné kódování delta:**
- `|delta| < 5%`: zelená text
- `5–20%`: oranžová text
- `> 20%`: červená text, tučně

**Interakce:**
- Klik na řádek: přejít na NoteDetail pro danou notu
- Checkbox: vyloučit tuto korekci z exportu

---

## Barevné konstanty — přehled

Konzistentní přes celý nástroj. Korespondují s `OutlierDetector.SOURCE_WEIGHTS` logikou — teplejší barvy = vyšší skóre problému.

```
register bass:    #534AB7  (purple)   — BCurveFitter, bass segment
register střed:   #1D9E75  (teal)     — přechodový region, DampingLaw clustery
register výšky:   #D85A30  (coral)    — Nyquist-omezené výšky
outlier silný:    #E24B4A  (red)      — score > 0.8
outlier střední:  #D85A30  (coral)    — score 0.5–0.8
mírná odchylka:   #BA7517  (amber)    — score 0.2–0.5
v normě:          #1D9E75  (teal)     — score 0.0–0.2
anchor nota:      #BA7517  (amber)    — zlatý rámeček
normální:         #888780  (gray)     — neutrální prvky
fit/model:        #0C447C  (dark blue)— regresní přímky a spline overlay
velocity pp:      průhledná (alpha 0.3)
velocity ff:      plná (alpha 1.0)
```

---

## Výkonnostní požadavky

Cílové latence zahrnují round-trip backend → frontend kde je to relevantní:

| Akce | Cílová latence | Bottleneck |
|------|---------------|------------|
| KeyboardMap překreslení | < 16ms (60fps) | React reconciliation |
| Initial fit po načtení banky | < 800ms | `RelationFitter.fit_all()` backend |
| WS preview update (drag anchor) | < 100ms | inkrementální refit 1 pluginu |
| RelationView update po full fit | < 500ms | Plotly.react() + data transfer |
| NoteDetail přepnutí noty | < 50ms | lokální data z bankStore |
| γ_k drag → cross-section update | < 50ms | lokální výpočet v physics.ts |
| `POST /corrections/propose` | < 200ms | ThreadPool per-nota |
| `POST /export` (704 not) | < 300ms | ThreadPool serialization + json.dump |

Frontend nikdy nevolá `Plotly.newPlot()` pro update dat — vždy `Plotly.react()` pro zachování zoom a pan stavu. Výjimka: přepnutí mezi typy grafů (NoteDetail sekce) kde je `newPlot` žádoucí.
