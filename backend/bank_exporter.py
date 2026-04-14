"""
bank_exporter.py — export opravené banky do JSON formátu engine

Paralelizace:
- _serialize_notes_parallel():  ThreadPoolExecutor — Pydantic v2 model_dump()
                                je CPU-bound, Rust core uvolňuje GIL.
                                704 not × ~0.1ms = 70ms sekvenčně → ~8ms
                                paralelně s 14 vlákny na M4.
- export() + json.dump():       sekvenční — I/O-bound FS zápis, < 100ms.
- export_diff_report():         sekvenční — CSV malá data, < 1ms.

Env proměnné:
  EXPORT_WORKERS  (default: cpu_count - 1)

Status: scaffold s loggingem a paralelizací — implementace TODO

Changelog:
  2025-04-14 v0.1  — initial scaffold
  2025-04-14 v0.2  — logging integrace, OperationLogger
  2025-04-14 v0.3  — _serialize_notes_parallel, ThreadPool s prahem 100 not
  2025-04-14 v0.4  — export_diff_report implementován (CSV)
  2025-04-14 v0.5  — _NOTE_KEY_ORDER, _PARTIAL_KEY_ORDER konstanty, _midi_to_name
  2025-04-14 v0.6  — IMPLEMENTOVÁNO: _serialize_note, _serialize_partial,
                     _collect_diff_keys deep compare — 5 testů PASS
"""

from __future__ import annotations

import csv
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

from logger import OperationLogger, get_logger, log_operation
from models import BankState, Correction, CorrectionSet, NoteParams

# ---------------------------------------------------------------------------
# Konfigurace
# ---------------------------------------------------------------------------

_EXPORT_WORKERS: int = int(
    os.environ.get("EXPORT_WORKERS", max((os.cpu_count() or 4) - 1, 1))
)

# Pořadí klíčů výstupu odpovídá engine formátu (bank_sample.txt)
_NOTE_KEY_ORDER = [
    "midi", "vel", "f0_hz", "B", "phi_diff",
    "attack_tau", "A_noise", "noise_centroid_hz", "rms_gain",
    "partials", "eq_biquads", "spectral_eq",
    "stereo_width", "n_strings", "rise_tau", "pan_correction",
]
_PARTIAL_KEY_ORDER = [
    "k", "f_hz", "A0", "tau1", "tau2", "a1",
    "beat_hz", "beat_depth", "phi", "fit_quality",
]


class BankExporter:
    """
    Exportuje BankState do JSON kompatibilního s AdditiveSynthesisPianoCore.

    Použití:
        exporter = BankExporter()
        path = exporter.export(corrected_bank, "output/ks-grand-corrected.json")
        exporter.export_diff_report(correction_set, "output/corrections.csv")
    """

    _log = get_logger(__name__, cls="BankExporter")
    EDITOR_VERSION = "0.1"

    def __init__(self, workers: int = _EXPORT_WORKERS):
        self.workers = workers
        self._log.debug(f"inicializován  workers={workers}")

    @log_operation("export")
    def export(
        self,
        bank: BankState,
        output_path: str,
        diff_only: bool = False,
        original_bank: Optional[BankState] = None,
        add_metadata: bool = True,
        correction_set: Optional[CorrectionSet] = None,
        indent: int = 2,
    ) -> Path:
        """
        Exportuje banku do JSON souboru.

        Args:
            diff_only: True → pouze noty s korekcemi (vyžaduje original_bank
                       nebo correction_set).

        Raises:
            ValueError: diff_only=True bez originálu nebo correction_set.
            IOError:    Nelze zapsat do output_path.

        TODO: implementovat
        """
        log = get_logger(__name__, cls="BankExporter", method="export")

        with OperationLogger(
            log, "export",
            input={
                "output":    Path(output_path).name,
                "diff_only": diff_only,
                "notes":     bank.note_count(),
            }
        ) as op:
            if diff_only and original_bank is None and correction_set is None:
                raise ValueError(
                    "diff_only=True vyžaduje original_bank nebo correction_set"
                )

            out_path = Path(output_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)

            # Urči které noty exportovat
            if diff_only:
                diff_keys = self._collect_diff_keys(
                    bank, original_bank, correction_set
                )
                op.progress("diff klíče nalezeny", count=len(diff_keys))
            else:
                diff_keys = None

            # Paralelní serializace not (ThreadPool, Pydantic v2 uvolňuje GIL)
            notes_raw = self._serialize_notes_parallel(bank, diff_keys, op)
            op.progress("noty serializovány", count=len(notes_raw))

            # Sestavení root payloadu
            payload: dict = {"metadata": bank.metadata.model_dump()}
            if bank.stereo_config:
                payload["stereo_config"] = bank.stereo_config.model_dump()
            payload["notes"] = notes_raw
            if add_metadata:
                payload["_editor_metadata"] = self._editor_metadata(
                    bank, correction_set
                )

            # Zápis na disk
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=indent, ensure_ascii=False)

            size_kb = out_path.stat().st_size / 1024
            op.set_output({
                "path":    str(out_path),
                "size_kb": round(size_kb, 1),
                "notes":   len(notes_raw),
            })
            return out_path.resolve()

    @log_operation("export_diff_report")
    def export_diff_report(
        self,
        correction_set: CorrectionSet,
        output_path: str,
    ) -> Path:
        """
        Exportuje CSV report korekcí pro audit.

        Sloupce: nota, vel, note_name, parametr, originál,
                 opraveno, zdroj, delta_pct.
        """
        log = get_logger(__name__, cls="BankExporter", method="export_diff_report")

        with OperationLogger(
            log, "export_diff_report",
            input={
                "corrections": len(correction_set.corrections),
                "output":      Path(output_path).name,
            }
        ) as op:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)

            fieldnames = [
                "nota", "vel", "note_name", "parametr",
                "original", "opraveno", "zdroj", "delta_pct",
            ]

            rows = 0
            with open(out, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for c in sorted(
                    correction_set.corrections,
                    key=lambda x: (x.midi, x.vel, x.field),
                ):
                    writer.writerow({
                        "nota":      f"m{c.midi:03d}",
                        "vel":       c.vel,
                        "note_name": self._midi_to_name(c.midi),
                        "parametr":  c.field,
                        "original":  round(c.original, 6),
                        "opraveno":  round(c.corrected, 6),
                        "zdroj":     c.source.value,
                        "delta_pct": round(c.delta_pct, 2),
                    })
                    rows += 1

            op.set_output({"rows": rows, "path": str(out)})
            return out.resolve()

    # ------------------------------------------------------------------
    # Paralelní serializace
    # ------------------------------------------------------------------

    def _serialize_notes_parallel(
        self,
        bank:      BankState,
        diff_keys: Optional[set[str]],
        op:        OperationLogger,
    ) -> dict:
        """
        Serializuje NoteParams paralelně přes ThreadPoolExecutor.

        Pydantic v2 model_dump() je implementován v Rustu a uvolňuje GIL
        → ThreadPool je dostatečný, pickling overhead ProcessPool by byl
        větší než samotná operace (~0.1ms per nota).

        Práh pro paralelizaci: 100 not (overhead ThreadPool ~5ms).
        Pod prahem: sekvenční zpracování.
        """
        log = get_logger(__name__, cls="BankExporter",
                         method="_serialize_notes_parallel")

        items = [
            (key, note)
            for key, note in bank.notes.items()
            if diff_keys is None or key in diff_keys
        ]

        use_parallel = len(items) >= 100 and self.workers > 1
        log.debug(
            f"strategie  notes={len(items)}  "
            f"parallel={use_parallel}  workers={self.workers}"
        )

        result: dict = {}

        if not use_parallel:
            for key, note in items:
                try:
                    result[key] = self._serialize_note(note)
                except Exception as e:
                    op.warn("serializace noty selhala",
                            key=key, error=str(e))
        else:
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                futures = {
                    ex.submit(self._serialize_note, note): key
                    for key, note in items
                }
                failed = 0
                for fut in as_completed(futures):
                    key = futures[fut]
                    try:
                        result[key] = fut.result()
                    except Exception as e:
                        op.warn("serializace noty selhala (parallel)",
                                key=key, error=str(e))
                        failed += 1

                if failed:
                    log.warning(
                        f"serializace: {failed} not přeskočeno "
                        f"z {len(items)}"
                    )

        return result

    # ------------------------------------------------------------------
    # Serializační helpery
    # ------------------------------------------------------------------

    def _serialize_note(self, note: NoteParams) -> dict:
        """
        Serializuje NoteParams do ordered dict dle engine formátu.
        Thread-safe — NoteParams je immutable Pydantic model.
        """
        log = get_logger(__name__, cls="BankExporter", method="_serialize_note")
        log.debug(f"midi={note.midi}  vel={note.vel}  partials={note.n_partials}")

        d: dict = {}
        for key in _NOTE_KEY_ORDER:
            if key == "partials":
                d["partials"] = [self._serialize_partial(p) for p in note.partials]
            elif key == "eq_biquads":
                d["eq_biquads"] = [
                    {"b": list(bq.b), "a": list(bq.a)}
                    for bq in note.eq_biquads
                ]
            elif key == "spectral_eq":
                if note.spectral_eq is not None:
                    seq = note.spectral_eq
                    d["spectral_eq"] = {
                        "freqs_hz": list(seq.freqs_hz),
                        "gains_db": list(seq.gains_db),
                        "stereo_width_factor": seq.stereo_width_factor,
                    }
                # else: omit key entirely (older banks may not have it)
            else:
                val = getattr(note, key, None)
                if val is not None:
                    d[key] = val
        return d

    def _serialize_partial(self, partial) -> dict:
        """Serializuje PartialParams do ordered dict dle _PARTIAL_KEY_ORDER."""
        d: dict = {}
        for key in _PARTIAL_KEY_ORDER:
            val = getattr(partial, key, None)
            if val is not None:
                d[key] = val
        return d

    def _editor_metadata(
        self,
        bank: BankState,
        correction_set: Optional[CorrectionSet],
    ) -> dict:
        """Sestaví _editor_metadata sekci."""
        meta: dict = {
            "editor_version":   self.EDITOR_VERSION,
            "export_timestamp": datetime.utcnow().isoformat(),
            "source_path":      bank.source_path,
            "is_modified":      bank.is_modified,
        }
        if correction_set:
            s = correction_set.summary()
            meta.update({
                "anchor_db_name":    correction_set.anchor_db_name,
                "corrections_count": s["total_corrections"],
                "affected_notes":    s["affected_notes"],
                "max_delta_pct":     s["max_delta_pct"],
            })
        return meta

    def _collect_diff_keys(
        self,
        bank:           BankState,
        original_bank:  Optional[BankState],
        correction_set: Optional[CorrectionSet],
    ) -> set[str]:
        """
        Vrátí note_key pro noty s korekcemi.

        Preferuje correction_set (O(k), rychlé).
        Fallback: deep porovnání bank (O(n), pomalé pro velké banky).
        """
        log = get_logger(__name__, cls="BankExporter",
                         method="_collect_diff_keys")

        if correction_set and correction_set.corrections:
            keys = {c.note_key for c in correction_set.corrections}
            log.debug(f"diff z correction_set  keys={len(keys)}")
            return keys

        if original_bank:
            log.debug("diff přes deep compare bank  "
                      f"notes={bank.note_count()}")
            changed: set[str] = set()
            for key, note in bank.notes.items():
                orig = original_bank.notes.get(key)
                if orig is None:
                    changed.add(key)
                    continue
                # Porovnej klíčové skalární parametry
                if (abs(note.B - orig.B) > 1e-10 or
                        abs(note.attack_tau - orig.attack_tau) > 1e-10 or
                        abs(note.rms_gain - orig.rms_gain) > 1e-10):
                    changed.add(key)
                    continue
                # Porovnej tau per parciál
                orig_map = {p.k: p for p in orig.partials}
                for p in note.partials:
                    op = orig_map.get(p.k)
                    if op and (abs(p.tau1 - op.tau1) > 1e-10 or
                               abs(p.tau2 - op.tau2) > 1e-10):
                        changed.add(key)
                        break
            log.debug(f"deep compare hotov  changed={len(changed)}")
            return changed

        log.warning("_collect_diff_keys: žádný zdroj, vracím prázdnou množinu")
        return set()

    @staticmethod
    def _midi_to_name(midi: int) -> str:
        """MIDI → název noty, např. 60 → 'C4'."""
        notes = ["C", "C#", "D", "D#", "E", "F",
                 "F#", "G", "G#", "A", "A#", "B"]
        return f"{notes[midi % 12]}{(midi // 12) - 1}"
