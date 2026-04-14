# Piano Soundbank Editor — implementační specifikace modulů

> Verze: 0.1-draft  
> Stav: pracovní dokument, průběžně aktualizováno

> Changelog:  
> 2025-04-14 v0.1  — initial draft  
> 2025-04-14 v0.2  — konvence: logging + paralelizace pravidla  
> 2025-04-14 v0.3  — logger.py: nová sekce na začátku dokumentu  
> 2025-04-14 v0.4  — models.py: Pydantic v2 schémata, nové třídy (CorrectionSet, Ws*)  
> 2025-04-14 v0.5  — signatury: fit(bank,weights), propose→CorrectionSet, load_multiple tuple  
> 2025-04-14 v0.6  — BankExporter: paralelní serializace, _editor_metadata pole  
> 2025-04-14 v0.7  — main.py: singleton+executor vzor, uvloop, WS session_state

---

## Konvence

- Každý modul je samostatný Python soubor s jednou hlavní třídou
- Všechny veřejné metody jsou typované (Pydantic v2 modely pro I/O)
- Fitting metody jsou deterministické: stejný vstup = stejný výstup
- Logging: každá třída má `_log = get_logger(__name__, cls="ClassName")`, každá veřejná metoda používá `@log_operation` nebo `OperationLogger`
- Paralelizace: CPU-bound operace → `ProcessPoolExecutor`, I/O-bound a scipy/numpy → `ThreadPoolExecutor` (GIL-friendly), počty workers přes env proměnné
- Žádné globální proměnné, žádný sdílený stav mimo explicitní datové třídy
- Top-level funkce pro `ProcessPoolExecutor` musí být pickle-serializovatelné (definovány na úrovni modulu, ne jako metody)
- Testy: každý modul má odpovídající `test_{module}.py` s alespoň smoke testem

---

## logger.py — centrální logging

**Odpovědnost:** sdílená logging infrastruktura bez externích závislostí.

```python
# Import vzor pro každý modul
from logger import get_logger, log_operation, OperationLogger

# Třída-level logger
class BankLoader:
    _log = get_logger(__name__, cls="BankLoader")

    # Jednoduchá metoda — automatický log vstupu, výstupu, výjimek
    @log_operation("load")
    def load(self, path: str) -> BankState: ...

    # Složitá operace — průběžné stavy
    def load_multiple(self, paths):
        with OperationLogger(self._log, "load_multiple",
                             input={"count": len(paths)}) as op:
            op.progress("krok hotov", detail=value)
            op.warn("problém", reason=str(e))
            op.set_output({"loaded": n})
```

**Výstupní formát** přepínatelný env proměnnou `LOG_FORMAT`:
- `console` (default): čitelný formát s barvami, `HH:MM:SS.mmm | LEVEL | module.Class.method`
- `json`: strukturovaný JSON per řádek pro log agregátory

**Úroveň** přes `LOG_LEVEL` (default `DEBUG`), verbozita přes `LOG_VERBOSE=0`.

---

## models.py — datová schémata

Pydantic v2. Kompletní seznam tříd:

```python
# Enums
class StringCount(int, Enum): BASS=1, MID=2, TREBLE=3
class CorrectionSource(str, Enum): B_CURVE_FIT, DAMPING_LAW, ANCHOR_INTERP,
                                    SPECTRAL_SHAPE, VELOCITY_MODEL, MANUAL
class FitSource(str, Enum): EXTRACTED, CORRECTED, INTERPOLATED, BORROWED

# Parametrické typy
class PartialParams(BaseModel):
    k: int                   # harmonické číslo (1-based)
    f_hz: float              # změřená frekvence [Hz]
    A0: float                # počáteční amplituda
    tau1: float              # rychlá časová konstanta [s]
    tau2: float              # pomalá časová konstanta [s]
    a1: float                # bi-exp blend (1.0 = mono-exp)
    beat_hz: float           # frekvence beatingu [Hz]
    beat_depth: float        # hloubka beatingu [0–1]
    phi: float               # počáteční fáze [rad]
    fit_quality: float       # 1 - residual/total [0–1]
    # property: is_mono, tau_ratio

class EqBiquad(BaseModel):
    b: list[float]           # [b0, b1, b2]
    a: list[float]           # [a1, a2]

class SpectralEq(BaseModel):
    freqs_hz: list[float]    # 64 log-spaced bodů 20–20000 Hz
    gains_db: list[float]    # 64 hodnot zisku
    stereo_width_factor: float

# Nota
class NoteParams(BaseModel):
    midi: int                # 21–108
    vel: int                 # 0–7
    f0_hz: float
    B: float                 # inharmonicita
    phi_diff: float
    attack_tau: float        # decay šumového impulzu [s]
    A_noise: float           # amplituda šumu
    noise_centroid_hz: float
    rms_gain: float
    n_strings: int           # 1/2/3
    rise_tau: float
    stereo_width: float
    pan_correction: float
    partials: list[PartialParams]
    eq_biquads: list[EqBiquad]
    spectral_eq: Optional[SpectralEq]
    # property: note_key, n_partials, string_count, partial(k)

# Banka
class BankMetadata(BaseModel):
    instrument_name: str
    midi_range_from: int
    midi_range_to: int
    sr: int                  # sample rate [Hz]
    target_rms: float
    vel_gamma: float
    k_max: int               # max parciálů
    rng_seed: int
    duration_s: float

class StereoConfig(BaseModel):
    keyboard_spread: float
    pan_spread: float
    stereo_decorr: float

class BankState(BaseModel):
    metadata: BankMetadata
    stereo_config: Optional[StereoConfig]
    notes: dict[str, NoteParams]  # klíč: "m060_vel4"
    source_path: str
    is_modified: bool
    # property/methods: get_note(midi,vel), midi_range(), velocity_layers(midi), note_count()

# Anchor databáze
class AnchorEntry(BaseModel):
    midi: int
    vel: int                 # -1 = všechny velocity vrstvy
    score: float             # 0.0–9.0
    note: Optional[str]
    timestamp: str
    # property: weight (score → 0.1–1.0), note_key

class AnchorDatabase(BaseModel):
    name: str
    description: Optional[str]
    created: str
    modified: str
    instrument_hint: Optional[str]
    entries: list[AnchorEntry]
    # methods: get_entry(midi,vel), coverage()

# Fitting výsledky
class BCurveParams(BaseModel):
    alpha_bass: float
    beta_bass: float
    alpha_treble: float
    beta_treble: float
    break_midi: int
    residuals: dict[int, float]    # {midi: MAD-sigma residuál}

class DampingParams(BaseModel):
    R: float                       # vnitřní tření
    eta: float                     # frekvenčně závislý odpor
    residuals: dict[int, float]    # {k: sigma}

class FitResult(BaseModel):
    b_curve: Optional[BCurveParams]
    damping: dict[int, DampingParams]        # {midi: params}
    damping_spline: dict[str, float]         # {"k{k}_m{midi}_v{vel}": predicted_inv_tau1}
    shape_residuals: dict[str, float]        # {note_key: dB}
    gamma_k: dict[int, list[float]]          # {midi: [γ_k] * k_max}
    attack_alpha: dict[int, float]
    attack_tref: dict[int, float]
    outlier_scores: dict[str, float]         # {note_key: 0.0–1.0}
    fit_timestamp: str
    anchor_db_name: Optional[str]

# Korekce
class Correction(BaseModel):
    midi: int
    vel: int
    field: str               # "B" | "tau1_k3" | "A0_k5" | "attack_tau"
    original: float
    corrected: float
    source: CorrectionSource
    delta_pct: float         # auto-computed v model_validator
    # property: note_key
    # model_config: frozen=True (immutable)

class CorrectionSet(BaseModel):
    corrections: list[Correction]
    created: str
    description: str
    anchor_db_name: Optional[str]
    # methods: filter_by_source(), filter_by_midi(), summary()

# API typy
class LoadBankRequest(BaseModel):
    paths: list[str]

class FitRequest(BaseModel):
    anchor_db_name: Optional[str]
    sigma_threshold: float       # default 2.5
    break_midi_override: Optional[int]

class ExportRequest(BaseModel):
    source_path: str
    output_path: str
    diff_only: bool
    add_metadata: bool
    correction_set_id: Optional[str]

class WsMessage(BaseModel):
    action: str              # "update_anchor" | "move_spline_node" | "drag_gamma_k"
    payload: dict

class WsResponse(BaseModel):
    outlier_scores: dict[str, float]
    spline_points: list[list[float]]
    fit_quality: float
    error: Optional[str]
```

---

## bank_loader.py — BankLoader

**Odpovědnost:** načíst jeden nebo více JSON souborů banky, validovat schéma, vrátit `BankState`.

**Paralelizace:** `ThreadPoolExecutor` pro I/O (list_banks, load_multiple), `ProcessPoolExecutor` pro CPU-bound Pydantic validaci not (práh: ≥ 88 not).

```python
class BankLoader:
    def __init__(self,
                 io_workers: int = _IO_WORKERS,    # default: min(cpu, 12)
                 cpu_workers: int = _CPU_WORKERS,  # default: cpu - 1
                 progress_cb: Optional[Callable] = None): ...

    def list_banks(self, directory: str,
                   recursive: bool = False) -> list[BankFileInfo]:
        """ThreadPool peek metadat — vrátí BankFileInfo seznam."""

    def load(self, path: str) -> BankState:
        """Načte jeden soubor. Parsování not: ProcessPool pokud ≥ 88 not."""

    def load_multiple(self, paths: list[str]
                      ) -> tuple[dict[str, BankState], list[BankLoadError]]:
        """ThreadPool — vrátí (dict {path: BankState}, list chyb)."""

# Top-level pro ProcessPoolExecutor (pickle-kompatibilní)
def _parse_note_chunk(
    chunk: list[tuple[str, dict]], path: str
) -> tuple[dict[str, NoteParams], list[BankValidationWarning]]: ...
```

**Normalizace klíčů:** `m60_vel4`, `m060-vel4`, `m060_vel4` → vždy `m060_vel4`.

---

## relation_fitter.py — RelationFitter

**Odpovědnost:** fitovat fyzikální vztahy Typ A a Typ B, agregovat outlier skóre.

**Paralelizace:** každý plugin interně paralelizuje; `fit_all()` spouští pluginy sekvenčně (výstupy na sebe nezávisí, ale sekvenční pořadí zaručuje deterministické výsledky).

```python
class FitPlugin(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def fit(self, bank: BankState, weights: dict[str, float]) -> dict:
        """Vrátí partial dict kompatibilní s FitResult poli."""

class BCurveFitter(FitPlugin):
    """Numpy vektorizace — žádný threading. ~2ms pro 88 not."""
    def __init__(self, break_midi: Optional[int] = None,
                 sigma_threshold: float = 2.5): ...
    def fit(self, bank: BankState, weights: dict) -> dict: ...
    def predict_B(self, params: BCurveParams, midi: int) -> float: ...

class DampingLawFitter(FitPlugin):
    """ThreadPoolExecutor per-nota. ~30ms pro 704 not s 14 vlákny."""
    def __init__(self, min_quality: float = 0.7,
                 sigma_threshold: float = 3.0,
                 workers: int = _FIT_WORKERS): ...
    def fit(self, bank: BankState, weights: dict) -> dict: ...
    def predict_tau(self, params: DampingParams, f_hz: float) -> float: ...

class SpectralShapeFitter(FitPlugin):
    """ThreadPoolExecutor per-k spline. vel_range=(4,7) pro forte vrstvy."""
    def __init__(self, vel_range: tuple[int,int] = (4,7),
                 workers: int = _FIT_WORKERS): ...
    def fit(self, bank: BankState, weights: dict) -> dict: ...
    def predict(self, midi: int, k: int, n_partials: int) -> float: ...

class VelocityModelFitter(FitPlugin):
    """ThreadPoolExecutor per-nota. Fituje z vel 4–7, extrapoluje dolů."""
    ATTACK_TAU_CAP: float = 0.10
    def __init__(self, fit_vel_range: tuple[int,int] = (4,7),
                 workers: int = _FIT_WORKERS): ...
    def fit(self, bank: BankState, weights: dict) -> dict: ...
    def predict_A0(self, gamma_k, A0_ref, vel, vel_ref=4) -> float: ...
    def predict_attack_tau(self, tau_ref, alpha, vel, vel_ref=4) -> float: ...

class RelationFitter:
    OUTLIER_WEIGHTS = {"b_curve": 0.30, "damping": 0.30,
                       "spectral": 0.25, "velocity": 0.15}

    def __init__(self, plugins: Optional[list[FitPlugin]] = None,
                 sigma_threshold: float = 2.5): ...

    def fit_all(self, bank: BankState,
                anchor_db: Optional[AnchorDatabase] = None) -> FitResult:
        """Spustí pluginy sekvenčně, agreguje FitResult."""

    def anchor_weights(self, bank: BankState,
                       anchor_db: Optional[AnchorDatabase]
                       ) -> dict[str, float]:
        """None anchor_db → uniform 1.0. Jinak deleguje na AnchorManager."""
```

---

## outlier_detector.py — OutlierDetector

**Odpovědnost:** detekovat anomální noty z FitResult residuálů, vrátit `OutlierReport`.

**Paralelizace:** numpy vektorizace — žádný threading (88 not × 4 zdroje = 352 hodnot, ~1ms).

```python
class OutlierFlag:
    source: str       # "b_curve" | "damping_law" | "spectral_shape" | "velocity_model"
    sigma: float      # počet MAD-sigma od mediánu
    description: str

class OutlierReport:
    scores: dict[str, float]             # {note_key: 0.0–1.0}
    flags:  dict[str, list[OutlierFlag]] # {note_key: [důvody]}
    threshold: float
    # methods: outlier_notes(), summary()

class OutlierDetector:
    SOURCE_WEIGHTS = {"b_curve": 0.30, "damping_law": 0.30,
                      "spectral_shape": 0.25, "velocity_model": 0.15}

    def __init__(self, sigma_threshold: float = 2.5): ...

    def detect(self, fit: FitResult) -> OutlierReport:
        """Vážená agregace residuálů přes zdroje, normalizace 0–1."""

    def mad_sigma(self, values: list[float]) -> tuple[float, float]:
        """(median, 1.4826 * MAD)"""

    def _score_from_residuals(self, residuals: dict[str, float],
                               source_name: str) -> dict[str, float]:
        """MAD-sigma z-score → normalizované skóre per nota."""
```

---

## anchor_manager.py — AnchorManager

**Odpovědnost:** CRUD operace nad AnchorDatabase, persistence, konverze score na váhy.

**Paralelizace:** sekvenční — anchor DB je malá (desítky záznamů), overhead by převýšil zisk.

```python
COVERAGE_THRESHOLDS = {
    "bass": 3, "mid": 6, "treble": 3,   # min. anchor not per region
    "vel_low": 2, "vel_high": 2,          # pp/ff pokrytí
    "total": 10,
}

class AnchorManager:
    def __init__(self, anchor_dir: str = "anchor-databases"): ...

    # CRUD — vše vrací novou instanci (immutable pattern)
    def create(self, name: str, description: str = None,
               instrument_hint: str = None) -> AnchorDatabase: ...
    def add_entry(self, db: AnchorDatabase, midi: int, vel: int,
                  score: float, note: str = None) -> AnchorDatabase:
        """vel=-1 → wildcard pro všechny velocity vrstvy noty."""
    def remove_entry(self, db: AnchorDatabase,
                     midi: int, vel: int) -> AnchorDatabase: ...
    def clear(self, db: AnchorDatabase) -> AnchorDatabase: ...

    # Persistence
    def save(self, db: AnchorDatabase,
             overwrite: bool = True) -> Path:
        """Uloží do {anchor_dir}/{name}.json. Aktualizuje modified."""
    def load(self, name_or_path: str) -> AnchorDatabase: ...
    def list_databases(self) -> list[dict]: ...
    def delete(self, name: str) -> bool: ...

    # Export/import
    def export_json(self, db: AnchorDatabase, indent: int = 2) -> str: ...
    def import_json(self, json_str: str) -> AnchorDatabase: ...

    # Konverze pro RelationFitter
    def to_weights(self, db: AnchorDatabase,
                   bank: BankState) -> dict[str, float]:
        """score 0–9 → weight 0.1–1.0. Noty bez záznamu: 1.0."""

    def coverage_report(self, db: AnchorDatabase,
                        bank: Optional[BankState] = None) -> dict:
        """{bass, mid, treble, vel_low, vel_high, total, warnings, ok}"""

    def suggest_anchors(self, bank: BankState,
                        existing_db: Optional[AnchorDatabase] = None,
                        n_suggestions: int = 15) -> list[dict]:
        """Navrhne anchor noty pro rovnoměrné pokrytí klávesnice."""
```

**Formát anchor-databases/{name}.json:**

```json
{
  "name": "ks-grand-recording-v1",
  "description": "Steinway D, studio recording, 2024",
  "created": "2024-11-01T14:32:00",
  "modified": "2024-11-03T09:15:00",
  "instrument_hint": "ks-grand",
  "entries": [
    {"midi": 21, "vel": -1, "score": 8.0, "note": "bass A0 - čistý decay"},
    {"midi": 60, "vel": 4,  "score": 9.0, "note": "middle C mf - reference"},
    {"midi": 60, "vel": 0,  "score": 6.0, "note": "middle C pp - slabší SNR"},
    {"midi": 84, "vel": 6,  "score": 7.0, "note": "C6 ff - ok"}
  ]
}
```

---

## correction_engine.py — CorrectionEngine

**Odpovědnost:** navrhnout a aplikovat fit-based korekce na základě FitResult. Toto je jedna ze tří korekčních metod (viz tension_manifold.py a pca_manifold.py).

**Paralelizace:** `propose()` ThreadPool per-nota (nezávislé). `apply()` sekvenční (deep copy + field patch < 10ms pro 704 not).

```python
class CorrectionEngine:
    DEFAULT_WEIGHTS = {"b_curve": 1.0, "tau": 1.0, "attack_tau": 1.0,
                       "gamma_k": 1.0, "beating": 1.0}

    def __init__(self, outlier_threshold: float = 2.5,
                 min_delta_pct: float = 0.5,
                 note_workers: int = _NOTE_WORKERS,
                 correction_weights: Optional[dict[str, float]] = None,
                 tau_spline_threshold: float = 0.20): ...

    def propose(self, bank: BankState, fit: FitResult,
                anchor_weights: Optional[dict] = None) -> CorrectionSet:
        """ThreadPool per-nota. Fáze 1: outlier noty, Fáze 2: spline tau pro všechny."""

    def apply(self, bank: BankState, correction_set: CorrectionSet,
              selected_fields: Optional[list[str]] = None) -> BankState:
        """Immutable — vrátí nový BankState, is_modified=True."""

    def diff(self, original: BankState,
             corrected: BankState) -> CorrectionSet:
        """Porovná dvě banky, vrátí CorrectionSet pro DiffPreview."""
```

**Korekční strategie per typ:**

| Parametr | Podmínka opravy | Zdroj korekce | CorrectionSource |
|----------|----------------|--------------|------------------|
| `B` | residuál > threshold od B-curve | `10^(α·log10(f0)+β)` | B_CURVE_FIT |
| `tau1_k{n}` | \|orig − spline_pred\| / pred > tau_spline_threshold | cross-keyboard spline (fallback: damping law) | DAMPING_LAW |
| `tau2_k{n}` | odvozeno z tau1 korekce | zachovat poměr τ2/τ1 z originálu | DAMPING_LAW |
| `attack_tau` | > 0.10s strop nebo > 2σ od trendu | power-law velocity model s cap 0.10s | VELOCITY_MODEL |
| `gamma_k{n}` | z-score > outlier_threshold od keyboard mediánu | medián γ_k přes MIDI per harmonik | SPECTRAL_SHAPE |
| `beat_hz_k{n}` | z-score > outlier_threshold od keyboard mediánu | medián beat_hz přes MIDI per k | SPECTRAL_SHAPE |

`A0` se neopravuje přes CorrectionEngine (manifold metody A0 korigují).

`correction_weights` umožňuje blendovat korekce per typ (0.0 = ignoruj, 1.0 = plná korekce).

**`field` formáty:** `"B"`, `"tau1_k3"`, `"tau2_k3"`, `"attack_tau"`, `"gamma_k5"`, `"beat_hz_k3"`

---

## tension_manifold.py — TensionManifold

**Odpovědnost:** anchor-based korekce interpolací v parametrovém prostoru. Pro každou non-anchor notu interpoluje „ideální" parametrový vektor z nejbližších anchorů.

**Paralelizace:** sekvenční — výpočet per nota je rychlý (~1ms), overhead threadingu by převýšil zisk.

```python
def propose_tension_corrections(
    bank: BankState,
    anchor_db: AnchorDatabase,
    tension: float = 0.5,       # 0.0 = žádná korekce, 1.0 = plná projekce
    falloff: float = 12.0,      # Gaussova šířka v půltónech (12 = oktáva)
    min_delta_pct: float = 1.0,
    max_delta_pct: float = 200.0,
    k_max: int = 60,
    n_neighbors: int = 8,
) -> CorrectionSet: ...
```

**Algoritmus:**
1. Expanduj anchor entries (vel=-1 → 8 velocity vrstev)
2. Pre-compute anchor parametrové vektory
3. Pro každou non-anchor notu:
   a. Najdi top-N sousedů: `weight = gauss(Δmidi, falloff) × score_weight × vel_penalty`
   b. Interpoluj: vážený průměr parametrových vektorů (per-parametr nezávisle)
   c. Blend: log-space pro multiplikativní parametry (B, τ, A0), lineární pro ostatní
   d. Clamp extrémní korekce na ±max_delta_pct

**Omezení:** interpoluje každý parametr nezávisle — nerespektuje korelace mezi parametry. PCA manifold tento problém řeší.

---

## pca_manifold.py — PCA Manifold

**Odpovědnost:** anchor-based korekce interpolací v PCA latentním prostoru. Zachovává korelační strukturu mezi parametry.

**Paralelizace:** sekvenční (numpy vektorizace pro SVD, IDW per nota ~0.5ms).

```python
class PCACorrector:
    def __init__(self,
                 n_components: float = 0.95,  # zachovej 95% variance
                 tension: float = 0.5,
                 min_delta_pct: float = 1.0,
                 max_delta_pct: float = 200.0,
                 k_max: int = 30): ...

    def fit(self, bank: BankState, anchor_db: AnchorDatabase) -> dict: ...
    def interpolate(self, midi: int, vel: int) -> np.ndarray: ...
    def propose(self, bank: BankState, anchor_db: AnchorDatabase) -> CorrectionSet: ...

def propose_pca_corrections(bank, anchor_db, ...) -> CorrectionSet:
    """Convenience: fit + propose v jednom."""
```

**Pipeline:**
1. **Fit:** extrahuj anchor vektory v log prostoru → z-score → SVD → zachovej n komponent
2. **Encode anchory:** uloží (midi, vel) → PCA koeficienty pro každý anchor
3. **Interpolace:** pro non-anchor notu IDW v (midi, vel) prostoru → vážený průměr anchor koeficientů → decode
4. **Blend:** log-space pro multiplikativní parametry, lineární pro ostatní

**Metrika vzdálenosti:** `d² = (Δmidi/12)² + (Δvel)²` — 1 oktáva ≈ 1 velocity krok.

**Klíčový rozdíl oproti Tension:** interpolace probíhá v PCA latentním prostoru. Decoded výsledek zachovává korelace mezi parametry (např. B a tau1 se mění koordinovaně). Tension interpoluje per-parametr nezávisle — může produkovat kombinace, které neodpovídají žádnému reálnému anchoru.

**Log-space parametry:** B, rms_gain, attack_tau, A_noise, A0, tau1, tau2 — transformovány logaritmem před PCA (striktně kladné, multiplikativní povahy)

---

## bank_exporter.py — BankExporter

**Odpovědnost:** exportovat opravenou banku do engine-kompatibilního JSON.

**Paralelizace:** `_serialize_notes_parallel()` ThreadPool ≥ 100 not. `json.dump()` sekvenční (I/O). CSV report sekvenční.

```python
class BankExporter:
    def __init__(self, workers: int = _EXPORT_WORKERS): ...

    def export(self, bank: BankState, output_path: str,
               diff_only: bool = False,
               original_bank: Optional[BankState] = None,
               add_metadata: bool = True,
               correction_set: Optional[CorrectionSet] = None,
               indent: int = 2) -> Path:
        """Vrátí absolutní Path výstupního souboru."""

    def export_diff_report(self, correction_set: CorrectionSet,
                           output_path: str) -> Path:
        """CSV audit report. Sloupce: nota, vel, note_name, parametr,
           original, opraveno, zdroj, delta_pct."""
```

**`_editor_metadata` sekce v exportovaném JSON:**

```json
"_editor_metadata": {
  "editor_version": "0.1",
  "export_timestamp": "2024-11-03T09:20:00",
  "source_path": "soundbanks/ks-grand-f44.json",
  "is_modified": true,
  "anchor_db_name": "ks-grand-recording-v1",
  "corrections_count": 47,
  "affected_notes": 12,
  "max_delta_pct": 8.3
}
```

---

## midi_bridge.py — MidiBridge

**Odpovědnost:** komunikace se syntetizérem přes MIDI/SysEx.

**Paralelizace:** sekvenční — SysEx protokol vyžaduje sekvenční zpracování zpráv.

> SysEx protokol cílového syntetizéru bude doplněn v samostatné specifikaci.

```python
class MidiBridge:
    def __init__(self, response_timeout_ms: int = 500): ...

    # Port management
    def list_ports(self) -> list[str]: ...
    def connect(self, port_name: str) -> None:
        """Raises MidiConnectionError pokud port neexistuje."""
    def disconnect(self) -> None: ...  # idempotentní
    @property
    def is_connected(self) -> bool: ...

    # Diagnostika
    def send_identity_request(self) -> dict:
        """Universal SysEx Identity Request F0 7E 7F 06 01 F7."""

    # Patch operace
    def patch_note(self, midi: int, vel: int, note_params: NoteParams,
                   progress_callback: Optional[Callable] = None
                   ) -> PatchResult: ...

    def patch_bank(self, bank: BankState,
                   midi_range: Optional[tuple[int,int]] = None,
                   vel_range:  Optional[tuple[int,int]] = None,
                   progress_callback: Optional[Callable] = None
                   ) -> BankPatchResult:
        """Sekvenční batch. Vrátí {total, success, failed, errors}."""
```

**Závislost:** `python-rtmidi` pro přímý přístup k portům bez závislosti na DAW.

---

## main.py — FastAPI aplikace

**Async architektura:** FastAPI + uvicorn na asyncio event loop. CPU-bound operace (fit, load) jsou offloadovány do `ThreadPoolExecutor` přes `run_in_executor()` aby neblokovaly event loop. WS preview má 100ms hard timeout.

**Doporučené spuštění pro M4:**
```bash
uvicorn main:app --workers 1 --loop uvloop --port 8000
# Jeden worker — fitting využívá vlastní thread/process pool interně.
```

```python
# Sdílené singleton služby (bezstavové — nenesou stav session)
loader   = BankLoader()
fitter   = RelationFitter()
engine   = CorrectionEngine()
exporter = BankExporter()
mgr      = AnchorManager(ANCHOR_DIR)
bridge   = MidiBridge()

# Offload CPU operací z event loop
_API_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="api-worker")

async def _run_blocking(fn, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_API_EXECUTOR, lambda: fn(*args, **kwargs))
```

**WebSocket `/ws/preview` — session state:**
```python
session_state = {
    "bank":      None,   # aktuálně editovaná banka
    "anchor_db": None,   # načtená anchor DB
    "last_fit":  None,   # poslední FitResult
}
```

**Příchozí zprávy:**
```json
{"action": "update_anchor",   "payload": {"midi": 60, "vel": 4, "score": 8}}
{"action": "move_spline_node","payload": {"fitter": "b_curve", "break_midi": 49}}
{"action": "drag_gamma_k",    "payload": {"midi": 60, "k": 5, "gamma": 0.72}}
```

**Odchozí zprávy (< 100ms, timeout vrátí error):**
```json
{"outlier_scores": {"m060_vel4": 0.12}, "spline_points": [...],
 "fit_quality": 0.94, "error": null}
```

---

## requirements.txt

```
fastapi>=0.111
uvicorn[standard]>=0.29
pydantic>=2.6
numpy>=1.26
scipy>=1.12
python-rtmidi>=1.5
websockets>=12.0
uvloop>=0.19          # doporučeno pro M4 (Apple Silicon)
```

## package.json (frontend klíčové závislosti)

```json
{
  "dependencies": {
    "react": "^18",
    "typescript": "^5",
    "zustand": "^4",
    "plotly.js": "^2",
    "react-plotly.js": "^2",
    "@vitejs/plugin-react": "^4"
  }
}
```
