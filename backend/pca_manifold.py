"""
pca_manifold.py — PCA-based manifold korekce parametrů

Anchor noty definují manifold v parametrovém prostoru.
PCA redukuje dimenzionalitu, zachovává korelace mezi parametry.
Non-anchor noty se korigují interpolací v PCA latentním prostoru
na základě blízkosti v (midi, vel) prostoru.

Pipeline:
  1. Extrahuj parametrové vektory z anchor not (log prostor)
  2. Normalizuj (z-score per parametr)
  3. PCA → zachovej n_components hlavních komponent
  4. Zakóduj anchor vektory → uložit (midi, vel) → koeficienty
  5. Pro každou non-anchor notu:
     a. Interpoluj PCA koeficienty z anchorů (IDW v midi/vel)
     b. Dekóduj → cílový parametrový vektor
     c. corrected = original + tension × (target - original)
"""

from __future__ import annotations

import numpy as np
from typing import Optional

from logger import get_logger, OperationLogger
from models import (
    AnchorDatabase, BankState, Correction,
    CorrectionSet, CorrectionSource, NoteParams,
)


_log = get_logger(__name__, cls="PCAManifold")

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
                    # Fallback: hledej v partials
                    p = next((pp for pp in note.partials if pp.k == k), None)
                vec[key] = getattr(p, field, 0.0) if p else 0.0
    return vec


def _build_param_keys(k_max: int) -> list[str]:
    """Sestav seznam parametrových klíčů."""
    keys = list(SCALAR_PARAMS)
    for k in range(1, min(k_max + 1, 31)):  # max 30 parciálů pro PCA
        for param in PARTIAL_PARAMS:
            keys.append(f"{param}_k{k}")
    return keys


class PCACorrector:
    """PCA manifold korekce s interpolací v latentním prostoru."""

    def __init__(
        self,
        n_components: float = 0.95,  # zachovej 95% variance (nebo int = fixní počet)
        tension: float = 0.5,
        min_delta_pct: float = 1.0,
        max_delta_pct: float = 200.0,
        k_max: int = 30,
    ):
        self.n_components = n_components
        self.tension = tension
        self.min_delta_pct = min_delta_pct
        self.max_delta_pct = max_delta_pct
        self.k_max = k_max
        self.param_keys = _build_param_keys(k_max)

        # PCA state (fitted)
        self._mean: Optional[np.ndarray] = None
        self._std: Optional[np.ndarray] = None
        self._components: Optional[np.ndarray] = None  # (n_comp, n_features)
        self._n_comp: int = 0
        # Anchor pozice a jejich PCA koeficienty
        self._anchor_positions: list[tuple[int, int]] = []   # (midi, vel)
        self._anchor_coeffs: Optional[np.ndarray] = None     # (n_anchors, n_comp)

    def fit(self, bank: BankState, anchor_db: AnchorDatabase) -> dict:
        """
        Fituj PCA z anchor not.
        Vrátí info dict.
        """
        log = get_logger(__name__, method="fit")

        with OperationLogger(log, "pca_fit", input={
            "anchors": len(anchor_db.entries),
            "params": len(self.param_keys),
        }) as op:
            # Seskup anchor entries per (midi, vel)
            anchor_set: set[tuple[int, int]] = set()
            for e in anchor_db.entries:
                if e.vel == -1:
                    for v in range(8):
                        anchor_set.add((e.midi, v))
                else:
                    anchor_set.add((e.midi, e.vel))

            # Extrahuj vektory z anchor not — zachovej pozice
            positions: list[tuple[int, int]] = []
            vectors: list[np.ndarray] = []
            for midi, vel in sorted(anchor_set):
                note = bank.get_note(midi, vel)
                if note is None:
                    continue
                vec = _note_to_vector(note, self.k_max, self.param_keys)
                vectors.append(np.array([_to_log(k, vec.get(k, 0.0)) for k in self.param_keys]))
                positions.append((midi, vel))

            if len(vectors) < 5:
                op.warn("nedostatek anchor not pro PCA", count=len(vectors))
                return {"fitted": False, "reason": "< 5 anchor vectors"}

            X = np.array(vectors)  # (n_anchors, n_features)
            n_samples, n_features = X.shape
            op.progress("matice", samples=n_samples, features=n_features)

            # Z-score normalizace
            self._mean = X.mean(axis=0)
            self._std = X.std(axis=0)
            self._std[self._std < 1e-12] = 1.0  # avoid div by zero
            X_norm = (X - self._mean) / self._std

            # SVD → PCA
            U, S, Vt = np.linalg.svd(X_norm, full_matrices=False)
            explained = (S ** 2) / (S ** 2).sum()
            cumulative = np.cumsum(explained)

            # Určení počtu komponent
            if isinstance(self.n_components, float) and self.n_components < 1.0:
                self._n_comp = int(np.searchsorted(cumulative, self.n_components) + 1)
            else:
                self._n_comp = min(int(self.n_components), len(S))
            self._n_comp = max(1, min(self._n_comp, n_samples, n_features))

            self._components = Vt[:self._n_comp]  # (n_comp, n_features)

            # Zakóduj anchor vektory do latentního prostoru
            self._anchor_coeffs = X_norm @ self._components.T  # (n_anchors, n_comp)
            self._anchor_positions = positions

            variance_kept = float(cumulative[self._n_comp - 1]) if self._n_comp <= len(cumulative) else 1.0

            op.set_output({
                "fitted": True,
                "n_components": self._n_comp,
                "variance_kept": round(variance_kept, 4),
                "anchor_vectors": n_samples,
                "features": n_features,
            })
            return {
                "fitted": True,
                "n_components": self._n_comp,
                "variance_kept": variance_kept,
            }

    def interpolate(self, midi: int, vel: int) -> np.ndarray:
        """Interpoluj cílový vektor z anchor koeficientů v PCA latentním prostoru.

        IDW (inverse-distance weighting) v (midi, vel) prostoru.
        Výsledek je vážený průměr anchor PCA koeficientů → decode → log-space vektor.
        Garantuje, že výsledek leží uvnitř konvexního obalu anchorů na manifoldu.
        """
        # Vzdálenosti ke všem anchorům: d² = (Δmidi/12)² + (Δvel)²
        dists_sq = np.array([
            ((midi - am) / _MIDI_SCALE) ** 2 + (vel - av) ** 2
            for am, av in self._anchor_positions
        ])

        # Přesná shoda → vrať přímo anchor koeficienty
        exact = np.where(dists_sq < 1e-10)[0]
        if len(exact) > 0:
            coeffs = self._anchor_coeffs[exact[0]]
        else:
            # IDW p=2: w_i = 1 / d_i²
            weights = 1.0 / dists_sq
            weights /= weights.sum()
            coeffs = weights @ self._anchor_coeffs  # (n_comp,)

        # Decode z PCA latentního prostoru do log prostoru
        decoded = coeffs @ self._components          # (n_features,)
        return decoded * self._std + self._mean

    def propose(self, bank: BankState, anchor_db: AnchorDatabase) -> CorrectionSet:
        """
        Navrhne PCA korekce pro non-anchor noty.
        """
        log = get_logger(__name__, method="pca_propose")

        with OperationLogger(log, "pca_propose", input={
            "notes": bank.note_count(),
            "tension": self.tension,
            "n_comp": self._n_comp,
        }) as op:
            if self._mean is None:
                op.warn("PCA nebyl fitován")
                return CorrectionSet(corrections=[], description="PCA not fitted")

            # Anchor set
            anchor_set: set[tuple[int, int]] = set()
            for e in anchor_db.entries:
                if e.vel == -1:
                    for v in range(8):
                        anchor_set.add((e.midi, v))
                else:
                    anchor_set.add((e.midi, e.vel))

            corrections: list[Correction] = []

            for note in bank.notes.values():
                if (note.midi, note.vel) in anchor_set:
                    continue

                # Originální vektor (lineární)
                orig_dict = _note_to_vector(note, self.k_max, self.param_keys)
                orig_linear = np.array([orig_dict.get(k, 0.0) for k in self.param_keys])

                # Log transformace
                orig_log = np.array([_to_log(k, orig_dict.get(k, 0.0)) for k in self.param_keys])

                # Interpolovaný cílový vektor z anchor manifoldu (v log prostoru)
                target_log = self.interpolate(note.midi, note.vel)

                # Generuj korekce per parametr
                for i, key in enumerate(self.param_keys):
                    orig_val = orig_linear[i]
                    base = key.split("_k")[0]

                    if base in LOG_PARAMS and orig_val > 0:
                        # Tension blend v LOG prostoru (geometrický průměr)
                        log_o = orig_log[i]
                        log_t = target_log[i]
                        log_c = log_o + self.tension * (log_t - log_o)
                        corrected = float(np.exp(log_c))
                    else:
                        # Lineární blend pro non-log parametry (a1, beat_hz)
                        target_val = _from_log(key, target_log[i])
                        corrected = orig_val + self.tension * (target_val - orig_val)

                    # Delta
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
                description=f"PCA manifold: n_comp={self._n_comp}, tension={self.tension}",
            )


def propose_pca_corrections(
    bank: BankState,
    anchor_db: AnchorDatabase,
    tension: float = 0.5,
    n_components: float = 0.95,
    min_delta_pct: float = 1.0,
    max_delta_pct: float = 200.0,
    k_max: int = 30,
) -> CorrectionSet:
    """Convenience funkce: fit + propose v jednom."""
    pca = PCACorrector(
        n_components=n_components,
        tension=tension,
        min_delta_pct=min_delta_pct,
        max_delta_pct=max_delta_pct,
        k_max=k_max,
    )
    info = pca.fit(bank, anchor_db)
    if not info.get("fitted"):
        return CorrectionSet(corrections=[], description=f"PCA fit failed: {info}")
    return pca.propose(bank, anchor_db)
