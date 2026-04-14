"""
relation_fitter.py — fitting fyzikálních vztahů mezi parametry banky

Paralelizace:
- RelationFitter.fit_all:      pluginy běží paralelně v ThreadPoolExecutor
                                (každý plugin je nezávislý, numpy/scipy
                                 uvolňuje GIL při numerických operacích)
- DampingLawFitter.fit:        per-nota fit běží paralelně v ProcessPoolExecutor
                                (88 × lineární regrese je čistě CPU-bound)
- SpectralShapeFitter.fit:     vektorizováno přes numpy bez explicitního
                                paralelismu (RectBivariateSpline je fast-path)
- VelocityModelFitter.fit:     per-nota curve_fit paralelně v ThreadPoolExecutor
                                (scipy.optimize.curve_fit uvolňuje GIL)

Volba executoru:
- numpy/scipy operace uvolňují GIL → ThreadPoolExecutor je dostačující
  pro většinu fitovacích úloh a má nulový pickling overhead
- Výjimka: damping law per-nota je čistě Python loop → ProcessPoolExecutor

Env proměnné:
  FIT_PLUGIN_WORKERS  (default: min(cpu_count, 4)  — počet paralelních pluginů)
  FIT_NOTE_WORKERS    (default: cpu_count - 1       — per-nota CPU workers)

Status: scaffold s loggingem a paralelizací — matematika TODO

Changelog:
  2025-04-14 v0.1  — initial scaffold, plugin architektura
  2025-04-14 v0.2  — logging integrace, OperationLogger per plugin
  2025-04-14 v0.3  — ThreadPool skeleton: DampingLaw, VelocityModel
  2025-04-14 v0.4  — BCurveFitter: numpy skeleton s mediánem B přes vel
  2025-04-14 v0.5  — fit(bank,weights) místo fit(notes,weights), OUTLIER_WEIGHTS
  2025-04-14 v0.6  — IMPLEMENTOVÁNO: BCurveFitter, DampingLawFitter,
                     VelocityModelFitter, SpectralShapeFitter
  2025-04-14 v0.7  — RelationFitter: anchor_weights, _aggregate_outlier_scores
  2025-04-14 v0.8  — mad_sigma guard, scipy linregress → numpy lstsq
"""

from __future__ import annotations

import math
import os
from abc import ABC, abstractmethod
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from typing import Optional

import numpy as np

from logger import OperationLogger, get_logger, log_operation
from models import (
    AnchorDatabase,
    BankState,
    BCurveParams,
    DampingParams,
    FitResult,
    NoteParams,
)

# ---------------------------------------------------------------------------
# Konfigurace paralelizace
# ---------------------------------------------------------------------------

_PLUGIN_WORKERS: int = int(os.environ.get(
    "FIT_PLUGIN_WORKERS", min(os.cpu_count() or 4, 4)
))
_NOTE_WORKERS: int = int(os.environ.get(
    "FIT_NOTE_WORKERS", max((os.cpu_count() or 4) - 1, 1)
))
# Minimální počet not pro spuštění paralelního damping fitu
_DAMPING_PARALLEL_THRESHOLD: int = 40


# ---------------------------------------------------------------------------
# Fyzikální helpery
# ---------------------------------------------------------------------------

def midi_to_f0(midi: int) -> float:
    """f0 = 440 * 2^((midi-69)/12)"""
    return 440.0 * math.pow(2.0, (midi - 69) / 12.0)


def partial_freq(f0: float, B: float, k: int) -> float:
    """f_k = k * f0 * sqrt(1 + B*k^2)"""
    return k * f0 * math.sqrt(1.0 + B * k * k)


def mad_sigma(values) -> tuple[float, float]:
    """(median, 1.4826 * MAD) — robustní estimátor sigma. Guard against zero."""
    arr = np.asarray(values, dtype=float)
    if len(arr) == 0:
        return 0.0, 1.0
    med = float(np.median(arr))
    sigma = max(1.4826 * float(np.median(np.abs(arr - med))), 1e-9)
    return med, sigma


# ---------------------------------------------------------------------------
# Plugin základní třída
# ---------------------------------------------------------------------------

class FitPlugin(ABC):
    """Základní třída pro fitting pluginy."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Identifikátor pro logování."""

    @abstractmethod
    def fit(self, bank: BankState, weights: dict[str, float]) -> dict:
        """
        Fituje fyzikální vztah. Vrátí partial dict pro FitResult.

        Args:
            bank:    Načtená banka.
            weights: {note_key: weight} z AnchorManager.to_weights().
        """


# ---------------------------------------------------------------------------
# BCurveFitter
# ---------------------------------------------------------------------------

class BCurveFitter(FitPlugin):
    """
    Segmentovaná log-log regrese B(f0).
    log10(B) = alpha * log10(f0) + beta — dva segmenty bass/treble.

    Paralelizace: není potřeba — 88 bodů, vektorizováno přes numpy.
    """

    _log = get_logger(__name__, cls="BCurveFitter")

    def __init__(self, break_midi: Optional[int] = None, sigma_threshold: float = 2.5,
                 spline_smoothing: float = 1.0):
        self.break_midi       = break_midi
        self.sigma_threshold  = sigma_threshold
        self.spline_smoothing = spline_smoothing
        self._spl_bass = None
        self._spl_treble = None

    @property
    def name(self) -> str:
        return "b_curve"

    def fit(self, bank: BankState, weights: dict[str, float]) -> dict:
        """
        Segmentovaná log-log regrese B(f0).
        Vrátí: {"b_curve": BCurveParams, "outlier_scores_b": {midi_prefix: score}}
        """
        log = get_logger(__name__, cls="BCurveFitter", method="fit")

        with OperationLogger(
            log, "fit",
            input={"notes": bank.note_count(), "break_midi": self.break_midi}
        ) as op:
            log_f0, log_B, w_arr, midis = self._collect_points(bank, weights)

            if len(log_f0) < 4:
                op.warn("nedostatek bodů", count=len(log_f0))
                return {"b_curve": None, "outlier_scores_b": {}}

            op.progress("body sesbírány", n=len(log_f0))

            # Detekce nebo použití zadaného zlomu
            break_midi = (self.break_midi
                          or self._auto_detect_break(log_f0, log_B, w_arr, midis))
            op.progress("zlom", break_midi=break_midi)

            lbk       = math.log10(midi_to_f0(break_midi))
            mask_bass = log_f0 < lbk
            mask_tre  = ~mask_bass

            if mask_bass.sum() < 4 or mask_tre.sum() < 4:
                op.warn("segment < 4 body", bass=int(mask_bass.sum()),
                        treble=int(mask_tre.sum()))
                return {"b_curve": None, "outlier_scores_b": {}}

            # Spline fit per segment (v log-log prostoru)
            from scipy.interpolate import UnivariateSpline

            s_bass = max(4, int(mask_bass.sum() * self.spline_smoothing))
            s_tre  = max(4, int(mask_tre.sum() * self.spline_smoothing))

            try:
                self._spl_bass = UnivariateSpline(
                    log_f0[mask_bass], log_B[mask_bass],
                    w=w_arr[mask_bass], s=s_bass, k=3)
                self._spl_treble = UnivariateSpline(
                    log_f0[mask_tre], log_B[mask_tre],
                    w=w_arr[mask_tre], s=s_tre, k=3)
            except Exception as e:
                op.warn("spline fit selhal, fallback na lineární", error=str(e))
                a_b, b_b = self._weighted_linear_fit(
                    log_f0[mask_bass], log_B[mask_bass], w_arr[mask_bass])
                a_t, b_t = self._weighted_linear_fit(
                    log_f0[mask_tre], log_B[mask_tre], w_arr[mask_tre])
                self._spl_bass = None
                self._spl_treble = None

            op.progress("segmenty nafitovány (spline)",
                        bass_pts=int(mask_bass.sum()),
                        treble_pts=int(mask_tre.sum()))

            # Residuály a outlier skóre
            if self._spl_bass is not None:
                predicted = np.where(mask_bass,
                                      self._spl_bass(log_f0),
                                      self._spl_treble(log_f0))
            else:
                predicted = np.where(mask_bass,
                                      a_b * log_f0 + b_b,
                                      a_t * log_f0 + b_t)

            residuals_arr = log_B - predicted
            med, sigma = mad_sigma(residuals_arr)
            z_scores   = np.abs(residuals_arr - med) / sigma

            outlier_midis = [midis[i] for i in range(len(midis))
                             if z_scores[i] > self.sigma_threshold]
            if outlier_midis:
                op.warn("B outliery", count=len(outlier_midis),
                        midis=outlier_midis[:6])

            outlier_scores = {
                f"m{midis[i]:03d}": float(
                    min(z_scores[i] / (self.sigma_threshold * 2), 1.0)
                )
                for i in range(len(midis))
            }
            residuals_dict = {midis[i]: float(residuals_arr[i])
                              for i in range(len(midis))}

            # Lineární approximace pro BCurveParams (zpětná kompatibilita)
            a_b, b_b = self._weighted_linear_fit(
                log_f0[mask_bass], log_B[mask_bass], w_arr[mask_bass])
            a_t, b_t = self._weighted_linear_fit(
                log_f0[mask_tre], log_B[mask_tre], w_arr[mask_tre])

            op.set_output({"break_midi": break_midi,
                           "outliers": len(outlier_midis),
                           "spline": self._spl_bass is not None})

            return {
                "b_curve": BCurveParams(
                    alpha_bass=a_b, beta_bass=b_b,
                    alpha_treble=a_t, beta_treble=b_t,
                    break_midi=break_midi,
                    residuals=residuals_dict,
                ),
                "outlier_scores_b": outlier_scores,
            }

    def predict_B(self, params: BCurveParams, midi: int) -> float:
        """Predikuje B pro MIDI notu — spline pokud dostupný, jinak lineární."""
        lf0 = math.log10(midi_to_f0(midi))
        lbk = math.log10(midi_to_f0(params.break_midi))
        if lf0 < lbk:
            if self._spl_bass is not None:
                return 10 ** float(self._spl_bass(lf0))
            return 10 ** (params.alpha_bass * lf0 + params.beta_bass)
        else:
            if self._spl_treble is not None:
                return 10 ** float(self._spl_treble(lf0))
            return 10 ** (params.alpha_treble * lf0 + params.beta_treble)

    def _collect_points(
        self, bank: BankState, weights: dict[str, float]
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[int]]:
        """
        Sestaví (log10_f0, log10_B, weight, midis) vektory.
        B je mediánováno přes velocity vrstvy per nota.
        """
        log_f0_list, log_B_list, w_list, midis = [], [], [], []

        for midi in range(bank.metadata.midi_range_from,
                          bank.metadata.midi_range_to + 1):
            vel_layers = bank.velocity_layers(midi)
            if not vel_layers:
                continue
            B_vals  = [n.B    for n in vel_layers if n.B > 0]
            f0_vals = [n.f0_hz for n in vel_layers if n.f0_hz > 0]
            if not B_vals or not f0_vals:
                continue
            B_med  = float(np.median(B_vals))
            f0_med = float(np.median(f0_vals))
            w_mean = float(np.mean([
                weights.get(n.note_key, 1.0) for n in vel_layers
            ]))
            log_f0_list.append(math.log10(f0_med))
            log_B_list.append(math.log10(B_med))
            w_list.append(w_mean)
            midis.append(midi)

        return (np.array(log_f0_list), np.array(log_B_list),
                np.array(w_list), midis)

    def _auto_detect_break(
        self,
        log_f0: np.ndarray,
        log_B:  np.ndarray,
        weights: np.ndarray,
        midis:  list[int],
    ) -> int:
        """
        Grid search MIDI 35–60 — minimalizuje součet RSS obou segmentů.
        """
        best_midi = 48
        best_rss  = float("inf")

        for break_candidate in range(35, 61):
            lbk       = math.log10(midi_to_f0(break_candidate))
            mask_bass = log_f0 < lbk
            mask_tre  = ~mask_bass

            if mask_bass.sum() < 2 or mask_tre.sum() < 2:
                continue

            rss = 0.0
            for mask in (mask_bass, mask_tre):
                a, b = self._weighted_linear_fit(
                    log_f0[mask], log_B[mask], weights[mask])
                pred = a * log_f0[mask] + b
                rss += float(np.sum(weights[mask] * (log_B[mask] - pred) ** 2))

            if rss < best_rss:
                best_rss  = rss
                best_midi = break_candidate

        return best_midi

    @staticmethod
    def _weighted_linear_fit(
        x: np.ndarray, y: np.ndarray, w: np.ndarray
    ) -> tuple[float, float]:
        """
        Váhovaná lineární regrese y = a*x + b.
        Implementace: numpy.linalg.lstsq s váhovací maticí sqrt(w).
        """
        if len(x) < 2:
            return 0.0, float(np.mean(y)) if len(y) else 0.0
        sw = np.sqrt(np.maximum(w, 0.0))
        A  = np.column_stack([x * sw, sw])
        b  = y * sw
        coeffs, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
        return float(coeffs[0]), float(coeffs[1])


# ---------------------------------------------------------------------------
# DampingLawFitter
# ---------------------------------------------------------------------------

class DampingLawFitter(FitPlugin):
    """
    Per-nota lineární fit: 1/tau1(k) = R + eta * f_k^2

    Paralelizace: ProcessPoolExecutor — 88 nezávislých lineárních fitů
    je CPU-bound Python loop. Na M4 14-core: ~8× zrychlení.
    """

    _log = get_logger(__name__, cls="DampingLawFitter")

    def __init__(
        self,
        min_quality: float = 0.7,
        sigma_threshold: float = 3.0,
        note_workers: int = _NOTE_WORKERS,
        spline_smoothing: float = 1.0,
    ):
        self.min_quality      = min_quality
        self.sigma_threshold  = sigma_threshold
        self.note_workers     = note_workers
        self.spline_smoothing = spline_smoothing

    @property
    def name(self) -> str:
        return "damping_law"

    def fit(self, bank: BankState, weights: dict[str, float]) -> dict:
        """
        Fituje damping law per nota paralelně.

        Paralelizace: každá nota je nezávislá → ProcessPoolExecutor.
        Výsledky jsou sloučeny v hlavním procesu.

        TODO: implementovat — skeleton s paralelizací je hotov
        """
        log = get_logger(__name__, cls="DampingLawFitter", method="fit")

        # Seskupit noty per MIDI (mediánovat přes velocity)
        midi_groups: dict[int, list[NoteParams]] = {}
        for note in bank.notes.values():
            midi_groups.setdefault(note.midi, []).append(note)

        use_parallel = (
            len(midi_groups) >= _DAMPING_PARALLEL_THRESHOLD
            and self.note_workers > 1
        )

        with OperationLogger(
            log, "fit",
            input={
                "midi_count": len(midi_groups),
                "parallel": use_parallel,
                "workers": self.note_workers if use_parallel else 1,
            }
        ) as op:
            damping:   dict[int, DampingParams] = {}
            residuals: dict[str, float]         = {}

            if not use_parallel:
                for midi, notes in midi_groups.items():
                    w = weights.get(f"m{midi:03d}_vel4", 1.0)
                    try:
                        params, note_res = self._fit_single_note(notes, w)
                        damping[midi]    = params
                        residuals.update(note_res)
                    except Exception as e:
                        op.warn("nota přeskočena", midi=midi, error=str(e))
            else:
                items = list(midi_groups.items())
                op.progress("ProcessPool spuštěn",
                            notes=len(items), workers=self.note_workers)

                with ProcessPoolExecutor(max_workers=self.note_workers) as ex:
                    futs = {
                        ex.submit(
                            _fit_damping_note,
                            midi,
                            # Předáme pouze serializovatelná data (dict)
                            [n.model_dump() for n in notes],
                            weights.get(f"m{midi:03d}_vel4", 1.0),
                            self.min_quality,
                        ): midi
                        for midi, notes in items
                    }
                    for fut in as_completed(futs):
                        midi = futs[fut]
                        try:
                            params_dict, note_res = fut.result()
                            damping[midi] = DampingParams(**params_dict)
                            residuals.update(note_res)
                        except Exception as e:
                            op.warn("nota selhal", midi=midi, error=str(e))

            # Cross-keyboard spline per parciál k
            # Pro každý k: fittuj 1/τ1(midi) jako spline s anchor váhami
            # → outlier score per nota založený na odchylce od spline
            outlier_scores_damping: dict[str, float] = {}
            spline_residuals, spline_preds = self._fit_cross_keyboard_splines(
                bank, weights, midi_groups, op
            )
            for pfx, res in spline_residuals.items():
                outlier_scores_damping[pfx] = min(res / (self.sigma_threshold * 2), 1.0)

            outlier_count = sum(
                1 for v in outlier_scores_damping.values() if v > 0.5
            )
            op.set_output({
                "fitted": len(damping),
                "outlier_partials": outlier_count,
            })
            return {
                "damping":                damping,
                "damping_residuals":      residuals,
                "outlier_scores_damping": outlier_scores_damping,
                "damping_spline":         spline_preds,
            }

    def predict_tau(self, params: DampingParams, f_hz: float) -> float:
        """tau = 1 / (R + eta * f^2)"""
        denom = params.R + params.eta * f_hz * f_hz
        return 1.0 / max(denom, 1e-9)

    def _fit_single_note(
        self,
        note_vels: list[NoteParams],
        weight: float,
    ) -> tuple[DampingParams, dict[str, float]]:
        """
        Fituje damping law pro jednu notu přes mediánované tau1 per parciál.
        Vrátí (DampingParams, {midi_k_key: |residual_sigma|}).
        """
        midi = note_vels[0].midi if note_vels else 0

        # Sbírej (f_k^2, 1/tau1) body přes velocity vrstvy
        fk2_list:  list[float] = []
        inv_tau_list: list[float] = []

        # Mediánuj tau1 per harmonický index k přes velocity vrstvy
        k_set = set()
        for n in note_vels:
            for p in n.partials:
                if p.fit_quality >= self.min_quality and p.tau1 > 0:
                    k_set.add(p.k)

        for k in sorted(k_set):
            tau1_vals = []
            fk_vals   = []
            for n in note_vels:
                p = n.partial(k)
                if p and p.fit_quality >= self.min_quality and p.tau1 > 0:
                    tau1_vals.append(p.tau1)
                    fk_vals.append(p.f_hz)

            if len(tau1_vals) < 1:
                continue

            tau1_med = float(np.median(tau1_vals))
            fk_med   = float(np.median(fk_vals))
            fk2_list.append(fk_med * fk_med)
            inv_tau_list.append(1.0 / tau1_med)

        if len(fk2_list) < 2:
            # Nedostatek parciálů — vráť defaults
            return DampingParams(R=0.05, eta=1e-8, residuals={}), {}

        x = np.array(fk2_list)
        y = np.array(inv_tau_list)

        # Váhovaný lineární fit: y = R + eta * x
        # scipy linregress neváhuje — pro jednoduchost použijeme lstsq
        A = np.column_stack([np.ones_like(x) * weight**0.5,
                             x * weight**0.5])
        b = y * weight**0.5
        coeffs, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
        R   = max(float(coeffs[0]), 1e-6)  # R musí být kladné
        eta = max(float(coeffs[1]), 1e-12)

        # Residuály per parciál
        predicted  = R + eta * x
        residuals_arr = np.abs(y - predicted) / np.maximum(y, 1e-9)
        residuals_dict: dict[str, float] = {}
        k_sorted = sorted(k_set)
        for i, k in enumerate(k_sorted[:len(residuals_arr)]):
            res = float(residuals_arr[i])
            if res > 0.01:  # ignoruj sub-1% odchylky
                residuals_dict[f"m{midi:03d}_k{k}"] = res

        return DampingParams(R=R, eta=eta, residuals=residuals_dict), residuals_dict

    def _fit_cross_keyboard_splines(
        self,
        bank: BankState,
        weights: dict[str, float],
        midi_groups: dict[int, list[NoteParams]],
        op: OperationLogger,
    ) -> tuple[dict[str, float], dict[str, float]]:
        """
        Per-parciál k: fittuj 1/τ1(midi, vel) jako 2D spline.
        Osa X = MIDI, osa Y = velocity. Anchor váhy ovlivňují fit.
        Vrátí (outlier_residuals, spline_predictions).
        Predictions klíč: "k{k}_m{midi:03d}_v{vel}" → predicted 1/tau1.
        """
        from scipy.interpolate import UnivariateSpline

        k_max = max(
            (p.k for n in bank.notes.values() for p in n.partials),
            default=0,
        )
        spline_predictions: dict[str, float] = {}
        if k_max < 2:
            return {}, spline_predictions

        # Per k: sbírej (midi, vel, inv_tau1, weight) — per nota, ne mediánované
        per_k_data: dict[int, list[tuple[int, int, float, float]]] = {}
        for note in bank.notes.values():
            w = weights.get(note.note_key, weights.get(f"m{note.midi:03d}_vel4", 1.0))
            for p in note.partials:
                if p.fit_quality >= self.min_quality and p.tau1 > 0:
                    per_k_data.setdefault(p.k, []).append(
                        (note.midi, note.vel, 1.0 / p.tau1, w)
                    )

        # Per k: fittuj 1D spline per velocity vrstvu
        # (2D RectBivariateSpline potřebuje grid — naše data nejsou grid)
        # Alternativa: per-vel 1D spline přes MIDI osu
        midi_residuals: dict[str, list[float]] = {}

        for k in range(1, min(k_max + 1, 61)):
            data = per_k_data.get(k)
            if not data or len(data) < 6:
                continue

            # Seskup per velocity
            per_vel: dict[int, list[tuple[int, float, float]]] = {}
            for midi, vel, inv_tau, w in data:
                per_vel.setdefault(vel, []).append((midi, inv_tau, w))

            for vel, vel_data in per_vel.items():
                if len(vel_data) < 4:
                    continue

                # Seřaď per MIDI
                vel_data.sort(key=lambda x: x[0])
                midis_arr = np.array([d[0] for d in vel_data], dtype=float)
                inv_tau   = np.array([d[1] for d in vel_data])
                w_arr     = np.array([d[2] for d in vel_data])

                # Deduplikace MIDI (UnivariateSpline vyžaduje unikátní x)
                if len(set(midis_arr)) < len(midis_arr):
                    unique_midis = sorted(set(midis_arr))
                    new_inv = []
                    new_w = []
                    for m in unique_midis:
                        mask = midis_arr == m
                        new_inv.append(float(np.mean(inv_tau[mask])))
                        new_w.append(float(np.max(w_arr[mask])))
                    midis_arr = np.array(unique_midis)
                    inv_tau = np.array(new_inv)
                    w_arr = np.array(new_w)

                if len(midis_arr) < 4:
                    continue

                try:
                    s_val = len(midis_arr) * self.spline_smoothing
                    spl = UnivariateSpline(midis_arr, inv_tau, w=w_arr,
                                           s=s_val, k=3)
                    predicted = spl(midis_arr)
                    res = np.abs(inv_tau - predicted) / np.maximum(np.abs(inv_tau), 1e-9)

                    for i, midi in enumerate(midis_arr):
                        midi_int = int(midi)
                        pfx = f"m{midi_int:03d}"
                        midi_residuals.setdefault(pfx, []).append(float(res[i]))
                        spline_predictions[f"k{k}_m{midi_int:03d}_v{vel}"] = float(predicted[i])
                except Exception:
                    continue

        # Agreguj residuály per midi → mean
        result: dict[str, float] = {}
        for pfx, res_list in midi_residuals.items():
            result[pfx] = float(np.mean(res_list))

        op.progress("cross-keyboard splines (per-vel)",
                    fitted_k=len(per_k_data), notes=len(result),
                    predictions=len(spline_predictions))
        return result, spline_predictions


# ---------------------------------------------------------------------------
# SpectralShapeFitter
# ---------------------------------------------------------------------------

class SpectralShapeFitter(FitPlugin):
    """
    2D spline A0(k)/A0(1) přes MIDI × k prostor.

    Paralelizace: RectBivariateSpline je single-threaded ale vektorizovaný.
    Pro 704 not × 60 harmonik je bez paralelismu dostatečně rychlý (~50ms).
    """

    _log = get_logger(__name__, cls="SpectralShapeFitter")

    def __init__(self, vel_range: tuple[int, int] = (4, 7)):
        self.vel_range = vel_range
        self._spline   = None

    @property
    def name(self) -> str:
        return "spectral_shape"

    def fit(self, bank: BankState, weights: dict[str, float]) -> dict:
        """
        Per-k UnivariateSpline A0(k)/A0(1) v dB přes MIDI osu.
        Pracuje pouze s forte velocity vrstvami (vel_range).
        """
        log = get_logger(__name__, cls="SpectralShapeFitter", method="fit")

        with OperationLogger(
            log, "fit",
            input={"vel_range": self.vel_range, "notes": bank.note_count()}
        ) as op:
            from scipy.interpolate import UnivariateSpline

            vel_lo, vel_hi = self.vel_range
            forte_notes = [n for n in bank.notes.values()
                           if vel_lo <= n.vel <= vel_hi]

            if len(forte_notes) < 4:
                op.warn("nedostatek not pro spline", count=len(forte_notes))
                return {
                    "shape_residuals": {},
                    "outlier_scores_spectral": {},
                }

            # Zjisti k_max dostupný napříč bankou
            k_max = max((n.n_partials for n in forte_notes), default=0)
            if k_max < 2:
                op.warn("k_max < 2, přeskakuji spectral shape fit")
                return {"shape_residuals": {}, "outlier_scores_spectral": {}}

            op.progress("forte notes sesbírány", count=len(forte_notes), k_max=k_max)

            # Sestav spline per k: A0(k)/A0(1) v dB vs MIDI
            # Uložíme jako dict {k: fitted UnivariateSpline}
            self._splines: dict[int, object] = {}
            self._k_max = k_max

            for k in range(2, k_max + 1):
                midi_pts, shape_pts, w_pts = [], [], []
                for n in forte_notes:
                    p1 = n.partial(1)
                    pk = n.partial(k)
                    if (p1 is None or pk is None or
                            p1.A0 <= 0 or pk.A0 <= 0 or
                            p1.fit_quality < 0.5 or pk.fit_quality < 0.5):
                        continue
                    ratio_db = 20.0 * math.log10(pk.A0 / p1.A0)
                    w_key    = n.note_key
                    midi_pts.append(n.midi)
                    shape_pts.append(ratio_db)
                    w_pts.append(weights.get(w_key, 1.0))

                if len(midi_pts) < 4:
                    continue

                try:
                    # k=3 pro hladký spline; s=None = auto smoothing
                    spl = UnivariateSpline(
                        midi_pts, shape_pts, w=w_pts, k=3,
                        s=len(midi_pts) * 0.5, ext=3,
                    )
                    self._splines[k] = spl
                except Exception as e:
                    log.warning(f"spline fit selhal k={k}  error={e}")

            op.progress("spline hotov", fitted_k=len(self._splines))

            # Residuály
            shape_residuals:   dict[str, float] = {}
            outlier_scores_sp: dict[str, float] = {}

            for n in forte_notes:
                p1 = n.partial(1)
                if p1 is None or p1.A0 <= 0:
                    continue
                res_list = []
                for k, spl in self._splines.items():
                    pk = n.partial(k)
                    if pk is None or pk.A0 <= 0:
                        continue
                    actual_db    = 20.0 * math.log10(pk.A0 / p1.A0)
                    predicted_db = float(spl(n.midi))
                    res_list.append(abs(actual_db - predicted_db))

                if res_list:
                    mean_res = float(np.mean(res_list))
                    key      = n.note_key
                    shape_residuals[key]   = mean_res
                    # Skóre: clamp na [0,1], 6 dB = 0.5
                    outlier_scores_sp[f"m{n.midi:03d}"] = max(
                        outlier_scores_sp.get(f"m{n.midi:03d}", 0.0),
                        min(mean_res / 12.0, 1.0),
                    )

            mean_res_db = (float(np.mean(list(shape_residuals.values())))
                           if shape_residuals else 0.0)
            op.set_output({
                "fitted_k": len(self._splines),
                "residuals_mean_db": round(mean_res_db, 3),
            })
            return {
                "shape_residuals":      shape_residuals,
                "outlier_scores_spectral": outlier_scores_sp,
            }

    def predict(self, midi: int, k: int, n_partials: int) -> float:
        """
        Predikuje A0(k)/A0(1) v dB pro danou notu a harmonické číslo.
        Vrátí 0.0 pokud spline pro k neexistuje.
        """
        if not hasattr(self, "_splines") or k not in self._splines:
            return 0.0
        return float(self._splines[k](midi))

    def _build_shape_matrix(self, bank: BankState) -> np.ndarray:
        """
        Sestaví 2D matici (midi × k) v dB — pomocná metoda.
        Používá forte velocity vrstva (vel 4–7 medián).
        """
        vel_lo, vel_hi = self.vel_range
        midis = sorted({n.midi for n in bank.notes.values()})
        k_max = max((n.n_partials for n in bank.notes.values()), default=0)

        matrix = np.zeros((len(midis), k_max))
        for i, midi in enumerate(midis):
            layers = [n for n in bank.notes.values()
                      if n.midi == midi and vel_lo <= n.vel <= vel_hi]
            if not layers:
                continue
            ref = max(layers, key=lambda n: n.vel)
            p1  = ref.partial(1)
            if p1 is None or p1.A0 <= 0:
                continue
            for k in range(1, k_max + 1):
                pk = ref.partial(k)
                if pk and pk.A0 > 0:
                    matrix[i, k - 1] = 20.0 * math.log10(pk.A0 / p1.A0)
        return matrix


# ---------------------------------------------------------------------------
# VelocityModelFitter
# ---------------------------------------------------------------------------

class VelocityModelFitter(FitPlugin):
    """
    Power-law fit velocity závislostí.
    A0(k, vel) = A0_ref(k) · S(vel)^gamma_k
    attack_tau(vel) = tau_ref · v_norm^(-alpha)

    Paralelizace: ThreadPoolExecutor — scipy.optimize.curve_fit uvolňuje GIL,
    takže vlákna jsou efektivní pro CPU-bound scipy operace.
    """

    _log = get_logger(__name__, cls="VelocityModelFitter")
    ATTACK_TAU_CAP = 0.10

    def __init__(
        self,
        fit_vel_range: tuple[int, int] = (4, 7),
        note_workers: int = _NOTE_WORKERS,
    ):
        self.fit_vel_range = fit_vel_range
        self.note_workers  = note_workers

    @property
    def name(self) -> str:
        return "velocity_model"

    def fit(self, bank: BankState, weights: dict[str, float]) -> dict:
        """
        Per-nota velocity model paralelně v ThreadPoolExecutor.

        scipy.optimize.curve_fit uvolňuje GIL při numerické optimalizaci
        → ThreadPoolExecutor je efektivnější než ProcessPoolExecutor
        (žádný pickling overhead).

        TODO: implementovat — skeleton s paralelizací je hotov
        """
        log = get_logger(__name__, cls="VelocityModelFitter", method="fit")

        midi_groups: dict[int, list[NoteParams]] = {}
        for note in bank.notes.values():
            midi_groups.setdefault(note.midi, []).append(note)

        with OperationLogger(
            log, "fit",
            input={
                "midi_count": len(midi_groups),
                "fit_vel_range": self.fit_vel_range,
                "workers": self.note_workers,
            }
        ) as op:
            gamma_k:      dict[int, list[float]] = {}
            attack_alpha: dict[int, float]       = {}
            attack_tref:  dict[int, float]       = {}

            with ThreadPoolExecutor(max_workers=self.note_workers) as ex:
                futs = {
                    ex.submit(
                        self._fit_single_note_velocity,
                        midi,
                        sorted(notes, key=lambda n: n.vel),
                    ): midi
                    for midi, notes in midi_groups.items()
                }
                for fut in as_completed(futs):
                    midi = futs[fut]
                    try:
                        gk, alpha, tref = fut.result()
                        gamma_k[midi]      = gk
                        attack_alpha[midi] = alpha
                        attack_tref[midi]  = tref
                        op.progress("nota fitována", midi=midi)
                    except Exception as e:
                        op.warn("velocity fit selhal", midi=midi, error=str(e))

            op.set_output({
                "fitted": len(gamma_k),
                "failed": len(midi_groups) - len(gamma_k),
            })
            return {
                "gamma_k":      gamma_k,
                "attack_alpha": attack_alpha,
                "attack_tref":  attack_tref,
            }

    def _fit_single_note_velocity(
        self,
        midi: int,
        note_vels: list[NoteParams],
    ) -> tuple[list[float], float, float]:
        """
        Fituje gamma_k a attack_tau model pro jednu notu.
        Returns: (gamma_k_list, alpha, tau_ref)
        """
        from scipy.optimize import curve_fit

        vel_lo, vel_hi = self.fit_vel_range
        forte = [n for n in note_vels if vel_lo <= n.vel <= vel_hi]
        if len(forte) < 2:
            forte = note_vels  # fallback na všechny vrstvy

        if not forte:
            k_max = 60
            return [1.0] * k_max, 0.3, 0.05

        k_max = max((n.n_partials for n in note_vels), default=0)
        if k_max == 0:
            return [], 0.3, 0.05

        # --- gamma_k per harmonický index ---
        def power_law(v_norm, gamma):
            return np.power(np.maximum(v_norm, 1e-9), gamma)

        # Referenční spektrální tvar z ff vrstvy (vel 7, nebo nejvyšší dostupná)
        ref_note = max(forte, key=lambda n: n.vel)

        gamma_k_list: list[float] = []
        for k in range(1, k_max + 1):
            # Sbírej A0(k) přes velocity vrstvy
            v_norms, A0_vals = [], []
            ref_p = ref_note.partial(k)
            if ref_p is None or ref_p.A0 <= 0:
                gamma_k_list.append(1.0)
                continue

            A0_ref = ref_p.A0
            for n in note_vels:
                p = n.partial(k)
                if p and p.A0 > 0 and p.fit_quality >= 0.5:
                    v_norms.append((n.vel + 1) / 8.0)
                    A0_vals.append(p.A0 / A0_ref)

            if len(v_norms) < 2:
                gamma_k_list.append(1.0)
                continue

            try:
                popt, _ = curve_fit(
                    power_law,
                    np.array(v_norms),
                    np.array(A0_vals),
                    p0=[1.0],
                    bounds=(0.0, 5.0),
                    maxfev=200,
                )
                gamma_k_list.append(float(np.clip(popt[0], 0.05, 4.0)))
            except Exception:
                gamma_k_list.append(1.0)

        # --- attack_tau power-law fit ---
        v_norms_att = [(n.vel + 1) / 8.0 for n in forte]
        tau_vals    = [min(n.attack_tau, self.ATTACK_TAU_CAP) for n in forte]

        vel_ref = (4 + 1) / 8.0
        tau_ref_idx = min(range(len(forte)),
                          key=lambda i: abs(forte[i].vel - 4))
        tau_ref = tau_vals[tau_ref_idx]

        def tau_model(v_norm, alpha):
            return tau_ref * np.power(np.maximum(v_norm / vel_ref, 1e-9), -alpha)

        alpha = 0.3
        if len(v_norms_att) >= 2:
            try:
                popt, _ = curve_fit(
                    tau_model,
                    np.array(v_norms_att),
                    np.array(tau_vals),
                    p0=[0.3],
                    bounds=(0.0, 2.0),
                    maxfev=200,
                )
                alpha = float(np.clip(popt[0], 0.05, 1.5))
            except Exception:
                pass

        return gamma_k_list, alpha, tau_ref

    def predict_A0(
        self, gamma_k: float, A0_ref: float, vel: int, vel_ref: int = 4
    ) -> float:
        """A0(vel) = A0_ref * (S(vel)/S(vel_ref))^gamma_k"""
        s_vel = (vel + 1) / 8.0
        s_ref = (vel_ref + 1) / 8.0
        return A0_ref * math.pow(s_vel / s_ref, gamma_k)

    def predict_attack_tau(
        self, tau_ref: float, alpha: float, vel: int, vel_ref: int = 4
    ) -> float:
        """tau(vel) = tau_ref * (v/v_ref)^(-alpha), clampováno na ATTACK_TAU_CAP."""
        v_norm = (vel + 1) / 8.0
        v_ref  = (vel_ref + 1) / 8.0
        raw    = tau_ref * math.pow(v_norm / v_ref, -alpha)
        return min(raw, self.ATTACK_TAU_CAP)


# ---------------------------------------------------------------------------
# RelationFitter — orchestrátor
# ---------------------------------------------------------------------------

class RelationFitter:
    """
    Orchestruje fitting pluginy. Pluginy běží paralelně v ThreadPoolExecutor
    — jsou nezávislé a scipy uvolňuje GIL.

    Použití:
        fitter = RelationFitter()
        result = fitter.fit_all(bank, anchor_db)
    """

    _log = get_logger(__name__, cls="RelationFitter")

    DEFAULT_PLUGINS: list[type[FitPlugin]] = [
        BCurveFitter,
        DampingLawFitter,
        SpectralShapeFitter,
        VelocityModelFitter,
    ]

    def __init__(
        self,
        plugins: Optional[list[FitPlugin]] = None,
        sigma_threshold: float = 2.5,
        plugin_workers: int = _PLUGIN_WORKERS,
    ):
        """
        Args:
            plugins:        Custom seznam pluginů. None = DEFAULT_PLUGINS.
            sigma_threshold: MAD-sigma práh pro outlier skóre.
            plugin_workers:  Počet paralelních pluginů (ThreadPool).
        """
        self.plugins         = plugins or [cls() for cls in self.DEFAULT_PLUGINS]
        self.sigma_threshold = sigma_threshold
        self.plugin_workers  = plugin_workers
        self._log.debug(
            f"inicializován  plugins={[p.name for p in self.plugins]}  "
            f"plugin_workers={plugin_workers}"
        )

    def fit_all(
        self,
        bank: BankState,
        anchor_db: Optional[AnchorDatabase] = None,
    ) -> FitResult:
        """
        Spustí všechny pluginy paralelně a vrátí FitResult.

        Paralelizace: ThreadPoolExecutor — pluginy jsou nezávislé,
        scipy operace uvnitř uvolňují GIL.

        Selhání jednoho pluginu nezastaví ostatní — zaznamená se
        WARNING a plugin je přeskočen.

        TODO: implementovat — skeleton s paralelizací je hotov
        """
        with OperationLogger(
            self._log, "fit_all",
            input={
                "notes":       bank.note_count(),
                "plugins":     [p.name for p in self.plugins],
                "anchor_db":   anchor_db.name if anchor_db else None,
                "plugin_workers": self.plugin_workers,
            }
        ) as op:
            weights = self.anchor_weights(bank, anchor_db)
            non_unit = sum(1 for w in weights.values() if w != 1.0)
            op.progress("váhy připraveny", non_unit=non_unit)

            partial_results: list[dict] = []

            # Paralelní spuštění pluginů
            with ThreadPoolExecutor(max_workers=self.plugin_workers) as ex:
                futs = {
                    ex.submit(plugin.fit, bank, weights): plugin
                    for plugin in self.plugins
                }
                for fut in as_completed(futs):
                    plugin = futs[fut]
                    try:
                        result = fut.result()
                        partial_results.append(result)
                        op.progress(f"plugin hotov", plugin=plugin.name)
                    except NotImplementedError:
                        op.warn("plugin není implementován", plugin=plugin.name)
                    except Exception as e:
                        op.warn("plugin selhal", plugin=plugin.name, error=str(e))

            merged = self._merge_results(partial_results)
            scores = self._aggregate_outlier_scores(partial_results)

            outlier_count = sum(1 for s in scores.values() if s > 0.5)
            op.set_output({
                "plugins_ok":        len(partial_results),
                "outliers_above_05": outlier_count,
            })

            return FitResult(**merged, outlier_scores=scores)

    def anchor_weights(
        self,
        bank: BankState,
        anchor_db: Optional[AnchorDatabase],
    ) -> dict[str, float]:
        """Sestaví fitting váhy. None anchor_db = uniform 1.0."""
        log = get_logger(__name__, cls="RelationFitter", method="anchor_weights")
        if anchor_db is None:
            log.debug("žádná anchor DB — uniform váhy 1.0")
            return {k: 1.0 for k in bank.notes}
        # Lazy import — vyhne se cirkulárním závislostem
        from anchor_manager import AnchorManager
        mgr = AnchorManager.__new__(AnchorManager)
        return mgr.to_weights(anchor_db, bank)

    def _aggregate_outlier_scores(
        self, partial_results: list[dict]
    ) -> dict[str, float]:
        """
        Agreguje outlier skóre z pluginů váženým součtem.
        Váhy: b_curve 0.30, damping 0.30, spectral 0.25, velocity 0.15.
        Normalizace na 0.0–1.0.
        """
        SOURCE_WEIGHTS = {
            "outlier_scores_b":        0.30,
            "outlier_scores_damping":  0.30,
            "outlier_scores_spectral": 0.25,
            "outlier_scores_velocity": 0.15,
        }

        # Sbírej skóre per key prefix "m{midi:03d}"
        agg: dict[str, float]       = {}
        w_sum: dict[str, float]     = {}

        for r in partial_results:
            for score_key, weight in SOURCE_WEIGHTS.items():
                scores = r.get(score_key, {})
                for note_key_prefix, score in scores.items():
                    # Normalizuj klíč na "m{midi:03d}" prefix
                    prefix = note_key_prefix.split("_")[0]
                    agg[prefix]   = agg.get(prefix, 0.0)   + weight * score
                    w_sum[prefix] = w_sum.get(prefix, 0.0) + weight

        # Finální skóre = vážený průměr, clamp na [0, 1]
        result: dict[str, float] = {}
        for prefix, total in agg.items():
            w = w_sum.get(prefix, 1.0)
            result[prefix] = min(total / w if w > 0 else 0.0, 1.0)

        return result

    def _merge_results(self, partial_results: list[dict]) -> dict:
        """
        Sloučí partial dict výsledky do jednoho dict pro FitResult(**).

        TODO: implementovat
        """
        merged = {}
        for r in partial_results:
            merged.update(r)
        return merged


# ---------------------------------------------------------------------------
# Top-level funkce pro ProcessPoolExecutor
# ---------------------------------------------------------------------------

def _fit_damping_note(
    midi: int,
    notes_dicts: list[dict],
    weight: float,
    min_quality: float,
) -> tuple[dict, dict[str, float]]:
    """
    Fituje damping law pro jednu notu v samostatném procesu.
    Přijímá plain dict (pickle-friendly), vrací plain dict.

    TODO: implementovat — zavolá DampingLawFitter()._fit_single_note()
    """
    from models import NoteParams
    notes = [NoteParams(**d) for d in notes_dicts]
    fitter = DampingLawFitter(min_quality=min_quality)
    params, residuals = fitter._fit_single_note(notes, weight)
    return params.model_dump(), residuals
