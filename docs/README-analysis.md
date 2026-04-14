# Piano Soundbank Editor — analytický základ

> Verze: 0.1-draft  
> Stav: pracovní dokument, průběžně aktualizováno

> Changelog:  
> 2025-04-14 v0.1  — initial draft  
> 2025-04-14 v0.2  — sekce: od analýzy k implementaci (OutlierDetector, CorrectionEngine)  
> 2025-04-14 v0.3  — tabulka matematických nástrojů: oprava scipy metod + paralelizace

---

## Formulace problému

Extrakce z originálních nahrávek piana je deterministická, ale nespolehlivá. Šum měření, překryv parciálů, zkrácené noty, mikrofonní barva a chyby tau fittování tvoří systematické odchylky — nikoliv lokální (jedna nota), ale strukturální (celý register nebo celá velocity vrstva).

**Cílem editoru není opravit absolutní číselné hodnoty, ale identifikovat a opravit strukturální odchylky ve fyzikálních vztazích** tak, aby syntetizovaný tón odpovídal originálu v barvě.

Klíčový insight: barva tónu není uložena v jednom parametru, ale v relacích — poměr A0 harmonik, tvar τ(k) křivky, poměr šumové složky k harmonické v attack fázi, rychlost přechodu τ1→τ2.

---

## Taxonomie parametrických vztahů

### Typ A — fyzikální invarianty

Tyto vztahy by měly být konstantní nebo pomalu monotónní přes celou klávesnici. Odchylky jsou chyby extrakce, ne fyzikální realita.

**Inharmonicita B(f0)**

```
log10(B) ≈ α · log10(f0) + β
```

- Dva lineární segmenty v log-log prostoru s lomem u přechodu ovinutých → čistých strun (typicky MIDI 48–52, závisí na nástroji)
- Slope α: +2 až +4 v bassu, +3 až +5 ve výškách
- B musí být velocity-nezávislé (fyzikální vlastnost struny)
- Outlier detekce: MAD-sigma na residuálech log-log fitu

**Damping law**

```
1/τ(k) = R + η · f_k²
```

kde `f_k = k · f0 · √(1 + B·k²)`

- Fyzikální constraint platný per nota
- Residuály odhalí artefaktní parciály (překryv, nízké SNR)
- R = vnitřní tření struny, η = vzdušný odpor (frekvenčně závislý)

**Bi-exponenciální decay a τ2/τ1 clustering**

```
A(t) = A0 · [a1 · e^(-t/τ1) + (1-a1) · e^(-t/τ2)]
```

Tři clustery podle počtu strun:

| Register | Struny | τ2/τ1 typicky | a1 typicky | Poznámka |
|----------|--------|---------------|------------|----------|
| Bass | 1 | 10–25× | 0.85–0.95 | beating ≈ 0 |
| Střed | 2 | 8–20× | 0.60–0.80 | beating 0.1–0.5 Hz |
| Výšky | 3 | 5–15× | 0.30–0.60 | double-decay efekt |

Diagnostika: `a1 = 1.0` u výšky = fit failure (krátká nahrávka nebo nízké SNR).

---

### Typ B — registrové trendy

Tyto vztahy závisí na f0 a mají specifický funkcionální tvar přes klávesnici. Fittujeme spline přes MIDI osu.

**Počet parciálů n_partials(midi)**

Aktivní limit je vždy `min(k_Nyquist, k_SNR, k_max_config)`:

- Bass (21–45): vítězí `k_max_config` (SNR a Nyquist jsou daleko)
- Střed (46–72): vítězí SNR nebo `k_max_config` (nestabilní oblast)
- Výšky (73–108): vítězí Nyquist (C8 = jen 5 parciálů)

**Spektrální tvar A0(k)/A0(1)**

2D spline povrch (k × MIDI). Normalizovaný harmonický index `k/n_partials` pro srovnatelnost napříč registry. Interpolace v log-domain (odpovídá dB, fyzikálně smysluplné).

**noise_centroid_hz(midi)**

Empiricky klesá s MIDI notou. Spline fit přes kotevní body.

**n_strings(midi)**

Skokový přechod (1→2 u ~MIDI 38–42, 2→3 u ~MIDI 48–52). Přesná poloha závisí na nástroji — anchor tóny ji kalibrují.

---

### Typ C — velocity závislosti

**Spektrální exponent γ_k**

```
A0(k, vel) = A0_ref(k) · S(vel)^γ_k
```

kde `S(vel) = (vel+1)/8` normalizovaná velocity, `A0_ref(k)` je forte spektrální tvar.

- γ_k je per-harmonický exponent (pole délky k_max)
- Nízké γ_k: harmonik citlivý na velocity (výrazné ztmavnutí při pp)
- Vysoké γ_k: harmonik stabilní napříč velocity
- Editace: drag křivky γ_k v harmonickém prostoru

**Velocity model attack_tau**

```
attack_tau(vel) ≈ τ_ref · (v_norm)^(-α)
A_noise(vel)    ≈ A_ref  · (v_norm)^β
```

- Power-law pokles τ s velocity (tvrdší filc = kratší kontaktní doba)
- Strop: `min(extracted, τ1_k1, 0.10s)` — pipeline sanitizace
- Fit pouze z vel 4–7 (nejlepší SNR), extrapolace dolů s clampingem
- Perceptuálně relevantní: `A_noise · attack_tau` jako integrovaná energie impulzu

---

## Kvalita extrakce — kumulativní chybovost per region

| Region | Problém | Dominantní zdroj chyby |
|--------|---------|----------------------|
| Bass (21–45) | nízký | k_max config vítězí deterministicky |
| Střed (46–72) | nejvyšší | beating interference + přechod strun + SNR/k_max nestabilita + EQ nerovnoměrnost |
| Výšky (73–108) | střední | Nyquist vítězí deterministicky, ale málo parciálů |

Střední register vyžaduje nejvyšší hustotu anchor tónů (6–8), bass a výšky postačí 3–4.

Specifické problémy středu:
- Beating (0.1–0.5 Hz) interferuje s bi-exp fitem — fitter zaměňuje beating koleno za τ1→τ2 přechod
- Přechod 1→2 strun není ostrý — τ2/τ1 a a1 se mění postupně, ne skokově
- SNR limit a k_max config se potkávají ve stejné oblasti → nestabilní n_partials
- Soundboard resonance 200–800 Hz → EQ biquads nejméně hladké přes klávesnici

---

## Anchor tóny — role ve fitting systému

Anchor tóny jsou referenční body pro kalibraci fyzikálního manifoldu. Quality score 0–9 se převede na fitting váhu:

```python
weight(score) = 0.1 + 0.9 * (score / 9)  # lineární mapování
# score 0 → váha 0.1 (ignorovat, ale nevyřadit)
# score 9 → váha 1.0 (plná důvěra)
```

Požadavky na anchor set pro spolehlivý fit:
- Pokrytí všech tří registrů (bass, střed, výšky)
- Pokrytí krajních velocity vrstev (vel 0–1 a vel 6–7)
- Ve středním registru: noty před i za přechodem strun
- Minimálně 10–15 anchor tónů pro celou klávesnici

---

## Fyzikální manifold — koncepce

Systém hledá nízkorozměrný podprostor v prostoru parametrů odpovídající fyzikálně smysluplným klavírním tónům. Extrakce produkuje body blízko manifoldu, ale s šumem.

Editor umožňuje:
1. **Identifikovat manifold** z anchor tónů (kde je uživatel přesvědčen o správnosti)
2. **Interpolovat cílový vektor** pro non-anchor noty z anchor pozic v (midi, vel) prostoru
3. **Blendovat** originální a cílový vektor s nastavitelnou tenzí

Dvě implementace manifoldu:
- **TensionManifold:** IDW interpolace přímo v parametrovém prostoru. Každý parametr se interpoluje nezávisle — jednoduché, ale nerespektuje korelace mezi parametry.
- **PCA Manifold:** interpolace v PCA latentním prostoru. Anchor vektory se zakódují do PCA koeficientů, ty se interpolují z anchor pozic, a dekódují zpět. Výsledek zachovává korelační strukturu: parametry se mění koordinovaně podle vztahů pozorovaných u anchorů.

Klíčový princip: **interpolace, ne projekce.** PCA se nepoužívá k projekci neznámého vektoru na subspace (to selhává pro body daleko od manifoldu), ale k interpolaci anchor koeficientů na pozici (midi, vel) dané noty. Výsledek je vždy konvexní kombinace anchor bodů na manifoldu.

---

## Od analýzy k implementaci — vazba na kód

Tato sekce ukazuje jak analytické koncepty odpovídají konkrétním třídám a rozhodnutím v kódu.

### Jak outlier skóre vzniká

`OutlierDetector` agreguje residuály ze čtyř zdrojů s pevnými váhami:

```
outlier_score(nota) = 0.30 × z(B_residual)
                    + 0.30 × z(damping_residual)
                    + 0.25 × z(spectral_shape_residual)
                    + 0.15 × z(velocity_model_residual)
```

kde `z(x)` je MAD-sigma z-score normalizovaný na [0, 1]. Výsledné skóre `0.0–1.0` je přímý vstup pro zbarvení `KeyboardMap`. Práh `> 0.5` odpovídá přibližně `2.5 MAD-sigma` od mediánu.

Váhy vyjadřují důvěryhodnost každého zdroje: B-curve a damping law jsou fyzikálně nejsilnější constrainty (každý 30 %), spektrální tvar je méně deterministický (25 %), velocity model má nejvyšší inherentní variance (15 %).

### Tři korekční metody

Systém nabízí tři nezávislé korekční pipeline. Každá produkuje `CorrectionSet`, uživatel vybírá korekce v DiffPreview.

**1. CorrectionEngine (fit-based)** — opravuje outlier noty na základě fyzikálních modelů z FitResult:

| Parametr | Podmínka opravy | Zdroj korekce |
|----------|----------------|--------------|
| `B` | residuál > threshold od B-curve | `10^(α·log10(f0)+β)` |
| `tau1_k{n}` | \|orig − spline\| / spline > 20% | cross-keyboard spline (fallback: damping law) |
| `tau2_k{n}` | odvozeno z tau1 | zachovat poměr τ2/τ1 z originálu |
| `attack_tau` | > 0.10s strop nebo > 2σ od trendu | power-law velocity model |
| `gamma_k{n}` | z-score > threshold od keyboard mediánu | medián γ_k přes MIDI |
| `beat_hz_k{n}` | z-score > threshold od keyboard mediánu | medián beat_hz per k |

`A0` se neopravuje přes CorrectionEngine — to je záležitost manifold metod.

**2. TensionManifold (anchor IDW)** — interpoluje „ideální" parametrový vektor z nejbližších anchor not. Koriguje všechny parametry (B, τ, A0, a1, beat_hz...) na základě vážené blízkosti v (midi, vel) prostoru. Omezení: interpoluje per-parametr nezávisle, nerespektuje korelace.

**3. PCA Manifold (anchor interpolace v latentním prostoru)** — fituje PCA na anchor vektory, interpoluje PCA koeficienty z anchor pozic v (midi, vel) prostoru, dekóduje zpět. Zachovává korelační strukturu: parametry se mění koordinovaně (např. B a tau1 spolu). Koriguje všechny parametry včetně A0.

### Proč fit probíhá per-nota, ne per-parametr globálně

Damping law a velocity model jsou per-nota fity — každá nota má vlastní `R`, `eta`, `gamma_k` hodnoty. Globální spline (přes `RelationFitter`) existuje pro vizualizaci a interpolaci, ale korekce vychází z per-nota fitu. Výjimka: B-curve je skutečně globální (jedna regresní přímka pro celou klávesnici), protože B je fyzikální vlastnost geometrie nástroje, ne jednotlivé struny.

### Proč anchor score 0 neznamená "vyřadit"

Váha `0.1` (ne `0.0`) pro score 0 je záměrné. Nota s score 0 přispívá do fitu s minimálním vlivem, ale je v datech přítomna. Kdybychom ji vyřadili, fitting by neměl informaci o tom že v daném MIDI regionu jsou problémové extrakce — a mohl by extrapolovat nesmyslné hodnoty do mezery.

---

## Matematické nástroje

| Problém | Metoda | Implementace | Paralelizace |
|---------|--------|--------------|--------------|
| B outlier detekce | MAD-sigma na log-log residuálech | `numpy.median` + vektorizace | — (88 bodů, ~0.1ms) |
| B-curve fit | Vážená lineární regrese (2 segmenty) | `numpy.linalg.lstsq` | — (vektorizace numpy) |
| B zlom auto-detekce | Grid search MIDI 35–60 | vlastní grid, `numpy` | — (~25 fitů) |
| Damping law | Per-nota lineární fit 1/τ vs f² | `scipy.stats.linregress` (váhovaný) | ThreadPool 14 vláken |
| Spektrální tvar | Per-k UnivariateSpline přes MIDI | `scipy.interpolate.UnivariateSpline` | ThreadPool per-k |
| Velocity model γ_k | Per-harmonický power-law fit | `scipy.optimize.curve_fit` | ThreadPool per-nota |
| attack_tau fit | Power-law z vel 4–7 + clamp | `scipy.optimize.curve_fit` | ThreadPool per-nota |
| Spline editace | Cubic spline s pevnými uzly | `scipy.interpolate.CubicSpline` | — (interaktivní, ~1ms) |
| Outlier agregace | Vážený součet z-score | `numpy` vektorizace | — (352 hodnot) |
| Tension manifold | IDW anchor interpolace v param prostoru | `numpy`, `math` | — (sekvenční) |
| PCA manifold | SVD + IDW interpolace v latentním prostoru | `numpy.linalg.svd` | — (sekvenční) |
