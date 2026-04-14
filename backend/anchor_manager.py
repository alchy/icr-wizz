"""
anchor_manager.py — správa databáze anchor not

Paralelizace: anchor operace jsou sekvenční — databáze je malá
(desítky záznamů) a I/O je jednorázové. Paralelizace by přinesla
pouze overhead bez měřitelného zisku.

Status: scaffold s loggingem — implementace TODO

Changelog:
  2025-04-14 v0.1  — initial scaffold
  2025-04-14 v0.2  — logging integrace, COVERAGE_THRESHOLDS
  2025-04-14 v0.3  — přidány: list_databases, delete, suggest_anchors
  2025-04-14 v0.4  — immutable pattern: add_entry/remove_entry vrací novou instanci
  2025-04-14 v0.5  — IMPLEMENTOVÁNO: všechny metody — 12 testů PASS
  2025-04-14 v0.6  — anchor_manager: suggest_anchors, lazy AnchorManager import
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from logger import OperationLogger, get_logger, log_operation
from models import AnchorDatabase, AnchorEntry, BankState

COVERAGE_THRESHOLDS = {
    "bass": 3, "mid": 6, "treble": 3,
    "vel_low": 2, "vel_high": 2, "total": 10,
}


class AnchorError(Exception):
    pass

class AnchorNotFoundError(AnchorError):
    def __init__(self, name: str):
        super().__init__(f"Anchor databáze nenalezena: {name}")

class AnchorConflictError(AnchorError):
    def __init__(self, name: str):
        super().__init__(f"Databáze '{name}' již existuje.")


class AnchorManager:
    """
    Správce databází anchor not.

    Použití:
        mgr = AnchorManager("anchor-databases")
        db  = mgr.create("ks-grand-v1", "Steinway D 2024")
        db  = mgr.add_entry(db, midi=60, vel=4, score=9.0)
        mgr.save(db)
    """

    _log = get_logger(__name__, cls="AnchorManager")

    def __init__(self, anchor_dir: str = "anchor-databases"):
        self.anchor_dir = Path(anchor_dir)
        self.anchor_dir.mkdir(parents=True, exist_ok=True)
        self._log.debug(f"inicializován  dir={self.anchor_dir.resolve()}")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @log_operation("create")
    def create(
        self, name: str,
        description: Optional[str] = None,
        instrument_hint: Optional[str] = None,
    ) -> AnchorDatabase:
        """Vytvoří novou prázdnou AnchorDatabase."""
        return AnchorDatabase(
            name=name,
            description=description,
            instrument_hint=instrument_hint,
        )

    @log_operation("add_entry")
    def add_entry(
        self, db: AnchorDatabase,
        midi: int, vel: int, score: float,
        note: Optional[str] = None,
    ) -> AnchorDatabase:
        """
        Přidá nebo aktualizuje anchor záznam. Vrátí novou instanci.
        vel == -1 → wildcard pro všechny velocity vrstvy noty.
        """
        log = get_logger(__name__, cls="AnchorManager", method="add_entry")
        new_entry = AnchorEntry(midi=midi, vel=vel, score=score, note=note)
        log.debug(f"midi={midi}  vel={vel}  score={score}"
                  f"  weight={new_entry.weight:.3f}")

        # Nahradit existující záznam pro stejné (midi, vel)
        entries = [e for e in db.entries
                   if not (e.midi == midi and e.vel == vel)]
        entries.append(new_entry)
        entries.sort(key=lambda e: (e.midi, e.vel))
        return db.model_copy(update={
            "entries":  entries,
            "modified": datetime.utcnow().isoformat(),
        })

    @log_operation("remove_entry")
    def remove_entry(
        self, db: AnchorDatabase, midi: int, vel: int
    ) -> AnchorDatabase:
        """Odebere záznam. Idempotentní."""
        entries = [e for e in db.entries
                   if not (e.midi == midi and e.vel == vel)]
        removed = len(db.entries) - len(entries)
        self._log.debug(f"odebráno={removed}  midi={midi}  vel={vel}")
        return db.model_copy(update={
            "entries":  entries,
            "modified": datetime.utcnow().isoformat(),
        })

    def clear(self, db: AnchorDatabase) -> AnchorDatabase:
        """Odstraní všechny záznamy."""
        log = get_logger(__name__, cls="AnchorManager", method="clear")
        log.info(f"mazání všech záznamů  db={db.name}  count={len(db.entries)}")
        return db.model_copy(update={
            "entries":  [],
            "modified": datetime.utcnow().isoformat(),
        })

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @log_operation("save")
    def save(self, db: AnchorDatabase, overwrite: bool = True) -> Path:
        """Uloží databázi do {anchor_dir}/{db.name}.json."""
        log  = get_logger(__name__, cls="AnchorManager", method="save")
        path = self._db_path(db.name)

        if path.exists() and not overwrite:
            log.warning(f"konflikt  name={db.name}  path={path}")
            raise AnchorConflictError(db.name)

        # Aktualizuj modified timestamp
        db = db.model_copy(update={"modified": datetime.utcnow().isoformat()})
        path.write_text(db.model_dump_json(indent=2), encoding="utf-8")
        log.info(f"uloženo  path={path}  entries={len(db.entries)}")
        return path.resolve()

    @log_operation("load")
    def load(self, name_or_path: str) -> AnchorDatabase:
        """Načte databázi podle názvu nebo absolutní cesty."""
        log  = get_logger(__name__, cls="AnchorManager", method="load")
        path = (
            Path(name_or_path)
            if ("/" in name_or_path or "\\" in name_or_path)
            else self._db_path(name_or_path)
        )
        if not path.exists():
            log.error(f"✗  soubor neexistuje  path={path}")
            raise AnchorNotFoundError(name_or_path)
        try:
            raw = path.read_text(encoding="utf-8")
            db  = AnchorDatabase.model_validate_json(raw)
            log.debug(f"načteno  path={path}  entries={len(db.entries)}")
            return db
        except Exception as e:
            raise AnchorError(f"Nevalidní AnchorDatabase: {path}: {e}")

    @log_operation("list_databases")
    def list_databases(self) -> list[dict]:
        """Vrátí seznam databází v anchor_dir."""
        result = []
        for p in sorted(self.anchor_dir.glob("*.json")):
            try:
                db = self.load(str(p))
                result.append({
                    "name":         db.name,
                    "path":         str(p),
                    "description":  db.description,
                    "instrument_hint": db.instrument_hint,
                    "modified":     db.modified,
                    "entry_count":  len(db.entries),
                })
            except Exception as e:
                self._log.warning(f"přeskočen  file={p.name}  error={e}")
        return result

    @log_operation("delete")
    def delete(self, name: str) -> bool:
        """Smaže soubor databáze."""
        path = self._db_path(name)
        log  = get_logger(__name__, cls="AnchorManager", method="delete")
        if not path.exists():
            log.warning(f"soubor neexistuje  name={name}")
            return False
        path.unlink()
        log.info(f"smazáno  name={name}  path={path}")
        return True

    # ------------------------------------------------------------------
    # Export / import
    # ------------------------------------------------------------------

    @log_operation("export_json")
    def export_json(self, db: AnchorDatabase, indent: int = 2) -> str:
        """Vrátí pretty-printed JSON string."""
        return db.model_dump_json(indent=indent)

    @log_operation("import_json")
    def import_json(self, json_str: str) -> AnchorDatabase:
        """Parsuje JSON string do AnchorDatabase."""
        log = get_logger(__name__, cls="AnchorManager", method="import_json")
        log.debug(f"import  len={len(json_str)} znaků")
        try:
            return AnchorDatabase.model_validate_json(json_str)
        except Exception as e:
            raise AnchorError(f"Nevalidní AnchorDatabase JSON: {e}")

    # ------------------------------------------------------------------
    # Váhy a analýza
    # ------------------------------------------------------------------

    @log_operation("to_weights")
    def to_weights(
        self, db: AnchorDatabase, bank: BankState
    ) -> dict[str, float]:
        """Převede anchor score na fitting váhy pro RelationFitter."""
        log = get_logger(__name__, cls="AnchorManager", method="to_weights")
        with OperationLogger(
            log, "to_weights",
            input={"entries": len(db.entries), "bank_notes": bank.note_count()}
        ) as op:
            weights = {k: 1.0 for k in bank.notes}
            applied = 0

            for entry in db.entries:
                if entry.vel == -1:
                    for v in range(8):
                        key = f"m{entry.midi:03d}_vel{v}"
                        if key in weights:
                            weights[key] = entry.weight
                            applied += 1
                else:
                    key = entry.note_key
                    if key in weights:
                        weights[key] = entry.weight
                        applied += 1
                    else:
                        op.warn("anchor nota není v bance", key=key)

            non_unit = sum(1 for w in weights.values() if w != 1.0)
            op.set_output({"applied": applied, "non_unit": non_unit})
            return weights

    @log_operation("coverage_report")
    def coverage_report(
        self,
        db: AnchorDatabase,
        bank: Optional[BankState] = None,
    ) -> dict:
        """Analýza pokrytí klávesnice anchor notami."""
        bass   = sum(1 for e in db.entries if 21 <= e.midi <= 38)
        mid    = sum(1 for e in db.entries if 39 <= e.midi <= 60)
        treble = sum(1 for e in db.entries if 61 <= e.midi <= 108)
        vel_lo = sum(1 for e in db.entries if e.vel in (0, 1))
        vel_hi = sum(1 for e in db.entries if e.vel in (6, 7))
        total  = len(db.entries)

        warnings = []
        if bass   < COVERAGE_THRESHOLDS["bass"]:
            warnings.append(f"bass: {bass} < {COVERAGE_THRESHOLDS['bass']}")
        if mid    < COVERAGE_THRESHOLDS["mid"]:
            warnings.append(f"mid: {mid} < {COVERAGE_THRESHOLDS['mid']}")
        if treble < COVERAGE_THRESHOLDS["treble"]:
            warnings.append(f"treble: {treble} < {COVERAGE_THRESHOLDS['treble']}")
        if vel_lo < COVERAGE_THRESHOLDS["vel_low"]:
            warnings.append(f"vel_low: {vel_lo} < {COVERAGE_THRESHOLDS['vel_low']}")
        if vel_hi < COVERAGE_THRESHOLDS["vel_high"]:
            warnings.append(f"vel_high: {vel_hi} < {COVERAGE_THRESHOLDS['vel_high']}")
        if total  < COVERAGE_THRESHOLDS["total"]:
            warnings.append(f"total: {total} < {COVERAGE_THRESHOLDS['total']}")

        # Zkontroluj missing v bance
        if bank:
            missing = []
            for e in db.entries:
                key = (f"m{e.midi:03d}_vel{e.vel}"
                       if e.vel >= 0 else f"m{e.midi:03d}_vel0")
                if key not in bank.notes:
                    missing.append(key)
            if missing:
                warnings.append(
                    f"anchor noty nejsou v bance: {missing[:5]}"
                    + ("…" if len(missing) > 5 else "")
                )

        return {
            "bass": bass, "mid": mid, "treble": treble,
            "vel_low": vel_lo, "vel_high": vel_hi, "total": total,
            "warnings": warnings,
            "ok": len(warnings) == 0,
        }

    @log_operation("suggest_anchors")
    def suggest_anchors(
        self,
        bank: BankState,
        existing_db: Optional[AnchorDatabase] = None,
        n_suggestions: int = 15,
    ) -> list[dict]:
        """
        Navrhne anchor noty pro rovnoměrné pokrytí klávesnice.
        Prioritizuje noty s vysokým průměrným fit_quality.
        """
        log = get_logger(__name__, cls="AnchorManager",
                         method="suggest_anchors")

        existing_keys: set[str] = set()
        if existing_db:
            existing_keys = {
                f"m{e.midi:03d}_vel{e.vel}" for e in existing_db.entries
            }

        # Regiony: bass 21-38 (3), mid 39-60 (6), treble 61-108 (3)
        # Velocity: vždy nabídni vel 0, 4, 7 per nota
        region_targets = [
            ("bass",   range(21, 39),  3),
            ("mid",    range(39, 61),  6),
            ("treble", range(61, 109), 3),
        ]
        vel_targets = [0, 4, 7]

        # Skóre per nota = průměrný fit_quality přes forte vrstvy
        def note_quality(midi: int) -> float:
            forte = [bank.get_note(midi, v) for v in (4, 5, 6, 7)
                     if bank.get_note(midi, v)]
            if not forte:
                return 0.0
            quals = []
            for n in forte:
                if n.partials:
                    quals.append(sum(p.fit_quality for p in n.partials)
                                 / len(n.partials))
            return sum(quals) / len(quals) if quals else 0.0

        suggestions = []
        for region_name, midi_range, target_count in region_targets:
            # Seřaď noty v regionu podle kvality
            midis_in_region = [
                m for m in midi_range
                if any(bank.get_note(m, v) for v in range(8))
            ]
            midis_sorted = sorted(
                midis_in_region,
                key=note_quality,
                reverse=True,
            )
            added = 0
            for midi in midis_sorted:
                if added >= target_count:
                    break
                for vel in vel_targets:
                    key = f"m{midi:03d}_vel{vel}"
                    if key in existing_keys:
                        continue
                    if bank.get_note(midi, vel):
                        suggestions.append({
                            "midi":     midi,
                            "vel":      vel,
                            "region":   region_name,
                            "quality":  round(note_quality(midi), 3),
                            "reason":   f"vysoké fit_quality v {region_name}",
                            "priority": target_count - added,
                        })
                        break
                added += 1

        # Doplnění krajních velocity vrstev pokud chybí
        for vel, label in ((0, "pp"), (7, "ff")):
            covered = sum(
                1 for s in suggestions if s["vel"] == vel
            )
            if covered < 2:
                # Přidej nejlepší notu pro tuto velocity
                best = max(
                    (m for m in range(21, 109) if bank.get_note(m, vel)),
                    key=note_quality,
                    default=None,
                )
                if best and f"m{best:03d}_vel{vel}" not in existing_keys:
                    suggestions.append({
                        "midi":     best,
                        "vel":      vel,
                        "region":   "velocity_coverage",
                        "quality":  round(note_quality(best), 3),
                        "reason":   f"pokrytí {label} velocity vrstvy",
                        "priority": 1,
                    })

        log.debug(f"navrženo={len(suggestions)}")
        return suggestions[:n_suggestions]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _db_path(self, name: str) -> Path:
        n = name if name.endswith(".json") else f"{name}.json"
        return self.anchor_dir / n

    @staticmethod
    def _touch_modified(db: AnchorDatabase) -> AnchorDatabase:
        """Vrátí kopii db s aktualizovaným modified."""
        return db.model_copy(
            update={"modified": datetime.utcnow().isoformat()}
        )
