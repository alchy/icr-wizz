"""
tension_manifold.py — multidimenzionální korekce parametrů pomocí anchor interpolace

Anchor noty definují "manifold" správných parametrů. Non-anchor noty se
projektují na manifold s tenzí úměrnou vzdálenosti a anchor score.

Pro každou non-anchor notu:
  1. Najdi N nejbližších anchor not (MIDI vzdálenost)
  2. Interpoluj "ideální" parametrový vektor z anchor vektorů
  3. corrected = original + tension × (interpolated - original)
  4. tension = f(anchor_score, MIDI_distance, outlier_score)
"""

from __future__ import annotations

import math
import numpy as np
from typing import Optional

from logger import get_logger, OperationLogger
from models import (
    AnchorDatabase, AnchorEntry, BankState, Correction,
    CorrectionSet, CorrectionSource, NoteParams,
)


_log = get_logger(__name__, cls="TensionManifold")

# Parametry které se korekují (skalární per nota)
SCALAR_PARAMS = ["B", "rms_gain", "attack_tau", "A_noise", "noise_centroid_hz"]

# Parametry per parciál
PARTIAL_PARAMS = ["A0", "tau1", "tau2", "a1", "beat_hz"]


def _note_vector(note: NoteParams, k_max: int) -> dict[str, float]:
    """Extrahuj parametrový vektor z noty — skalární + per-parciál."""
    vec: dict[str, float] = {}
    # Skalární
    vec["B"] = note.B
    vec["rms_gain"] = note.rms_gain
    vec["attack_tau"] = note.attack_tau
    vec["A_noise"] = note.A_noise
    vec["noise_centroid_hz"] = note.noise_centroid_hz

    # Per parciál
    for p in note.partials:
        if p.k > k_max:
            break
        for param in PARTIAL_PARAMS:
            vec[f"{param}_k{p.k}"] = getattr(p, param)

    return vec


def _interpolate_vectors(
    vectors: list[dict[str, float]],
    weights: list[float],
) -> dict[str, float]:
    """Váhovaný průměr parametrových vektorů."""
    if not vectors:
        return {}
    w_sum = sum(weights)
    if w_sum < 1e-12:
        return vectors[0]

    result: dict[str, float] = {}
    all_keys = set()
    for v in vectors:
        all_keys.update(v.keys())

    for key in all_keys:
        vals = []
        ws = []
        for v, w in zip(vectors, weights):
            if key in v:
                vals.append(v[key])
                ws.append(w)
        if vals:
            w_total = sum(ws)
            result[key] = sum(v * w for v, w in zip(vals, ws)) / max(w_total, 1e-12)

    return result


def _midi_distance_weight(midi_a: int, midi_b: int, falloff: float = 12.0) -> float:
    """Gaussovská váha podle MIDI vzdálenosti. falloff = půl-šířka v půltónech."""
    d = abs(midi_a - midi_b)
    return math.exp(-0.5 * (d / falloff) ** 2)


def propose_tension_corrections(
    bank: BankState,
    anchor_db: AnchorDatabase,
    tension: float = 0.5,
    falloff: float = 12.0,
    min_delta_pct: float = 1.0,
    max_delta_pct: float = 200.0,
    k_max: int = 60,
    n_neighbors: int = 8,
) -> CorrectionSet:
    """
    Navrhne korekce pro všechny non-anchor noty interpolací z anchor vektorů.

    Args:
        bank: zdrojová banka
        anchor_db: databáze anchor not
        tension: 0.0 = žádná korekce, 1.0 = plná projekce na manifold
        falloff: šířka Gaussova jádra v půltónech (12 = oktáva)
        min_delta_pct: minimální delta % pro zahrnutí korekce
        k_max: max parciálů pro vektorizaci
        n_neighbors: max počet anchor not pro interpolaci
    """
    log = get_logger(__name__, method="propose_tension")

    with OperationLogger(log, "propose_tension", input={
        "notes": bank.note_count(),
        "anchors": len(anchor_db.entries),
        "tension": tension,
        "falloff": falloff,
    }) as op:
        if not anchor_db.entries:
            op.warn("žádné anchor noty")
            return CorrectionSet(corrections=[], description="tension: no anchors")

        # Seskup anchor entries per (midi, vel) — vezmi nejvyšší score
        anchor_map: dict[tuple[int, int], float] = {}
        for e in anchor_db.entries:
            key = (e.midi, e.vel)
            if key not in anchor_map or e.score > anchor_map[key]:
                anchor_map[key] = e.score

        # Expanduj wildcard vel=-1 na všechny velocity
        expanded: dict[tuple[int, int], float] = {}
        for (midi, vel), score in anchor_map.items():
            if vel == -1:
                for v in range(8):
                    k = (midi, v)
                    if k not in expanded or score > expanded[k]:
                        expanded[k] = score
            else:
                if (midi, vel) not in expanded or score > expanded[(midi, vel)]:
                    expanded[(midi, vel)] = score
        anchor_map = expanded

        # Pre-compute anchor vektory
        anchor_vectors: dict[tuple[int, int], dict[str, float]] = {}
        anchor_scores: dict[tuple[int, int], float] = {}
        for (midi, vel), score in anchor_map.items():
            note = bank.get_note(midi, vel)
            if note is None:
                continue
            anchor_vectors[(midi, vel)] = _note_vector(note, k_max)
            anchor_scores[(midi, vel)] = score

        if not anchor_vectors:
            op.warn("žádné anchor noty nalezeny v bance")
            return CorrectionSet(corrections=[], description="tension: no anchor notes in bank")

        anchor_midis = sorted(set(m for m, v in anchor_vectors))
        op.progress("anchor vektory", count=len(anchor_vectors))

        # Pro každou non-anchor notu: interpoluj a navrhni korekce
        corrections: list[Correction] = []

        for note in bank.notes.values():
            key = (note.midi, note.vel)
            if key in anchor_scores:
                continue  # anchor nota — nekorektovat

            # Najdi nejbližší anchor noty pro tuto velocity (nebo jakoukoli)
            neighbors: list[tuple[tuple[int, int], float, float]] = []  # (key, score_weight, dist_weight)
            for a_key, score in anchor_scores.items():
                a_midi, a_vel = a_key
                # Preferuj stejnou velocity, ale akceptuj jakoukoli
                vel_penalty = 1.0 if a_vel == note.vel else 0.5
                dist_w = _midi_distance_weight(note.midi, a_midi, falloff)
                score_w = 0.1 + 0.9 * (score / 9.0)  # score 0→0.1, 9→1.0
                combined_w = dist_w * score_w * vel_penalty
                if combined_w > 0.01:  # ignoruj vzdálené/slabé
                    neighbors.append((a_key, combined_w, dist_w))

            if not neighbors:
                continue

            # Seřaď a vezmi top N
            neighbors.sort(key=lambda x: -x[1])
            neighbors = neighbors[:n_neighbors]

            # Interpoluj cílový vektor
            vecs = [anchor_vectors[n[0]] for n in neighbors]
            ws = [n[1] for n in neighbors]
            target = _interpolate_vectors(vecs, ws)

            # Originální vektor
            orig_vec = _note_vector(note, k_max)

            # Navrhni korekce
            for param_key, target_val in target.items():
                orig_val = orig_vec.get(param_key)
                if orig_val is None:
                    continue
                if abs(orig_val) < 1e-15 and abs(target_val) < 1e-15:
                    continue

                # Aplikuj tenzi — log blend pro multiplikativní parametry
                base = param_key.split("_k")[0]
                LOG_BLEND = {"B", "rms_gain", "attack_tau", "A_noise",
                             "A0", "tau1", "tau2"}
                if base in LOG_BLEND and orig_val > 0 and target_val > 0:
                    import math
                    log_o = math.log(orig_val)
                    log_t = math.log(target_val)
                    corrected = math.exp(log_o + tension * (log_t - log_o))
                else:
                    corrected = orig_val + tension * (target_val - orig_val)

                # Delta
                denom = max(abs(orig_val), abs(target_val), 1e-15)
                delta_pct = (corrected - orig_val) / denom * 100
                if abs(delta_pct) < min_delta_pct:
                    continue
                if abs(delta_pct) > max_delta_pct:
                    # Clamp extrémní korekce
                    sign = 1 if delta_pct > 0 else -1
                    corrected = orig_val * (1 + sign * max_delta_pct / 100)
                    delta_pct = sign * max_delta_pct

                # Mapuj param_key na Correction field
                corrections.append(Correction(
                    midi=note.midi,
                    vel=note.vel,
                    field=param_key,
                    original=orig_val,
                    corrected=corrected,
                    source=CorrectionSource.ANCHOR_INTERP,
                ))

        # Deduplicate (stejný midi+vel+field — vezmi větší delta)
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
            description=f"tension manifold: tension={tension}, falloff={falloff}, anchors={len(anchor_vectors)}",
        )
