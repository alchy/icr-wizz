"""
models.py — datová schémata Piano Soundbank Editoru

Všechny datové třídy jsou Pydantic v2 modely.
Tento soubor nemá žádné závislosti mimo standardní knihovnu a pydantic.
Veškerý kód v projektu importuje datové typy odtud.

Status: scaffold — signatury a docstringy hotové, validační logika TODO

Changelog:
  2025-04-14 v0.1  — initial scaffold, základní schémata
  2025-04-14 v0.2  — přidány: CorrectionSet, EqBiquad, SpectralEq, StereoConfig
  2025-04-14 v0.3  — přidány: StringCount, FitSource enum, WsMessage, WsResponse
  2025-04-14 v0.4  — BCurveParams, DampingParams odděleny z FitResult
  2025-04-14 v0.5  — BankMetadata validator: < → <= (single-nota banka)
  2025-04-14 v0.6  — WsResponse: přidáno outlier_scores_per_vel pro velocity čtverečky
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class StringCount(int, Enum):
    """Počet strun per nota — fyzikální klasifikace registru."""
    BASS   = 1   # MIDI cca 21–38, ovinutá struna
    MID    = 2   # MIDI cca 39–47, dvě struny
    TREBLE = 3   # MIDI cca 48–108, tři struny


class CorrectionSource(str, Enum):
    """Zdroj navržené korekce — pro audit a filtrování v DiffPreview."""
    B_CURVE_FIT   = "b_curve_fit"
    DAMPING_LAW   = "damping_law"
    ANCHOR_INTERP = "anchor_interp"
    SPECTRAL_SHAPE= "spectral_shape"
    VELOCITY_MODEL= "velocity_model"
    MANUAL        = "manual"


class FitSource(str, Enum):
    """Typ fittingu použitého pro daný parametr."""
    EXTRACTED   = "extracted"    # přímo z extrakce, bez korekce
    CORRECTED   = "corrected"    # opraveno CorrectionEngine
    INTERPOLATED= "interpolated" # interpolováno z anchor not
    BORROWED    = "borrowed"     # spectral borrowing (forte→piano)


# ---------------------------------------------------------------------------
# Nízkoúrovňové parametrické typy
# ---------------------------------------------------------------------------

class PartialParams(BaseModel):
    """
    Parametry jednoho parciálu (harmoniku) klavírní noty.

    Bi-exponenciální model:
        A(t) = A0 * [a1 * exp(-t/tau1) + (1-a1) * exp(-t/tau2)]
        f_k  = k * f0 * sqrt(1 + B * k^2)
    """

    k: int = Field(..., ge=1, le=200, description="Harmonické číslo (1-based)")
    f_hz: float = Field(..., gt=0, description="Změřená frekvence parciálu [Hz]")
    A0: float = Field(..., ge=0, description="Počáteční amplituda")
    tau1: float = Field(..., gt=0, description="Rychlá časová konstanta decayu [s]")
    tau2: float = Field(..., gt=0, description="Pomalá časová konstanta decayu [s]")
    a1: float = Field(..., ge=0, le=1, description="Bi-exp blend (1.0 = mono-exp)")
    beat_hz: float = Field(0.0, ge=0, description="Frekvence beatingu mezi strunami [Hz]")
    beat_depth: float = Field(0.0, ge=0, le=1, description="Hloubka modulace beatingu")
    phi: float = Field(0.0, description="Počáteční fáze [rad]")
    fit_quality: float = Field(..., ge=0, le=1, description="1 - residual/total")

    @property
    def is_mono(self) -> bool:
        """True pokud a1 == 1.0 — single-exponential decay."""
        return self.a1 >= 0.999

    @property
    def tau_ratio(self) -> float:
        """tau2 / tau1 — ukazatel double-decay síly."""
        return self.tau2 / self.tau1

    @field_validator("tau2")
    @classmethod
    def tau2_gte_tau1(cls, v: float, info) -> float:
        # TODO: tau2 >= tau1 je fyzikálně požadováno, ale raw extrakce
        #       může vrátit tau2 < tau1 jako fit artefakt.
        #       Prozatím pouze varování, ne výjimka.
        return v


class EqBiquad(BaseModel):
    """
    Jeden biquad IIR filtr sekce (přímá forma II transponovaná).

    H(z) = (b0 + b1*z^-1 + b2*z^-2) / (1 + a1*z^-1 + a2*z^-2)

    Pydantic serializace je kompatibilní s formátem engine JSON:
        {"b": [b0, b1, b2], "a": [a1, a2]}
    """

    b: list[float] = Field(..., min_length=3, max_length=3)
    a: list[float] = Field(..., min_length=2, max_length=2)


class SpectralEq(BaseModel):
    """
    Spektrální EQ křivka (LTASE korekce) pro jednu notu.
    64 log-spaced bodů od 20 Hz do 20 kHz.
    """

    freqs_hz: list[float] = Field(..., min_length=64, max_length=64)
    gains_db: list[float] = Field(..., min_length=64, max_length=64)
    stereo_width_factor: float = Field(1.0, gt=0)

    @model_validator(mode="after")
    def lengths_match(self) -> SpectralEq:
        assert len(self.freqs_hz) == len(self.gains_db), \
            "freqs_hz a gains_db musí mít stejnou délku"
        return self


# ---------------------------------------------------------------------------
# Nota
# ---------------------------------------------------------------------------

class NoteParams(BaseModel):
    """
    Kompletní parametry jedné klavírní noty při jedné velocity vrstvě.

    Klíč v BankState.notes: "m{midi:03d}_vel{vel}"
    Příklad: "m060_vel4" = Middle C, mezzo-forte
    """

    midi: int = Field(..., ge=21, le=108)
    vel: int = Field(..., ge=0, le=7)

    # fundamentální fyzikální parametry
    f0_hz: float = Field(..., gt=0, description="Základní frekvence [Hz]")
    B: float = Field(..., gt=0, description="Inharmonicita: f_k = k*f0*sqrt(1+B*k^2)")

    # fázové parametry (generované při exportu)
    phi_diff: float = Field(0.0, description="Fázový offset [rad]")

    # šumová složka (attack impulz)
    attack_tau: float = Field(..., gt=0, description="Decay šumového impulzu [s]")
    A_noise: float = Field(..., ge=0, description="Amplituda šumu (RMS-relative)")
    noise_centroid_hz: float = Field(..., gt=0, description="Střed šumového pásma [Hz]")

    # gain
    rms_gain: float = Field(..., gt=0, description="RMS gain kalibrace")

    # strukturální informace
    n_strings: int = Field(1, ge=1, le=3, description="Počet strun (1=bass, 3=treble)")
    rise_tau: float = Field(0.004, gt=0, description="Náběhová časová konstanta [s]")

    # stereo
    stereo_width: float = Field(1.0, gt=0)
    pan_correction: float = Field(0.0, description="L/R korekce pan pozice")

    # parciály a EQ
    partials: list[PartialParams] = Field(default_factory=list)
    eq_biquads: list[EqBiquad] = Field(default_factory=list)
    spectral_eq: Optional[SpectralEq] = None

    @property
    def note_key(self) -> str:
        """Primární klíč pro BankState.notes dict."""
        return f"m{self.midi:03d}_vel{self.vel}"

    @property
    def n_partials(self) -> int:
        return len(self.partials)

    @property
    def string_count(self) -> StringCount:
        return StringCount(self.n_strings)

    def partial(self, k: int) -> Optional[PartialParams]:
        """Vrátí parciál s daným harmonickým číslem, nebo None."""
        for p in self.partials:
            if p.k == k:
                return p
        return None


# ---------------------------------------------------------------------------
# Banka
# ---------------------------------------------------------------------------

class BankMetadata(BaseModel):
    """Hlavička JSON banky — sekce 'metadata'."""

    instrument_name: str = Field("", description="Název nástroje")
    midi_range_from: int = Field(21, ge=0, le=127)
    midi_range_to: int = Field(108, ge=0, le=127)
    source: str = Field("soundbank:params")
    sr: int = Field(44100, description="Sample rate [Hz]")
    target_rms: float = Field(0.06, gt=0)
    vel_gamma: float = Field(0.7, gt=0, description="Velocity exponent pro RMS")
    k_max: int = Field(60, ge=1, le=200, description="Max parciálů v enginu")
    rng_seed: int = Field(0)
    duration_s: float = Field(3.0, gt=0)

    @model_validator(mode="after")
    def range_valid(self) -> BankMetadata:
        assert self.midi_range_from <= self.midi_range_to, \
            "midi_range_from musí být <= midi_range_to"
        return self


class StereoConfig(BaseModel):
    """Top-level stereo konfigurace banky."""

    keyboard_spread: float = Field(1.0, gt=0)
    pan_spread: float = Field(1.2886, gt=0)
    stereo_decorr: float = Field(0.5, ge=0, le=1)


class BankState(BaseModel):
    """
    Kompletní stav načtené banky v paměti editoru.

    notes dict používá note_key ("m060_vel4") jako klíč.
    source_path je cesta k originálnímu JSON souboru.
    is_modified == True pokud byla aplikována alespoň jedna korekce.
    """

    metadata: BankMetadata
    stereo_config: Optional[StereoConfig] = None
    notes: dict[str, NoteParams] = Field(default_factory=dict)

    source_path: str = ""
    is_modified: bool = False

    def get_note(self, midi: int, vel: int) -> Optional[NoteParams]:
        key = f"m{midi:03d}_vel{vel}"
        return self.notes.get(key)

    def midi_range(self) -> range:
        return range(
            self.metadata.midi_range_from,
            self.metadata.midi_range_to + 1
        )

    def velocity_layers(self, midi: int) -> list[NoteParams]:
        """Vrátí všechny velocity vrstvy pro danou MIDI notu, seřazené."""
        return sorted(
            [n for n in self.notes.values() if n.midi == midi],
            key=lambda n: n.vel
        )

    def note_count(self) -> int:
        return len(self.notes)


# ---------------------------------------------------------------------------
# Anchor databáze
# ---------------------------------------------------------------------------

class AnchorEntry(BaseModel):
    """
    Jeden záznam v anchor databázi.

    vel == -1 znamená "platí pro všechny velocity vrstvy" dané MIDI noty.
    score 0–9: 0 = ignorovat, 9 = plná důvěra jako referenční bod.
    """

    midi: int = Field(..., ge=21, le=108)
    vel: int = Field(..., ge=-1, le=7)
    score: float = Field(..., ge=0.0, le=9.0)
    note: Optional[str] = Field(None, description="Volitelná uživatelská poznámka")
    timestamp: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )

    @property
    def weight(self) -> float:
        """
        Převod score 0–9 na fitting váhu 0.1–1.0.
        score 0 → 0.1 (nevyřadit, ale minimální vliv)
        score 9 → 1.0 (plná váha)
        """
        return 0.1 + 0.9 * (self.score / 9.0)

    @property
    def note_key(self) -> str:
        if self.vel == -1:
            return f"m{self.midi:03d}_all"
        return f"m{self.midi:03d}_vel{self.vel}"


class AnchorDatabase(BaseModel):
    """
    Pojmenovaná databáze anchor not — "správných" extrakčních bodů.

    Uloženo jako: anchor-databases/{name}.json
    Nezávislá na konkrétním souboru banky — lze aplikovat
    na různé extrakce téhož nástroje.
    """

    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    created: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )
    modified: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )
    instrument_hint: Optional[str] = Field(
        None,
        description="Volný text — název nástroje pro orientaci uživatele"
    )
    entries: list[AnchorEntry] = Field(default_factory=list)

    def get_entry(self, midi: int, vel: int) -> Optional[AnchorEntry]:
        """Najde záznam pro danou notu+vel. vel=-1 = wildcard."""
        for e in self.entries:
            if e.midi == midi and (e.vel == vel or e.vel == -1):
                return e
        return None

    def coverage(self) -> dict[str, int]:
        """
        Počet anchor not v každém registru.
        Bass: MIDI 21–38, Mid: 39–60, Treble: 61–108
        """
        bass   = sum(1 for e in self.entries if 21 <= e.midi <= 38)
        mid    = sum(1 for e in self.entries if 39 <= e.midi <= 60)
        treble = sum(1 for e in self.entries if 61 <= e.midi <= 108)
        vel_lo = sum(1 for e in self.entries if e.vel in (0, 1))
        vel_hi = sum(1 for e in self.entries if e.vel in (6, 7))
        return {
            "bass": bass, "mid": mid, "treble": treble,
            "vel_low": vel_lo, "vel_high": vel_hi,
            "total": len(self.entries)
        }


# ---------------------------------------------------------------------------
# Fitting výsledky
# ---------------------------------------------------------------------------

class BCurveParams(BaseModel):
    """Parametry segmentované B(f0) regrese v log-log prostoru."""

    alpha_bass: float
    beta_bass: float
    alpha_treble: float
    beta_treble: float
    break_midi: int = Field(..., ge=30, le=70)
    residuals: dict[int, float] = Field(
        default_factory=dict,
        description="{midi: MAD-sigma residuál}"
    )


class DampingParams(BaseModel):
    """Damping law parametry per nota: 1/tau(k) = R + eta * f_k^2"""

    R: float = Field(..., description="Vnitřní tření struny")
    eta: float = Field(..., description="Frekvenčně závislý odpor")
    residuals: dict[str, float] = Field(
        default_factory=dict,
        description="{note_key: sigma od fitu}"
    )


class FitResult(BaseModel):
    """
    Kompletní výsledek RelationFitter.fit_all().

    Obsahuje fitted parametry, residuály a outlier skóre per nota.
    Frontend používá outlier_scores přímo pro zbarvení KeyboardMap.
    """

    # B-curve
    b_curve: Optional[BCurveParams] = None

    # damping law per nota: {midi: DampingParams}
    damping: dict[int, DampingParams] = Field(default_factory=dict)

    # spektrální tvar: residuály v dB per nota+vel
    shape_residuals: dict[str, float] = Field(default_factory=dict)

    # velocity modely per nota
    gamma_k: dict[int, list[float]] = Field(
        default_factory=dict,
        description="{midi: list[γ_k] délky k_max}"
    )
    attack_alpha: dict[int, float] = Field(default_factory=dict)
    attack_tref: dict[int, float] = Field(default_factory=dict)

    # outlier skóre: 0.0 = v normě, 1.0 = silný outlier
    outlier_scores: dict[str, float] = Field(
        default_factory=dict,
        description="{note_key: score}"
    )

    # meta
    fit_timestamp: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )
    anchor_db_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Korekce
# ---------------------------------------------------------------------------

class Correction(BaseModel):
    """
    Jedna navržená nebo aplikovaná korekce parametru.

    Immutable — nová korekce vzniká kopií, nikdy modifikací.
    """

    midi: int
    vel: int
    field: str = Field(
        ...,
        description="Název pole: 'B' | 'tau1_k3' | 'A0_k5' | 'attack_tau' | ..."
    )
    original: float
    corrected: float
    source: CorrectionSource
    delta_pct: float = Field(0.0)

    @model_validator(mode="after")
    def compute_delta(self) -> Correction:
        if self.original != 0:
            object.__setattr__(
                self, "delta_pct",
                100.0 * (self.corrected - self.original) / abs(self.original)
            )
        return self

    @property
    def note_key(self) -> str:
        return f"m{self.midi:03d}_vel{self.vel}"

    model_config = {"frozen": True}


class CorrectionSet(BaseModel):
    """
    Sada korekcí z jednoho průběhu CorrectionEngine — jeden undo checkpoint.
    """

    corrections: list[Correction] = Field(default_factory=list)
    created: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )
    description: str = ""
    anchor_db_name: Optional[str] = None

    def filter_by_source(self, source: CorrectionSource) -> list[Correction]:
        return [c for c in self.corrections if c.source == source]

    def filter_by_midi(self, midi: int) -> list[Correction]:
        return [c for c in self.corrections if c.midi == midi]

    def summary(self) -> dict:
        total = len(self.corrections)
        midis = {c.midi for c in self.corrections}
        max_delta = max((abs(c.delta_pct) for c in self.corrections), default=0.0)
        return {
            "total_corrections": total,
            "affected_notes": len(midis),
            "max_delta_pct": round(max_delta, 2),
        }


# ---------------------------------------------------------------------------
# API request / response modely
# ---------------------------------------------------------------------------

class LoadBankRequest(BaseModel):
    paths: list[str] = Field(..., min_length=1)


class FitRequest(BaseModel):
    anchor_db_name: Optional[str] = None
    sigma_threshold: float = Field(2.5, gt=0)
    break_midi_override: Optional[int] = Field(None, ge=30, le=70)


class ExportRequest(BaseModel):
    source_path: str
    output_path: str
    diff_only: bool = False
    add_metadata: bool = True
    correction_set_id: Optional[str] = None


class WsMessage(BaseModel):
    """Příchozí WebSocket zpráva pro live preview."""

    action: str  # "update_anchor" | "move_spline_node" | "drag_gamma_k"
    payload: dict


class WsResponse(BaseModel):
    """Odchozí WebSocket zpráva — aktualizace vizualizace."""

    # Agregované na prefix "m060" — pro výšku sloupce KeyboardMap
    outlier_scores: dict[str, float] = Field(default_factory=dict)
    # Per note_key "m060_vel4" — pro velocity čtverečky pod klávesami
    outlier_scores_per_vel: dict[str, float] = Field(default_factory=dict)
    spline_points: list[list[float]] = Field(default_factory=list)
    fit_quality: float = 1.0
    error: Optional[str] = None
