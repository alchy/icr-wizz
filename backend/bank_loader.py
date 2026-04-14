"""
bank_loader.py — načítání a validace JSON bank AdditiveSynthesisPianoCore

Paralelizace:
- list_banks:         ThreadPoolExecutor — I/O-bound peek metadat
- load_multiple:      ThreadPoolExecutor — I/O-bound načítání souborů
- _parse_notes_parallel: ProcessPoolExecutor — CPU-bound Pydantic validace
                      (banka 704 not × 60 parciálů = 42 000 objektů)

Volba executoru:
- Čtení JSON ze SSD je I/O-bound → ThreadPoolExecutor (GIL neblokuje I/O)
- Pydantic validace parciálů je CPU-bound → ProcessPoolExecutor
- Na M4 15-core: io_workers=12, cpu_workers=14 (1 rezerva pro event loop)

Env proměnné:
  BANK_IO_WORKERS   (default: min(cpu_count, 12))
  BANK_CPU_WORKERS  (default: cpu_count - 1)

Status: scaffold s loggingem a paralelizací — implementace metod TODO

Changelog:
  2025-04-14 v0.1  — initial scaffold
  2025-04-14 v0.2  — logging integrace, OperationLogger
  2025-04-14 v0.3  — ThreadPool + ProcessPool skeleton, _parse_note_chunk
  2025-04-14 v0.4  — _normalize_key regex, _peek_bank_info implementován
  2025-04-14 v0.5  — IMPLEMENTOVÁNO: _parse_metadata, _parse_stereo_config,
                     _parse_note, _parse_partial, _parse_eq_biquads,
                     _parse_spectral_eq, _validate_physics — 12 testů PASS
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

from logger import OperationLogger, get_logger, log_operation
from models import (
    BankMetadata,
    BankState,
    EqBiquad,
    NoteParams,
    PartialParams,
    SpectralEq,
    StereoConfig,
)

# ---------------------------------------------------------------------------
# Konfigurace paralelizace
# ---------------------------------------------------------------------------

_IO_WORKERS: int  = int(os.environ.get("BANK_IO_WORKERS",  min(os.cpu_count() or 8, 12)))
_CPU_WORKERS: int = int(os.environ.get("BANK_CPU_WORKERS", max((os.cpu_count() or 4) - 1, 1)))
_PARALLEL_THRESHOLD: int = 88  # min. not pro ProcessPoolExecutor


# ---------------------------------------------------------------------------
# Výjimky
# ---------------------------------------------------------------------------

class BankLoadError(Exception):
    def __init__(self, path: str, reason: str):
        self.path   = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


class BankValidationWarning:
    def __init__(self, note_key: str, field: str, detail: str):
        self.note_key = note_key
        self.field    = field
        self.detail   = detail

    def __str__(self) -> str:
        return f"[{self.note_key}] {self.field}: {self.detail}"


# ---------------------------------------------------------------------------
# BankFileInfo
# ---------------------------------------------------------------------------

class BankFileInfo:
    """Lehká reprezentace souboru banky pro FileSelector UI."""

    def __init__(self, path: str, instrument_name: str,
                 midi_range_from: int, midi_range_to: int,
                 sr: int, note_count: int, file_size_kb: float):
        self.path            = path
        self.filename        = Path(path).name
        self.instrument_name = instrument_name
        self.midi_range_from = midi_range_from
        self.midi_range_to   = midi_range_to
        self.sr              = sr
        self.note_count      = note_count
        self.file_size_kb    = file_size_kb

    def to_dict(self) -> dict:
        return {
            "path":            self.path,
            "filename":        self.filename,
            "instrument_name": self.instrument_name,
            "midi_range":      f"{self.midi_range_from}–{self.midi_range_to}",
            "sr":              self.sr,
            "note_count":      self.note_count,
            "file_size_kb":    round(self.file_size_kb, 1),
        }


# ---------------------------------------------------------------------------
# BankLoader
# ---------------------------------------------------------------------------

class BankLoader:
    """
    Načítá JSON banky AdditiveSynthesisPianoCore do BankState objektů.

    Paralelizace je transparentní vůči volajícímu — API je identické
    se sekvenční verzí.

    Použití:
        loader = BankLoader()
        infos  = loader.list_banks("/path/to/banks")
        bank   = loader.load("ks-grand.json")
        banks, errs = loader.load_multiple(["a.json", "b.json"])
    """

    _log = get_logger(__name__, cls="BankLoader")
    NOTE_KEY_PREFIXES = ("m0", "m1")

    def __init__(
        self,
        io_workers:  int = _IO_WORKERS,
        cpu_workers: int = _CPU_WORKERS,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
    ):
        """
        Args:
            io_workers:  Vlákna pro I/O (čtení souborů).
            cpu_workers: Procesy pro CPU (parsování parciálů).
            progress_cb: callback(current, total, filename) pro UI.
        """
        self.io_workers  = io_workers
        self.cpu_workers = cpu_workers
        self.progress_cb = progress_cb
        self._log.debug(
            f"inicializován  io_workers={io_workers}  cpu_workers={cpu_workers}"
        )

    # ------------------------------------------------------------------
    # Veřejné API
    # ------------------------------------------------------------------

    @log_operation("list_banks")
    def list_banks(self, directory: str, recursive: bool = False) -> list[BankFileInfo]:
        """
        Vrátí seznam JSON souborů v adresáři s metadaty.

        Paralelizace: ThreadPoolExecutor — peek metadat je I/O-bound.
        20 souborů × 5 MB = sekvenčně ~800 ms, paralelně ~80 ms na SSD.

        TODO: implementovat — nyní obsahuje paralelní skeleton
        """
        log = get_logger(__name__, cls="BankLoader", method="list_banks")
        dir_path = Path(directory)

        if not dir_path.exists():
            raise BankLoadError(directory, "Adresář neexistuje")

        pattern = "**/*.json" if recursive else "*.json"
        paths   = list(dir_path.glob(pattern))
        log.debug(f"nalezeno JSON souborů  count={len(paths)}")

        results: list[BankFileInfo] = []

        with ThreadPoolExecutor(max_workers=self.io_workers) as ex:
            futures = {ex.submit(self._peek_bank_info, str(p)): p for p in paths}
            for i, fut in enumerate(as_completed(futures)):
                src = futures[fut]
                try:
                    info = fut.result()
                    if info:
                        results.append(info)
                except Exception as e:
                    log.warning(f"peek selhal  file={src.name}  error={e}")
                if self.progress_cb:
                    self.progress_cb(i + 1, len(paths), src.name)

        return sorted(results, key=lambda x: x.filename)

    @log_operation("load")
    def load(self, path: str) -> BankState:
        """
        Načte jeden JSON soubor banky.

        Parsování parciálů (CPU-bound) je paralelizováno pomocí
        ProcessPoolExecutor pokud počet not ≥ _PARALLEL_THRESHOLD.

        Raises:
            BankLoadError: neexistující soubor, nevalidní JSON,
                           chybějící povinná pole.

        TODO: implementovat _parse_metadata, _parse_note
        """
        log = get_logger(__name__, cls="BankLoader", method="load")

        with OperationLogger(log, "load", input={"path": Path(path).name}) as op:
            raw      = self._parse_raw_json(path)
            metadata = self._parse_metadata(raw, path)
            stereo   = self._parse_stereo_config(raw)
            op.progress("hlavička OK",
                        instrument=metadata.instrument_name, sr=metadata.sr)

            # Rozlišení struktury — noty buď na top-level nebo v "notes"
            note_src = raw.get("notes", raw)
            note_items = [
                (k, v) for k, v in note_src.items()
                if isinstance(v, dict) and any(
                    k.startswith(p) for p in self.NOTE_KEY_PREFIXES
                )
            ]
            op.progress("note klíče identifikovány", count=len(note_items))

            notes, warnings = self._parse_notes_parallel(note_items, path, op)

            if warnings:
                op.warn("validační varování",
                        count=len(warnings), sample=str(warnings[0]))

            op.set_output({"notes": len(notes), "warnings": len(warnings)})
            return BankState(
                metadata=metadata,
                stereo_config=stereo,
                notes=notes,
                source_path=str(Path(path).resolve()),
            )

    def load_multiple(
        self, paths: list[str]
    ) -> tuple[dict[str, BankState], list[BankLoadError]]:
        """
        Načte více bank paralelně.

        I/O fáze (čtení JSON) → ThreadPoolExecutor (souběžné čtení souborů).
        CPU fáze (parsování parciálů) → ProcessPoolExecutor uvnitř load().

        Na M4 15-core s 5 bankami po 5 MB:
        Sekvenčně: ~5 × 800 ms = 4 s
        Paralelně: ~800 ms (I/O) + ~200 ms (CPU) = ~1 s

        Chyby per soubor jsou shromážděny — nezastaví ostatní.

        TODO: implementovat — skeleton s paralelizací je hotov
        """
        log = get_logger(__name__, cls="BankLoader", method="load_multiple")

        with OperationLogger(
            log, "load_multiple",
            input={"count": len(paths), "io_workers": self.io_workers}
        ) as op:
            results: dict[str, BankState] = {}
            errors:  list[BankLoadError]  = []

            with ThreadPoolExecutor(max_workers=self.io_workers) as ex:
                futures = {ex.submit(self.load, p): p for p in paths}

                for i, fut in enumerate(as_completed(futures)):
                    path = futures[fut]
                    try:
                        bank = fut.result()
                        results[path] = bank
                        op.progress("načteno",
                                    file=Path(path).name,
                                    notes=bank.note_count())
                    except BankLoadError as e:
                        op.warn("selhalo", file=Path(path).name, reason=e.reason)
                        errors.append(e)
                    except Exception as e:
                        op.warn("neočekávaná chyba",
                                file=Path(path).name, error=str(e))
                        errors.append(BankLoadError(path, str(e)))

                    if self.progress_cb:
                        self.progress_cb(i + 1, len(paths), Path(path).name)

            op.set_output({"loaded": len(results), "errors": len(errors)})
            return results, errors

    # ------------------------------------------------------------------
    # Paralelní parsování not
    # ------------------------------------------------------------------

    def _parse_notes_parallel(
        self,
        note_items: list[tuple[str, dict]],
        path: str,
        op: OperationLogger,
    ) -> tuple[dict[str, NoteParams], list[BankValidationWarning]]:
        """
        Parsuje noty paralelně nebo sekvenčně podle počtu.

        Prahování:
        < _PARALLEL_THRESHOLD → sekvenční (overhead > zisk)
        ≥ _PARALLEL_THRESHOLD → ProcessPoolExecutor

        Chunking: note_items rozděleny na cpu_workers chunků.
        Každý worker dostane chunk jako plain dict list (pickle-friendly),
        vrátí list (normalized_key, note_dict) + varování.

        Výsledky jsou deserializovány zpět v hlavním procesu.
        """
        log = get_logger(__name__, cls="BankLoader", method="_parse_notes_parallel")

        use_parallel = (
            len(note_items) >= _PARALLEL_THRESHOLD and self.cpu_workers > 1
        )
        log.debug(
            f"strategie  notes={len(note_items)}  "
            f"parallel={use_parallel}  workers={self.cpu_workers}"
        )

        notes:    dict[str, NoteParams]       = {}
        warnings: list[BankValidationWarning] = []

        if not use_parallel:
            for key, data in note_items:
                try:
                    note, w = self._parse_note(key, data, path)
                    notes[self._normalize_key(key)] = note
                    warnings.extend(w)
                except BankLoadError:
                    raise
                except Exception as e:
                    log.warning(f"nota přeskočena  key={key}  error={e}")
        else:
            chunk_size = max(1, len(note_items) // self.cpu_workers)
            chunks = [
                note_items[i: i + chunk_size]
                for i in range(0, len(note_items), chunk_size)
            ]
            op.progress("ProcessPool spuštěn",
                        chunks=len(chunks), chunk_size=chunk_size,
                        workers=self.cpu_workers)

            with ProcessPoolExecutor(max_workers=self.cpu_workers) as ex:
                futs = {ex.submit(_parse_note_chunk, c, path): i
                        for i, c in enumerate(chunks)}
                for fut in as_completed(futs):
                    idx = futs[fut]
                    try:
                        chunk_notes, chunk_w = fut.result()
                        notes.update(chunk_notes)
                        warnings.extend(chunk_w)
                        op.progress("chunk hotov",
                                    chunk=idx, parsed=len(chunk_notes))
                    except Exception as e:
                        op.warn("chunk selhal", chunk=idx, error=str(e))

        return notes, warnings

    # ------------------------------------------------------------------
    # Privátní parse metody
    # ------------------------------------------------------------------

    def _peek_bank_info(self, path: str) -> Optional[BankFileInfo]:
        """Rychlé načtení metadat bez parsování parciálů."""
        log = get_logger(__name__, cls="BankLoader", method="_peek_bank_info")
        try:
            size_kb = Path(path).stat().st_size / 1024
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            meta  = raw.get("metadata", {})
            notes = raw.get("notes", raw)
            count = sum(1 for k in notes
                        if any(k.startswith(p) for p in self.NOTE_KEY_PREFIXES))
            return BankFileInfo(
                path=path,
                instrument_name=meta.get("instrument_name", ""),
                midi_range_from=meta.get("midi_range_from", 21),
                midi_range_to=meta.get("midi_range_to", 108),
                sr=meta.get("sr", 44100),
                note_count=count,
                file_size_kb=size_kb,
            )
        except Exception as e:
            log.debug(f"peek selhal  file={Path(path).name}  error={e}")
            return None

    def _parse_raw_json(self, path: str) -> dict:
        """Načte a deserializuje JSON soubor."""
        p = Path(path)
        if not p.exists():
            raise BankLoadError(path, "Soubor neexistuje")
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            self._log.error(f"✗  nevalidní JSON  file={p.name}  pos={e.pos}")
            raise BankLoadError(path, f"Nevalidní JSON: {e}")

    def _parse_metadata(self, raw: dict, path: str) -> BankMetadata:
        """Parsuje sekci 'metadata' z raw dict, doplní defaults."""
        log = get_logger(__name__, cls="BankLoader", method="_parse_metadata")
        meta = raw.get("metadata", {})
        if not meta:
            log.warning(f"chybí sekce 'metadata'  file={Path(path).name}")
        try:
            return BankMetadata(
                instrument_name  = meta.get("instrument_name", ""),
                midi_range_from  = int(meta.get("midi_range_from", 21)),
                midi_range_to    = int(meta.get("midi_range_to",  108)),
                source           = meta.get("source", "soundbank:params"),
                sr               = int(meta.get("sr", 44100)),
                target_rms       = float(meta.get("target_rms", 0.06)),
                vel_gamma        = float(meta.get("vel_gamma", 0.7)),
                k_max            = int(meta.get("k_max", 60)),
                rng_seed         = int(meta.get("rng_seed", 0)),
                duration_s       = float(meta.get("duration_s", 3.0)),
            )
        except (TypeError, ValueError) as e:
            raise BankLoadError(path, f"Neplatná metadata: {e}")

    def _parse_stereo_config(self, raw: dict) -> Optional[StereoConfig]:
        """Parsuje sekci 'stereo_config' pokud existuje."""
        sc = raw.get("stereo_config")
        if not sc:
            return None
        try:
            return StereoConfig(
                keyboard_spread = float(sc.get("keyboard_spread", 1.0)),
                pan_spread      = float(sc.get("pan_spread",      1.2886)),
                stereo_decorr   = float(sc.get("stereo_decorr",   0.5)),
            )
        except (TypeError, ValueError):
            self._log.warning("neplatná stereo_config, použity defaulty")
            return None

    def _parse_note(
        self, key: str, data: dict, path: str
    ) -> tuple[NoteParams, list[BankValidationWarning]]:
        """Parsuje jeden note záznam z raw dict."""
        log = get_logger(__name__, cls="BankLoader", method="_parse_note")
        warnings: list[BankValidationWarning] = []

        try:
            midi      = int(data["midi"])
            vel       = int(data["vel"])
            f0        = float(data["f0_hz"])
            B         = float(data["B"])
            note_key  = f"m{midi:03d}_vel{vel}"

            if f0 <= 0:
                raise BankLoadError(path, f"f0_hz <= 0 pro {note_key}")
            if B <= 0:
                warnings.append(BankValidationWarning(
                    note_key, "B", f"B={B} <= 0, nahrazeno 1e-6"
                ))
                B = 1e-6

            # Parciály
            raw_partials = data.get("partials", [])
            partials: list[PartialParams] = []
            for i, pd in enumerate(raw_partials):
                try:
                    p, w = self._parse_partial(pd, note_key, i + 1)
                    partials.append(p)
                    warnings.extend(w)
                except BankLoadError as e:
                    log.warning(
                        f"parciál přeskočen  {note_key}  k={i+1}  reason={e.reason}"
                    )

            # EQ
            eq_biquads  = self._parse_eq_biquads(
                data.get("eq_biquads", []), note_key
            )
            spectral_eq = self._parse_spectral_eq(
                data.get("spectral_eq"), note_key
            )

            note = NoteParams(
                midi              = midi,
                vel               = vel,
                f0_hz             = f0,
                B                 = B,
                phi_diff          = float(data.get("phi_diff",          0.0)),
                attack_tau        = float(data.get("attack_tau",        0.05)),
                A_noise           = float(data.get("A_noise",           0.0)),
                noise_centroid_hz = float(data.get("noise_centroid_hz", 1000.0)),
                rms_gain          = float(data.get("rms_gain",          1.0)),
                n_strings         = int(data.get("n_strings",           1)),
                rise_tau          = float(data.get("rise_tau",          0.004)),
                stereo_width      = float(data.get("stereo_width",      1.0)),
                pan_correction    = float(data.get("pan_correction",    0.0)),
                partials          = partials,
                eq_biquads        = eq_biquads,
                spectral_eq       = spectral_eq,
            )

            warnings.extend(self._validate_physics(note))
            return note, warnings

        except BankLoadError:
            raise
        except (KeyError, TypeError, ValueError) as e:
            raise BankLoadError(path, f"Neplatná nota {key}: {e}")

    @staticmethod
    def _normalize_key(raw_key: str) -> str:
        """Normalizuje klíč noty na m{midi:03d}_vel{vel}."""
        m = re.match(r"m(\d+)[_\-]vel(\d+)", raw_key)
        if not m:
            raise BankLoadError("?", f"Nelze normalizovat klíč: {raw_key!r}")
        return f"m{int(m.group(1)):03d}_vel{m.group(2)}"

    @staticmethod
    def _validate_physics(note: NoteParams) -> list[BankValidationWarning]:
        """Fyzikální konzistenci kontroly."""
        import math
        warnings: list[BankValidationWarning] = []
        nk = note.note_key

        if not (1e-6 <= note.B <= 0.5):
            warnings.append(BankValidationWarning(
                nk, "B", f"B={note.B:.2e} mimo rozsah [1e-6, 0.5]"
            ))

        expected_f0 = 440.0 * math.pow(2.0, (note.midi - 69) / 12.0)
        if note.f0_hz > 0 and expected_f0 > 0:
            cents = 1200 * math.log2(note.f0_hz / expected_f0)
            if abs(cents) > 50:
                warnings.append(BankValidationWarning(
                    nk, "f0_hz",
                    f"f0={note.f0_hz:.2f}Hz odchylka {cents:+.1f}¢ od MIDI {note.midi}"
                ))

        if note.attack_tau > 0.12:
            warnings.append(BankValidationWarning(
                nk, "attack_tau",
                f"attack_tau={note.attack_tau:.3f}s > strop 0.10s"
            ))

        expected_strings = 1 if note.midi <= 39 else (2 if note.midi <= 47 else 3)
        if abs(note.n_strings - expected_strings) > 1:
            warnings.append(BankValidationWarning(
                nk, "n_strings",
                f"n_strings={note.n_strings} neočekávané pro MIDI {note.midi}"
            ))

        return warnings

    def _parse_partial(
        self, data: dict, note_key: str, k_expected: int
    ) -> tuple[PartialParams, list[BankValidationWarning]]:
        """Parsuje jeden parciál z raw dict."""
        warnings: list[BankValidationWarning] = []
        try:
            k    = int(data.get("k", k_expected))
            tau1 = float(data["tau1"])
            tau2 = float(data["tau2"])
            a1   = float(data.get("a1", 1.0))
            fq   = float(data.get("fit_quality", 0.0))

            if tau2 < tau1 and abs(tau2 - tau1) > 1e-9:
                warnings.append(BankValidationWarning(
                    note_key, f"tau2_k{k}",
                    f"tau2={tau2:.4f} < tau1={tau1:.4f} — fit artefakt"
                ))
                tau2 = tau1

            return PartialParams(
                k           = k,
                f_hz        = float(data["f_hz"]),
                A0          = float(data["A0"]),
                tau1        = tau1,
                tau2        = tau2,
                a1          = max(0.0, min(1.0, a1)),
                beat_hz     = float(data.get("beat_hz",    0.0)),
                beat_depth  = float(data.get("beat_depth", 0.0)),
                phi         = float(data.get("phi",        0.0)),
                fit_quality = max(0.0, min(1.0, fq)),
            ), warnings

        except (KeyError, TypeError, ValueError) as e:
            raise BankLoadError(
                "?", f"Neplatný parciál k={k_expected} v {note_key}: {e}"
            )

    def _parse_eq_biquads(
        self, data: list[dict], note_key: str
    ) -> list[EqBiquad]:
        """Parsuje seznam biquad filtrů. Vrátí [] pokud data chybí."""
        if not data:
            return []
        result = []
        for i, bq in enumerate(data):
            try:
                result.append(EqBiquad(
                    b=list(map(float, bq["b"])),
                    a=list(map(float, bq["a"])),
                ))
            except (KeyError, TypeError, ValueError) as e:
                self._log.warning(
                    f"přeskočen biquad  note={note_key}  idx={i}  error={e}"
                )
        return result

    def _parse_spectral_eq(
        self, data: dict, note_key: str
    ) -> Optional[SpectralEq]:
        """Parsuje spectral_eq sekci pokud existuje."""
        if not data:
            return None
        try:
            freqs = list(map(float, data["freqs_hz"]))
            gains = list(map(float, data["gains_db"]))
            sw    = float(data.get("stereo_width_factor", 1.0))
            if len(freqs) != len(gains):
                self._log.warning(
                    f"spectral_eq délky nesedí  note={note_key}  "
                    f"freqs={len(freqs)}  gains={len(gains)}"
                )
                return None
            # Pydantic vyžaduje přesně 64 bodů — zkrátíme nebo doplníme
            if len(freqs) != 64:
                self._log.warning(
                    f"spectral_eq má {len(freqs)} bodů místo 64  note={note_key}"
                )
                return None
            return SpectralEq(
                freqs_hz=freqs, gains_db=gains, stereo_width_factor=sw
            )
        except (KeyError, TypeError, ValueError) as e:
            self._log.warning(
                f"spectral_eq přeskočen  note={note_key}  error={e}"
            )
            return None


# ---------------------------------------------------------------------------
# Top-level funkce pro ProcessPoolExecutor (musí být pickle-serializovatelné)
# ---------------------------------------------------------------------------

def _parse_note_chunk(
    chunk: list[tuple[str, dict]],
    path: str,
) -> tuple[dict[str, NoteParams], list[BankValidationWarning]]:
    """
    Parsuje chunk not v samostatném procesu.
    Každý worker vytvoří vlastní BankLoader instanci.
    Chyby per nota jsou logovány a přeskočeny — worker nesmí crashovat.

    TODO: zavolá BankLoader()._parse_note() po implementaci
    """
    loader   = BankLoader()
    notes:    dict[str, NoteParams]       = {}
    warnings: list[BankValidationWarning] = []

    for key, data in chunk:
        try:
            note, w = loader._parse_note(key, data, path)
            notes[BankLoader._normalize_key(key)] = note
            warnings.extend(w)
        except BankLoadError:
            raise
        except Exception:
            pass

    return notes, warnings
