"""
rbf_surface.py — RBF surface korekce parametrů

Anchor noty definují kontrolní body v (midi, vel) prostoru.
RBF (Radial Basis Function) fituje hladké plochy přes tyto body
a interpoluje parametrové vektory pro non-anchor noty.

Na rozdíl od IDW (Tension, PCA) vytváří globální model tvaru
variace — povrch, ne bodový odhad.

Pipeline:
  1. Extrahuj parametrové vektory z anchor not (log prostor)
  2. Sestav pozice X (midi/12, vel) a hodnoty Y (log vektory)
  3. Fituj RBFInterpolator (thin_plate_spline default)
  4. Pro každou non-anchor notu:
     a. Evaluuj RBF surface na (midi, vel)
     b. corrected = original + tension × (surface - original)
"""

from __future__ import annotations

import numpy as np
from typing import Optional

from logger import get_logger, OperationLogger
from models import (
    AnchorDatabase, BankState, Correction,
    CorrectionSet, CorrectionSource, NoteParams,
)


_log = get_logger(__name__, cls="RBFSurface")

# Parametry per nota pro vektorizaci
SCALAR_PARAMS = ["B", "rms_gain", "attack_tau", "A_noise", "noise_centroid_hz"]
PARTIAL_PARAMS = ["A0", "tau1", "tau2", "a1", "beat_hz"]

# Parametry které se transformují do log prostoru (striktně kladné, multiplikativní)
LOG_PARAMS = {"B", "rms_gain", "attack_tau", "A_noise", "A0", "tau1", "tau2"}

# Metrika vzdálenosti: 1 oktáva (12 půltónů) ≈ 1 velocity krok
_MIDI_SCALE = 12.0


def _to_log(key: str, val: float) -> float:
    """Transformuj do log prostoru pokud parametr je logaritmický."""
    base = key.split("_k")[0]
    if base in LOG_PARAMS and val > 0:
        return np.log(val)
    return val

def _from_log(key: str, val: float) -> float:
    """Transformuj zpět z log prostoru."""
    base = key.split("_k")[0]
    if base in LOG_PARAMS:
        return float(np.exp(val))
    return val


def _note_to_vector(note: NoteParams, k_max: int, param_keys: list[str]) -> dict[str, float]:
    """Extrahuj parametrový vektor z noty."""
    vec: dict[str, float] = {}
    for key in param_keys:
        if key in SCALAR_PARAMS:
            vec[key] = getattr(note, key, 0.0)
        else:
            m = key.split("_k")
            if len(m) == 2:
                field, k_str = m
                k = int(k_str)
                p = note.partial(k) if hasattr(note, 'partial') else None
                if p is None:
                    p = next((pp for pp in note.partials if pp.k == k), None)
                vec[key] = getattr(p, field, 0.0) if p else 0.0
    return vec


def _build_param_keys(k_max: int) -> list[str]:
    """Sestav seznam parametrových klíčů."""
    keys = list(SCALAR_PARAMS)
    for k in range(1, min(k_max + 1, 31)):
        for param in PARTIAL_PARAMS:
            keys.append(f"{param}_k{k}")
    return keys


class RBFCorrector:
    """RBF surface korekce — fituje hladké plochy přes anchor body."""

    def __init__(
        self,
        kernel: str = "thin_plate_spline",
        smoothing: float = 0.0,
        tension: float = 0.5,
        min_delta_pct: float = 1.0,
        max_delta_pct: float = 200.0,
        k_max: int = 30,
    ):
        self.kernel = kernel
        self.smoothing = smoothing
        self.tension = tension
        self.min_delta_pct = min_delta_pct
        self.max_delta_pct = max_delta_pct
        self.k_max = k_max
        self.param_keys = _build_param_keys(k_max)

        self._rbf = None  # fitted RBFInterpolator

    def fit(self, bank: BankState, anchor_db: AnchorDatabase) -> dict:
        """
        Fituj RBF surface z anchor not.
        """
        from scipy.interpolate import RBFInterpolator

        log = get_logger(__name__, method="fit")

        with OperationLogger(log, "rbf_fit", input={
            "anchors": len(anchor_db.entries),
            "params": len(self.param_keys),
            "kernel": self.kernel,
        }) as op:
            # Seskup anchor entries per (midi, vel)
            anchor_set: set[tuple[int, int]] = set()
            for e in anchor_db.entries:
                if e.vel == -1:
                    for v in range(8):
                        anchor_set.add((e.midi, v))
                else:
                    anchor_set.add((e.midi, e.vel))

            # Extrahuj pozice a vektory
            positions: list[tuple[int, int]] = []
            vectors: list[np.ndarray] = []
            for midi, vel in sorted(anchor_set):
                note = bank.get_note(midi, vel)
                if note is None:
                    continue
                vec = _note_to_vector(note, self.k_max, self.param_keys)
                vectors.append(np.array([_to_log(k, vec.get(k, 0.0)) for k in self.param_keys]))
                positions.append((midi, vel))

            if len(vectors) < 3:
                op.warn("nedostatek anchor not pro RBF", count=len(vectors))
                return {"fitted": False, "reason": "< 3 anchor vectors"}

            # Pozice: (N, 2) normalizované
            X = np.array([[m / _MIDI_SCALE, v] for m, v in positions])
            # Hodnoty: (N, K) v log prostoru
            Y = np.array(vectors)

            n_anchors, n_features = Y.shape
            op.progress("matice", anchors=n_anchors, features=n_features)

            self._rbf = RBFInterpolator(
                X, Y,
                kernel=self.kernel,
                smoothing=self.smoothing,
            )

            op.set_output({
                "fitted": True,
                "kernel": self.kernel,
                "smoothing": self.smoothing,
                "anchor_vectors": n_anchors,
                "features": n_features,
            })
            return {
                "fitted": True,
                "kernel": self.kernel,
                "anchors": n_anchors,
            }

    def interpolate(self, midi: int, vel: int) -> np.ndarray:
        """Evaluuj RBF surface na (midi, vel). Vrací log-space vektor."""
        query = np.array([[midi / _MIDI_SCALE, vel]])
        return self._rbf(query)[0]  # (K,)

    def propose(self, bank: BankState, anchor_db: AnchorDatabase) -> CorrectionSet:
        """
        Navrhne RBF korekce pro non-anchor noty.
        """
        log = get_logger(__name__, method="rbf_propose")

        with OperationLogger(log, "rbf_propose", input={
            "notes": bank.note_count(),
            "tension": self.tension,
            "kernel": self.kernel,
        }) as op:
            if self._rbf is None:
                op.warn("RBF nebyl fitován")
                return CorrectionSet(corrections=[], description="RBF not fitted")

            # Anchor set
            anchor_set: set[tuple[int, int]] = set()
            for e in anchor_db.entries:
                if e.vel == -1:
                    for v in range(8):
                        anchor_set.add((e.midi, v))
                else:
                    anchor_set.add((e.midi, e.vel))

            # Sesbírej non-anchor noty a jejich vektory
            query_notes: list[NoteParams] = []
            query_positions: list[list[float]] = []
            orig_linear_all: list[np.ndarray] = []
            orig_log_all: list[np.ndarray] = []

            for note in bank.notes.values():
                if (note.midi, note.vel) in anchor_set:
                    continue
                query_notes.append(note)
                query_positions.append([note.midi / _MIDI_SCALE, note.vel])
                orig_dict = _note_to_vector(note, self.k_max, self.param_keys)
                orig_linear_all.append(np.array([orig_dict.get(k, 0.0) for k in self.param_keys]))
                orig_log_all.append(np.array([_to_log(k, orig_dict.get(k, 0.0)) for k in self.param_keys]))

            # Batch RBF evaluace — jeden call pro všechny noty
            Q = np.array(query_positions)           # (M, 2)
            targets_log = self._rbf(Q)              # (M, K)
            op.progress("RBF evaluace", notes=len(query_notes))

            # Pre-compute log param mask
            is_log = np.array([key.split("_k")[0] in LOG_PARAMS for key in self.param_keys])

            corrections: list[Correction] = []

            for idx, note in enumerate(query_notes):
                orig_linear = orig_linear_all[idx]
                orig_log = orig_log_all[idx]
                target_log = targets_log[idx]

                for i, key in enumerate(self.param_keys):
                    orig_val = orig_linear[i]

                    if is_log[i] and orig_val > 0:
                        log_c = orig_log[i] + self.tension * (target_log[i] - orig_log[i])
                        corrected = float(np.exp(log_c))
                    else:
                        target_val = _from_log(key, target_log[i])
                        corrected = orig_val + self.tension * (target_val - orig_val)

                    denom = max(abs(orig_val), abs(corrected), 1e-15)
                    delta_pct = (corrected - orig_val) / denom * 100

                    if abs(delta_pct) < self.min_delta_pct:
                        continue
                    if abs(delta_pct) > self.max_delta_pct:
                        sign = 1 if delta_pct > 0 else -1
                        corrected = orig_val * (1 + sign * self.max_delta_pct / 100)
                        delta_pct = sign * self.max_delta_pct

                    corrections.append(Correction(
                        midi=note.midi,
                        vel=note.vel,
                        field=key,
                        original=float(orig_val),
                        corrected=float(corrected),
                        source=CorrectionSource.ANCHOR_INTERP,
                    ))

            # Dedup
            seen: dict[str, Correction] = {}
            for c in corrections:
                ck = f"{c.midi}_{c.vel}_{c.field}"
                if ck not in seen or abs(c.delta_pct) > abs(seen[ck].delta_pct):
                    seen[ck] = c
            corrections = list(seen.values())

            by_type: dict[str, int] = {}
            for c in corrections:
                t = c.field.split("_k")[0] if "_k" in c.field else c.field
                by_type[t] = by_type.get(t, 0) + 1

            op.set_output({
                "corrections": len(corrections),
                "affected_notes": len(set(f"{c.midi}_{c.vel}" for c in corrections)),
                "by_type": by_type,
            })

            return CorrectionSet(
                corrections=corrections,
                description=f"RBF surface: kernel={self.kernel}, tension={self.tension}",
            )


def propose_rbf_corrections(
    bank: BankState,
    anchor_db: AnchorDatabase,
    tension: float = 0.5,
    kernel: str = "thin_plate_spline",
    smoothing: float = 0.0,
    min_delta_pct: float = 1.0,
    max_delta_pct: float = 200.0,
    k_max: int = 30,
) -> CorrectionSet:
    """Convenience funkce: fit + propose v jednom."""
    rbf = RBFCorrector(
        kernel=kernel,
        smoothing=smoothing,
        tension=tension,
        min_delta_pct=min_delta_pct,
        max_delta_pct=max_delta_pct,
        k_max=k_max,
    )
    info = rbf.fit(bank, anchor_db)
    if not info.get("fitted"):
        return CorrectionSet(corrections=[], description=f"RBF fit failed: {info}")
    return rbf.propose(bank, anchor_db)
