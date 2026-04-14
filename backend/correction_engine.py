"""
correction_engine.py — navrhování a aplikace korekcí parametrů

Paralelizace:
- propose:  ThreadPoolExecutor — per-nota korekce jsou nezávislé
            (88 not × 3 typy korekcí, žádný sdílený stav)
- apply:    sekvenční — deep copy + field patch je rychlý (<10ms pro 704 not)
- diff:     sekvenční — porovnání dvou BankState je O(n) string compare

Nikdy nemodifikuje originální BankState in-place.

Status: scaffold s loggingem a paralelizací — implementace TODO

Changelog:
  2025-04-14 v0.1  — initial scaffold
  2025-04-14 v0.2  — logging integrace, ThreadPool skeleton v propose()
  2025-04-14 v0.3  — propose() → CorrectionSet místo list[Correction]
  2025-04-14 v0.4  — _parse_field_name, _apply_to_note skeleton
  2025-04-14 v0.5  — IMPLEMENTOVÁNO: _propose_B_correction, _propose_tau_corrections,
                     _propose_attack_tau_correction, _apply_to_note, _diff_note
  2025-04-14 v0.6  — midi_to_f0 helper, inline math imports → top-level
"""

from __future__ import annotations

import copy
import os
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from logger import OperationLogger, get_logger, log_operation
from models import (
    BankState,
    Correction,
    CorrectionSet,
    CorrectionSource,
    FitResult,
    NoteParams,
)

_NOTE_WORKERS: int = int(os.environ.get(
    "CORRECTION_WORKERS", max((os.cpu_count() or 4) - 1, 1)
))


def midi_to_f0(midi: int) -> float:
    """A440 ladění: f0 = 440 * 2^((midi-69)/12)"""
    return 440.0 * math.pow(2.0, (midi - 69) / 12.0)


class CorrectionEngine:
    """
    Navrhuje a aplikuje korekce parametrů banky.

    Všechny operace jsou non-destruktivní.

    Použití:
        engine = CorrectionEngine()
        cs     = engine.propose(bank, fit, anchor_weights)
        fixed  = engine.apply(bank, cs)
        diff   = engine.diff(bank, fixed)
    """

    _log = get_logger(__name__, cls="CorrectionEngine")
    MIN_DELTA_PCT = 0.5

    # Default correction weights per typ
    DEFAULT_WEIGHTS = {
        "b_curve": 1.0,
        "tau": 1.0,
        "attack_tau": 1.0,
        "gamma_k": 1.0,
        "beating": 1.0,
    }

    def __init__(
        self,
        outlier_threshold: float = 2.5,
        min_delta_pct: float = MIN_DELTA_PCT,
        note_workers: int = _NOTE_WORKERS,
        correction_weights: Optional[dict[str, float]] = None,
    ):
        self.outlier_threshold = outlier_threshold
        self.min_delta_pct     = min_delta_pct
        self.note_workers      = note_workers
        self.weights = {**self.DEFAULT_WEIGHTS, **(correction_weights or {})}
        self._log.debug(
            f"inicializován  outlier_threshold={outlier_threshold}  "
            f"min_delta_pct={min_delta_pct}  note_workers={note_workers}  "
            f"weights={self.weights}"
        )

    @log_operation("propose")
    def propose(
        self,
        bank: BankState,
        fit: FitResult,
        anchor_weights: Optional[dict[str, float]] = None,
    ) -> CorrectionSet:
        """
        Navrhne korekce pro outlier noty paralelně.

        Paralelizace: ThreadPoolExecutor — per-nota korekce jsou
        nezávislé operace bez sdíleného stavu.

        TODO: implementovat — skeleton s paralelizací je hotov
        """
        with OperationLogger(
            self._log, "propose",
            input={
                "notes":              bank.note_count(),
                "outlier_threshold":  self.outlier_threshold,
                "workers":            self.note_workers,
            }
        ) as op:
            # Pre-compute beat_hz statistiky per k (pro beating korekce)
            import numpy as np
            beat_per_k: dict[int, list[float]] = {}
            for n in bank.notes.values():
                if n.n_strings <= 1:
                    continue
                for p in n.partials:
                    if p.beat_hz > 0:
                        beat_per_k.setdefault(p.k, []).append(p.beat_hz)
            self._beat_stats: dict[int, tuple[float, float]] = {}
            for k, vals in beat_per_k.items():
                if len(vals) < 5:
                    continue
                med = float(np.median(vals))
                mad = float(np.median([abs(v - med) for v in vals]))
                self._beat_stats[k] = (med, mad * 1.4826 if mad > 0 else 1.0)

            # Identifikuj outlier noty (skóre je normalizováno na 0–1)
            SCORE_CUTOFF = self.outlier_threshold / 5.0
            outlier_keys = [
                k for k, s in fit.outlier_scores.items()
                if s >= SCORE_CUTOFF
            ]
            op.progress("outliery identifikovány", count=len(outlier_keys))

            all_corrections: list[Correction] = []

            with ThreadPoolExecutor(max_workers=self.note_workers) as ex:
                futs = {}
                for key in outlier_keys:
                    # Outlier scores mají prefix klíče "m060" — expanduj na noty v bance
                    m_pfx = re.match(r"m(\d+)$", key)
                    m_vel = re.match(r"m(\d+)_vel(\d+)", key)
                    if m_vel:
                        midi = int(m_vel.group(1))
                        vel  = int(m_vel.group(2))
                        note = bank.get_note(midi, vel)
                        if note is None:
                            op.warn("outlier nota není v bance", key=key)
                            continue
                        futs[ex.submit(self._propose_note_corrections, note, fit)] = key
                    elif m_pfx:
                        midi = int(m_pfx.group(1))
                        for vel in range(8):
                            note = bank.get_note(midi, vel)
                            if note is None:
                                continue
                            nk = f"m{midi:03d}_vel{vel}"
                            futs[ex.submit(self._propose_note_corrections, note, fit)] = nk
                    else:
                        continue

                for fut in as_completed(futs):
                    key = futs[fut]
                    try:
                        corrections = fut.result()
                        all_corrections.extend(corrections)
                        op.progress("nota zpracována",
                                    key=key, corrections=len(corrections))
                    except Exception as e:
                        op.warn("nota selhala", key=key, error=str(e))

            # Filtruj malé změny
            before = len(all_corrections)
            all_corrections = [
                c for c in all_corrections
                if abs(c.delta_pct) >= self.min_delta_pct
            ]
            filtered = before - len(all_corrections)
            if filtered:
                op.progress("filtrovány malé změny", count=filtered)

            op.set_output({
                "corrections":     len(all_corrections),
                "affected_notes":  len({c.note_key for c in all_corrections}),
            })
            return CorrectionSet(corrections=all_corrections)

    @log_operation("apply")
    def apply(
        self,
        bank: BankState,
        correction_set: CorrectionSet,
        selected_fields: Optional[list[str]] = None,
    ) -> BankState:
        """
        Aplikuje korekce na banku. Vrátí nový BankState.

        Sekvenční — deep copy + field patch je rychlý (~5ms pro 704 not).
        Paralelizace by přinesla pickling overhead bez reálného zisku.

        TODO: implementovat
        """
        with OperationLogger(
            self._log, "apply",
            input={
                "corrections":       len(correction_set.corrections),
                "selected_fields":   selected_fields,
            }
        ) as op:
            new_notes = copy.deepcopy(bank.notes)

            # Seskupit korekce per nota
            per_note: dict[str, list[Correction]] = {}
            for c in correction_set.corrections:
                if selected_fields and c.field not in selected_fields:
                    continue
                per_note.setdefault(c.note_key, []).append(c)

            applied = 0
            for key, corrs in per_note.items():
                if key not in new_notes:
                    self._log.warning(f"nota k opravě není v bance  key={key}")
                    continue
                new_notes[key] = self._apply_to_note(new_notes[key], corrs)
                applied += len(corrs)
                op.progress("nota opravena", key=key, fields=len(corrs))

            op.set_output({
                "applied":          applied,
                "notes_modified":   len(per_note),
            })
            return bank.model_copy(
                update={"notes": new_notes, "is_modified": True}
            )

    @log_operation("diff")
    def diff(
        self,
        original: BankState,
        corrected: BankState,
    ) -> CorrectionSet:
        """
        Vrátí diff jako CorrectionSet pro DiffPreview.

        TODO: implementovat porovnání numerických polí
        """
        with OperationLogger(
            self._log, "diff",
            input={"original_notes": original.note_count()}
        ) as op:
            corrections: list[Correction] = []

            for key, orig_note in original.notes.items():
                corr_note = corrected.notes.get(key)
                if corr_note is None:
                    op.warn("nota chybí v opravené bance", key=key)
                    continue

                note_corrs = self._diff_note(orig_note, corr_note)
                corrections.extend(note_corrs)

            op.set_output({"diff_corrections": len(corrections)})
            return CorrectionSet(
                corrections=corrections,
                description="diff: original → corrected",
            )

    # ------------------------------------------------------------------
    # Per-nota korekce (volány paralelně z propose)
    # ------------------------------------------------------------------

    def _propose_note_corrections(
        self, note: NoteParams, fit: FitResult
    ) -> list[Correction]:
        """
        Navrhne všechny korekce pro jednu notu.
        Agreguje B, tau a attack_tau návrhy.

        TODO: implementovat
        """
        corrections = []
        c = self._propose_B_correction(note, fit)
        if c:
            corrections.append(c)
        corrections.extend(self._propose_tau_corrections(note, fit))
        c = self._propose_attack_tau_correction(note, fit)
        if c:
            corrections.append(c)
        corrections.extend(self._propose_gamma_corrections(note, fit))
        corrections.extend(self._propose_beating_corrections(note, fit))

        # Aplikuj correction weights — blend original → corrected
        weighted = []
        for c in corrections:
            w = self._weight_for(c.field)
            if w <= 0:
                continue
            if w >= 1.0:
                weighted.append(c)
            else:
                blended = c.original + w * (c.corrected - c.original)
                weighted.append(Correction(
                    midi=c.midi, vel=c.vel, field=c.field,
                    original=c.original, corrected=blended,
                    source=c.source,
                ))
        return weighted

    def _weight_for(self, field: str) -> float:
        """Vrátí correction weight pro dané pole."""
        if field == "B":
            return self.weights.get("b_curve", 1.0)
        if field.startswith("tau1_") or field.startswith("tau2_"):
            return self.weights.get("tau", 1.0)
        if field == "attack_tau":
            return self.weights.get("attack_tau", 1.0)
        if field.startswith("gamma_k"):
            return self.weights.get("gamma_k", 1.0)
        if field.startswith("beat_hz_"):
            return self.weights.get("beating", 1.0)
        return 1.0

    def _propose_B_correction(
        self, note: NoteParams, fit: FitResult
    ) -> Optional[Correction]:
        """
        Nahradí B hodnotou z B-curve fitu.
        B_corrected = 10^(alpha * log10(f0) + beta)
        """
        log = get_logger(__name__, cls="CorrectionEngine",
                         method="_propose_B_correction")
        if fit.b_curve is None:
            return None

        lf0   = math.log10(note.f0_hz)
        lbk   = math.log10(midi_to_f0(fit.b_curve.break_midi))
        if lf0 < lbk:
            B_pred = 10 ** (fit.b_curve.alpha_bass * lf0 + fit.b_curve.beta_bass)
        else:
            B_pred = 10 ** (fit.b_curve.alpha_treble * lf0 + fit.b_curve.beta_treble)

        if abs(B_pred - note.B) / max(note.B, 1e-12) < self.min_delta_pct / 100:
            return None

        log.debug(f"B korekce  {note.note_key}  "
                  f"orig={note.B:.6f}  pred={B_pred:.6f}")
        return Correction(
            midi=note.midi, vel=note.vel,
            field="B",
            original=note.B, corrected=B_pred,
            source=CorrectionSource.B_CURVE_FIT,
        )

    def _propose_tau_corrections(
        self, note: NoteParams, fit: FitResult
    ) -> list[Correction]:
        """
        Pro outlier parciály navrhne korekci tau1 z damping law.
        tau2 se přizpůsobí zachováním poměru tau2/tau1 z cluster mediánu.
        """
        log = get_logger(__name__, cls="CorrectionEngine",
                         method="_propose_tau_corrections")
        corrections: list[Correction] = []
        params = fit.damping.get(note.midi)
        if params is None:
            return corrections

        # Mediánový poměr tau2/tau1 pro tento register (fyzikální cluster)
        n_strings = note.n_strings
        if n_strings == 1:
            tau_ratio_ref = 15.0   # bass: typicky 10–25×
        elif n_strings == 2:
            tau_ratio_ref = 12.0   # střed: 8–20×
        else:
            tau_ratio_ref = 8.0    # výšky: 5–15×

        for p in note.partials:
            if p.fit_quality < 0.5:
                continue  # příliš nízká kvalita pro spolehlivou korekci

            # Predikce tau1 z damping law
            fk      = p.f_hz
            denom   = params.R + params.eta * fk * fk
            tau1_pred = 1.0 / max(denom, 1e-9)

            delta_frac = abs(tau1_pred - p.tau1) / max(p.tau1, 1e-9)
            if delta_frac * 100 < self.min_delta_pct:
                continue

            # tau1 korekce
            corrections.append(Correction(
                midi=note.midi, vel=note.vel,
                field=f"tau1_k{p.k}",
                original=p.tau1, corrected=tau1_pred,
                source=CorrectionSource.DAMPING_LAW,
            ))
            log.debug(f"tau1 korekce  {note.note_key}  k={p.k}  "
                      f"orig={p.tau1:.3f}  pred={tau1_pred:.3f}")

            # tau2 korekce — zachovat poměr tau2/tau1
            tau2_pred = tau1_pred * tau_ratio_ref
            if abs(tau2_pred - p.tau2) / max(p.tau2, 1e-9) * 100 >= self.min_delta_pct:
                corrections.append(Correction(
                    midi=note.midi, vel=note.vel,
                    field=f"tau2_k{p.k}",
                    original=p.tau2, corrected=tau2_pred,
                    source=CorrectionSource.DAMPING_LAW,
                ))

        return corrections

    def _propose_attack_tau_correction(
        self, note: NoteParams, fit: FitResult
    ) -> Optional[Correction]:
        """
        Nahradí attack_tau hodnotou z power-law velocity modelu
        pokud přesahuje fyzikální strop 0.10s nebo je výrazně mimo trend.
        """
        log = get_logger(__name__, cls="CorrectionEngine",
                         method="_propose_attack_tau_correction")
        CAP = 0.10

        alpha = fit.attack_alpha.get(note.midi)
        tref  = fit.attack_tref.get(note.midi)

        if alpha is not None and tref is not None:
            v_norm = (note.vel + 1) / 8.0
            v_ref  = (4 + 1) / 8.0
            tau_pred = min(tref * math.pow(v_norm / v_ref, -alpha), CAP)
        else:
            # Žádný model — jen kontroluj strop
            tau_pred = min(note.attack_tau, CAP)

        delta_frac = abs(tau_pred - note.attack_tau) / max(note.attack_tau, 1e-9)
        if delta_frac * 100 < self.min_delta_pct:
            return None

        log.debug(f"attack_tau korekce  {note.note_key}  "
                  f"orig={note.attack_tau:.4f}  pred={tau_pred:.4f}")
        return Correction(
            midi=note.midi, vel=note.vel,
            field="attack_tau",
            original=note.attack_tau, corrected=tau_pred,
            source=CorrectionSource.VELOCITY_MODEL,
        )

    def _propose_gamma_corrections(
        self, note: NoteParams, fit: FitResult
    ) -> list[Correction]:
        """
        Porovná γ_k noty s mediánem γ_k přes klávesnici (z FitResult.gamma_k).
        Navrhne korekci pro harmoniky kde se γ_k výrazně odchyluje od trendu.
        """
        log = get_logger(__name__, cls="CorrectionEngine",
                         method="_propose_gamma_corrections")
        corrections: list[Correction] = []

        note_gamma = fit.gamma_k.get(note.midi)
        if not note_gamma or len(note_gamma) == 0:
            return corrections

        # Vypočti medián γ_k pro každý harmonický index přes všechny MIDI noty
        import numpy as np
        all_midis = sorted(fit.gamma_k.keys())
        k_max = len(note_gamma)

        for ki in range(k_max):
            vals = [fit.gamma_k[m][ki] for m in all_midis
                    if len(fit.gamma_k[m]) > ki]
            if len(vals) < 5:
                continue

            median = float(np.median(vals))
            mad = float(np.median([abs(v - median) for v in vals]))
            sigma = mad * 1.4826 if mad > 0 else 1.0

            orig = note_gamma[ki]
            z_score = abs(orig - median) / max(sigma, 1e-9)

            if z_score < self.outlier_threshold:
                continue

            delta_frac = abs(median - orig) / max(abs(orig), 1e-9)
            if delta_frac * 100 < self.min_delta_pct:
                continue

            corrections.append(Correction(
                midi=note.midi, vel=note.vel,
                field=f"gamma_k{ki + 1}",
                original=orig, corrected=median,
                source=CorrectionSource.VELOCITY_MODEL,
            ))
            log.debug(f"gamma_k korekce  {note.note_key}  k={ki+1}  "
                      f"orig={orig:.3f}  median={median:.3f}  z={z_score:.1f}")

        return corrections

    def _propose_beating_corrections(
        self, note: NoteParams, fit: FitResult
    ) -> list[Correction]:
        """
        Porovná beat_hz parciálů s mediánem beat_hz přes klávesnici per k.
        Navrhne korekci pro outliery.
        """
        corrections: list[Correction] = []
        if note.n_strings <= 1:
            return corrections  # bass — žádný beating

        # Potřebujeme globální statistiku beat_hz per k — uloženou v self._beat_stats
        if not hasattr(self, '_beat_stats'):
            return corrections

        for p in note.partials:
            stats = self._beat_stats.get(p.k)
            if stats is None:
                continue
            median, sigma = stats
            if sigma < 1e-6:
                continue
            z = abs(p.beat_hz - median) / sigma
            if z < self.outlier_threshold:
                continue
            delta_frac = abs(median - p.beat_hz) / max(abs(p.beat_hz), 1e-9)
            if delta_frac * 100 < self.min_delta_pct:
                continue
            corrections.append(Correction(
                midi=note.midi, vel=note.vel,
                field=f"beat_hz_k{p.k}",
                original=p.beat_hz, corrected=median,
                source=CorrectionSource.SPECTRAL_SHAPE,
            ))
        return corrections

    # ------------------------------------------------------------------
    # Apply helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_to_note(
        note: NoteParams, corrections: list[Correction]
    ) -> NoteParams:
        """
        Aplikuje korekce na NoteParams. Vrátí novou instanci (immutable).
        """
        # Skalární pole přímo na NoteParams
        scalar_updates: dict = {}
        # Parciální korekce: {k: {field: value}}
        partial_updates: dict[int, dict] = {}

        for c in corrections:
            field_type, k = CorrectionEngine._parse_field_name(c.field)

            if k is None:
                # Skalární pole: B, attack_tau, rms_gain, ...
                scalar_updates[field_type] = c.corrected
            else:
                # Per-parciál: tau1_k3 → k=3, field_type="tau1"
                if k not in partial_updates:
                    partial_updates[k] = {}
                partial_updates[k][field_type] = c.corrected

        # Aplikuj parciální korekce
        if partial_updates:
            new_partials = []
            for p in note.partials:
                if p.k in partial_updates:
                    new_partials.append(
                        p.model_copy(update=partial_updates[p.k])
                    )
                else:
                    new_partials.append(p)
            scalar_updates["partials"] = new_partials

        if scalar_updates:
            return note.model_copy(update=scalar_updates)
        return note

    @staticmethod
    def _parse_field_name(field: str) -> tuple[str, Optional[int]]:
        """
        Parsuje field název na (typ, k).
            "B"          → ("B", None)
            "tau1_k3"    → ("tau1", 3)
            "attack_tau" → ("attack_tau", None)
        """
        m = re.match(r"(\w+?)_k(\d+)$", field)
        if m:
            return m.group(1), int(m.group(2))
        return field, None

    def _diff_note(
        self, original: NoteParams, corrected: NoteParams
    ) -> list[Correction]:
        """
        Porovná dvě NoteParams a vrátí korekce pro každé odlišné pole.
        Porovnává: B, attack_tau, tau1/tau2 per parciál.
        """
        corrections: list[Correction] = []

        def add(field: str, orig: float, corr: float) -> None:
            delta = abs(corr - orig) / max(abs(orig), 1e-12) * 100
            if delta >= self.min_delta_pct:
                corrections.append(Correction(
                    midi=original.midi, vel=original.vel,
                    field=field,
                    original=orig, corrected=corr,
                    source=CorrectionSource.MANUAL,
                ))

        add("B", original.B, corrected.B)
        add("attack_tau", original.attack_tau, corrected.attack_tau)

        orig_map = {p.k: p for p in original.partials}
        for p in corrected.partials:
            op = orig_map.get(p.k)
            if op is None:
                continue
            add(f"tau1_k{p.k}", op.tau1, p.tau1)
            add(f"tau2_k{p.k}", op.tau2, p.tau2)
            add(f"A0_k{p.k}",  op.A0,  p.A0)

        return corrections
