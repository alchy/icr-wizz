"""
logging_integration.py — vzorové integrace loggeru do každého modulu

Tento soubor není součástí produkčního kódu.
Slouží jako referenční dokumentace a copy-paste šablony
pro implementaci metod v jednotlivých modulech.

Každá sekce ukazuje:
  1. Import na začátku souboru
  2. Logger inicializaci v třídě
  3. Ukázku implementace metody s loggingem
"""

# =============================================================================
# bank_loader.py
# =============================================================================

"""
# --- Přidat na začátek bank_loader.py ---
from logger import get_logger, log_operation, OperationLogger

class BankLoader:
    _log = get_logger(__name__, cls="BankLoader")

    @log_operation("list_banks")
    def list_banks(self, directory: str) -> list:
        # log_operation automaticky loguje: vstup (directory=...), výstup, čas
        infos = []
        for f in Path(directory).glob("*.json"):
            info = self._peek_bank_info(str(f))
            if info:
                infos.append(info)
        return sorted(infos, key=lambda x: x.filename)

    @log_operation("load")
    def load(self, path: str) -> "BankState":
        raw = self._parse_raw_json(path)
        # ... implementace ...
        return bank_state

    def load_multiple(self, paths: list[str]):
        with OperationLogger(
            self._log, "load_multiple",
            input={"count": len(paths)}
        ) as op:
            results, errors = {}, []

            for p in paths:
                try:
                    results[p] = self.load(p)
                    op.progress(f"načteno", path=p)
                except BankLoadError as e:
                    op.warn(f"přeskočeno", path=p, reason=str(e))
                    errors.append(e)

            op.set_output({"loaded": len(results), "errors": len(errors)})
            return results, errors

    def _parse_note(self, key: str, data: dict, path: str):
        log = get_logger(__name__, cls="BankLoader", method="_parse_note")
        warnings = []

        # Fyzikální kontroly s explicitním logováním důvodu
        if data.get("B", 0) > 0.1:
            msg = f"B={data['B']:.4f} mimo realistický rozsah"
            log.warning(f"⚠  {msg}  note={key}")
            warnings.append(BankValidationWarning(key, "B", msg))

        if data.get("f0_hz", 0) <= 0:
            log.error(f"✗  f0_hz <= 0  note={key}  value={data.get('f0_hz')}")
            raise BankLoadError(path, f"Neplatná f0_hz pro notu {key}")

        # ... parsování ...
        return note_params, warnings
"""


# =============================================================================
# anchor_manager.py
# =============================================================================

"""
# --- Přidat na začátek anchor_manager.py ---
from logger import get_logger, log_operation, OperationLogger

class AnchorManager:
    _log = get_logger(__name__, cls="AnchorManager")

    def __init__(self, anchor_dir: str = "anchor-databases"):
        self.anchor_dir = Path(anchor_dir)
        self.anchor_dir.mkdir(parents=True, exist_ok=True)
        self._log.debug(f"inicializován  anchor_dir={self.anchor_dir.resolve()}")

    @log_operation("create")
    def create(self, name: str, description=None, instrument_hint=None):
        # log_operation zachytí AnchorConflictError automaticky
        ...

    @log_operation("save")
    def save(self, db: "AnchorDatabase", overwrite: bool = True) -> Path:
        path = self._db_path(db.name)
        if path.exists() and not overwrite:
            raise AnchorConflictError(db.name)
        # ... zápis ...
        self._log.info(f"uloženo  entries={len(db.entries)}  path={path}")
        return path

    @log_operation("load")
    def load(self, name_or_path: str) -> "AnchorDatabase":
        ...

    def to_weights(self, db, bank):
        log = get_logger(__name__, cls="AnchorManager", method="to_weights")
        weights = {k: 1.0 for k in bank.notes}

        applied = 0
        for entry in db.entries:
            if entry.vel == -1:
                # wildcard — aplikovat na všechny velocity vrstvy
                for vel in range(8):
                    key = f"m{entry.midi:03d}_vel{vel}"
                    if key in weights:
                        weights[key] = entry.weight
                        applied += 1
            else:
                key = entry.note_key
                if key in weights:
                    weights[key] = entry.weight
                    applied += 1
                else:
                    log.debug(f"anchor nota není v bance  key={key}")

        log.debug(f"váhy přiřazeny  applied={applied}  total={len(weights)}")
        return weights
"""


# =============================================================================
# relation_fitter.py
# =============================================================================

"""
# --- Přidat na začátek relation_fitter.py ---
from logger import get_logger, log_operation, OperationLogger

class BCurveFitter(FitPlugin):
    _log = get_logger(__name__, cls="BCurveFitter")

    def fit(self, bank, weights):
        with OperationLogger(
            self._log, "fit",
            input={"notes": bank.note_count(), "break_midi": self.break_midi}
        ) as op:
            # sestavit body
            log_f0_arr, log_B_arr, w_arr = self._collect_points(bank, weights)
            op.progress(f"body sesbírány", n=len(log_f0_arr))

            # detekce zlomu
            break_midi = self.break_midi or self._auto_detect_break(
                log_f0_arr, log_B_arr, w_arr
            )
            op.progress(f"zlom detekován", break_midi=break_midi)

            # fit segmentů
            alpha_bass, beta_bass   = self._weighted_linear_fit(...)
            alpha_tre,  beta_tre    = self._weighted_linear_fit(...)

            # residuály + outlier detekce
            residuals = self._compute_residuals(...)
            outliers  = {m: r for m, r in residuals.items() if r > self.sigma_threshold}

            if outliers:
                op.warn(
                    f"B outlierů nad prahem",
                    count=len(outliers),
                    threshold=self.sigma_threshold,
                    midis=sorted(outliers.keys())
                )

            op.set_output({
                "alpha_bass": round(alpha_bass, 3),
                "alpha_treble": round(alpha_tre, 3),
                "outliers": len(outliers),
            })
            return {"b_curve": BCurveParams(...), "outlier_scores": ...}


class RelationFitter:
    _log = get_logger(__name__, cls="RelationFitter")

    def fit_all(self, bank, anchor_db=None):
        with OperationLogger(
            self._log, "fit_all",
            input={
                "notes": bank.note_count(),
                "plugins": [p.name for p in self.plugins],
                "anchor_db": anchor_db.name if anchor_db else None,
            }
        ) as op:
            weights = self.anchor_weights(bank, anchor_db)
            op.progress(f"váhy připraveny", non_unit={
                sum(1 for w in weights.values() if w != 1.0)
            })

            partial_results = []
            for plugin in self.plugins:
                try:
                    result = plugin.fit(bank, weights)
                    partial_results.append(result)
                    op.progress(f"plugin hotov", plugin=plugin.name)
                except Exception as e:
                    op.warn(
                        f"plugin selhal, přeskočen",
                        plugin=plugin.name,
                        error=str(e)
                    )

            merged  = self._merge_results(partial_results)
            scores  = self._aggregate_outlier_scores(partial_results)
            outlier_count = sum(1 for s in scores.values() if s > 0.5)

            op.set_output({
                "plugins_ok": len(partial_results),
                "outliers_above_05": outlier_count,
            })
            return FitResult(**merged, outlier_scores=scores)
"""


# =============================================================================
# outlier_detector.py
# =============================================================================

"""
# --- Přidat na začátek outlier_detector.py ---
from logger import get_logger, log_operation, OperationLogger

class OutlierDetector:
    _log = get_logger(__name__, cls="OutlierDetector")

    def detect(self, fit: "FitResult") -> "OutlierReport":
        with OperationLogger(
            self._log, "detect",
            input={"sigma_threshold": self.sigma_threshold}
        ) as op:
            all_scores: dict[str, float] = {}

            for source, weight in self.SOURCE_WEIGHTS.items():
                residuals = self._get_residuals(fit, source)
                if not residuals:
                    op.progress(f"zdroj bez dat, přeskočen", source=source)
                    continue
                scores = self._score_from_residuals(residuals, source)
                for key, score in scores.items():
                    all_scores[key] = all_scores.get(key, 0.0) + score * weight
                op.progress(
                    f"zdroj zpracován",
                    source=source,
                    notes=len(residuals),
                    flagged=sum(1 for s in scores.values() if s > 0.5)
                )

            # Normalizuj na 0–1
            max_score = max(all_scores.values(), default=1.0)
            if max_score > 0:
                all_scores = {k: v / max_score for k, v in all_scores.items()}

            flagged = [k for k, v in all_scores.items()
                       if v >= self.sigma_threshold / 5.0]
            op.set_output({"total": len(all_scores), "flagged": len(flagged)})

            flags = self._build_flags(fit, all_scores)
            return OutlierReport(all_scores, flags, self.sigma_threshold)
"""


# =============================================================================
# correction_engine.py
# =============================================================================

"""
# --- Přidat na začátek correction_engine.py ---
from logger import get_logger, log_operation, OperationLogger

class CorrectionEngine:
    _log = get_logger(__name__, cls="CorrectionEngine")

    def propose(self, bank, fit, anchor_weights=None):
        with OperationLogger(
            self._log, "propose",
            input={
                "notes": bank.note_count(),
                "outlier_threshold": self.outlier_threshold,
            }
        ) as op:
            corrections = []
            outlier_keys = [
                k for k, s in fit.outlier_scores.items()
                if s >= self.outlier_threshold / 5.0  # normalizováno na 0-1
            ]
            op.progress(f"outlierů nalezeno", count=len(outlier_keys))

            for key in outlier_keys:
                parts   = key.split("_")
                midi    = int(parts[0][1:])
                vel     = int(parts[1][3:])
                note    = bank.get_note(midi, vel)
                if note is None:
                    op.warn(f"nota z FitResult není v bance", key=key)
                    continue

                # B korekce
                c = self._propose_B_correction(note, fit)
                if c:
                    corrections.append(c)

                # tau korekce
                tau_corrs = self._propose_tau_corrections(note, fit)
                corrections.extend(tau_corrs)

                # attack_tau korekce
                a = self._propose_attack_tau_correction(note, fit)
                if a:
                    corrections.append(a)

            # Filtrovat malé změny
            before = len(corrections)
            corrections = [c for c in corrections
                           if abs(c.delta_pct) >= self.min_delta_pct]
            filtered = before - len(corrections)
            if filtered:
                op.progress(f"filtrovány malé změny", count=filtered)

            op.set_output({
                "corrections": len(corrections),
                "affected_notes": len({c.note_key for c in corrections}),
            })
            return CorrectionSet(corrections=corrections)

    def apply(self, bank, correction_set, selected_fields=None):
        with OperationLogger(
            self._log, "apply",
            input={
                "corrections": len(correction_set.corrections),
                "selected_fields": selected_fields,
            }
        ) as op:
            import copy
            new_notes = copy.deepcopy(bank.notes)

            per_note: dict[str, list] = {}
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
                op.progress(f"nota opravena", key=key, fields=len(corrs))

            op.set_output({"applied": applied, "notes_modified": len(per_note)})
            return bank.model_copy(update={"notes": new_notes, "is_modified": True})
"""


# =============================================================================
# main.py — FastAPI endpointy
# =============================================================================

"""
# --- Přidat na začátek main.py ---
from logger import get_logger, app_log, OperationLogger

# Použití v endpointech:
@app.post("/fit")
async def run_fit(bank_path: str, request: FitRequest):
    log = get_logger("main", method="run_fit")
    log.info(f"→  bank_path={bank_path!r}  anchor={request.anchor_db_name!r}")

    try:
        with OperationLogger(log, "fit_pipeline",
                             input={"bank": bank_path}) as op:
            bank     = loader.load(bank_path)
            op.progress("banka načtena", notes=bank.note_count())

            anchor_db = None
            if request.anchor_db_name:
                anchor_db = mgr.load(request.anchor_db_name)
                op.progress("anchor DB načtena",
                            entries=len(anchor_db.entries))

            fit = fitter.fit_all(bank, anchor_db)
            op.set_output({
                "outliers": sum(1 for s in fit.outlier_scores.values()
                                if s > 0.5)
            })
        return fit

    except Exception as e:
        log.error(f"✗  {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# WebSocket — logování per zpráva:
async def _handle_ws_action(msg, session_state):
    log = get_logger("main", method="ws_preview")
    log.debug(f"→  action={msg.action!r}  payload={msg.payload}")
    # ... dispatch ...
"""


# =============================================================================
# Příklad výstupu konzole
# =============================================================================

"""
Příklad konzolového výstupu pro workflow "načti banku → fit → export":

09:15:32.000 | INFO     | piano_editor.bank_loader.BankLoader.load
              →  path="soundbanks/ks-grand-f44.json"
09:15:32.312 | INFO     | piano_editor.bank_loader.BankLoader.load
              ←  BankState(source_path="ks-grand-f44.json")  [312ms]

09:15:32.315 | INFO     | piano_editor.anchor_manager.AnchorManager.load
              →  name_or_path="ks-grand-v1"
09:15:32.318 | INFO     | piano_editor.anchor_manager.AnchorManager.load
              ←  AnchorDatabase(name="ks-grand-v1")  [3ms]

09:15:32.320 | INFO     | piano_editor.relation_fitter.RelationFitter.fit_all
              [fit_all] start  notes=704, plugins=['b_curve', 'damping_law', 'spectral_shape', 'velocity_model'], anchor_db="ks-grand-v1"
09:15:32.325 | DEBUG    | piano_editor.relation_fitter.RelationFitter.fit_all
              [fit_all] ·  váhy připraveny  non_unit=14
09:15:32.401 | INFO     | piano_editor.relation_fitter.BCurveFitter.fit
              [fit] start  notes=704, break_midi=None
09:15:32.408 | DEBUG    | piano_editor.relation_fitter.BCurveFitter.fit
              [fit] ·  body sesbírány  n=88
09:15:32.409 | DEBUG    | piano_editor.relation_fitter.BCurveFitter.fit
              [fit] ·  zlom detekován  break_midi=48
09:15:32.412 | WARNING  | piano_editor.relation_fitter.BCurveFitter.fit
              [fit] ⚠  B outlierů nad prahem  count=3, threshold=2.5, midis=[42, 55, 61]
09:15:32.412 | INFO     | piano_editor.relation_fitter.BCurveFitter.fit
              [fit] ✓  alpha_bass=2.41, alpha_treble=3.68, outliers=3  [11ms]
09:15:32.580 | INFO     | piano_editor.relation_fitter.RelationFitter.fit_all
              [fit_all] ✓  plugins_ok=4, outliers_above_05=7  [260ms]  (1 varování)

09:15:32.582 | INFO     | piano_editor.correction_engine.CorrectionEngine.propose
              [propose] start  notes=704, outlier_threshold=2.5
09:15:32.590 | DEBUG    | piano_editor.correction_engine.CorrectionEngine.propose
              [propose] ·  outlierů nalezeno  count=7
09:15:32.591 | INFO     | piano_editor.correction_engine.CorrectionEngine.propose
              [propose] ✓  corrections=23, affected_notes=7  [9ms]
"""
