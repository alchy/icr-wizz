"""
main.py — FastAPI aplikace Piano Soundbank Editoru

Changelog:
  2025-04-14 v0.6  — IMPLEMENTOVÁNO: vsechny endpointy
  2025-04-14 v0.7  — nove: /bank/state, /bank/note, /fit/details
  2025-04-14 v0.8  — WsResponse outlier_scores_per_vel, WS handlery
  2025-04-14 v0.9  — anchor CRUD: /entry, /coverage, /suggest
"""
from __future__ import annotations
import asyncio, json, os, math, yaml
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from anchor_manager import AnchorManager, AnchorError, AnchorNotFoundError
from bank_exporter import BankExporter
from bank_loader import BankLoader, BankLoadError
from correction_engine import CorrectionEngine
from logger import app_log, get_logger
from midi_bridge import MidiBridge, MidiConnectionError
from models import (
    AnchorDatabase, BankState, CorrectionSet,
    ExportRequest, FitRequest, FitResult, LoadBankRequest,
    NoteParams, WsMessage, WsResponse,
)
from relation_fitter import BCurveFitter, RelationFitter, VelocityModelFitter

# ---------------------------------------------------------------------------
# Konfigurace
# ---------------------------------------------------------------------------
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "run-wizz.yaml"
_app_config: dict = {}
if _CONFIG_PATH.exists():
    with open(_CONFIG_PATH) as f:
        _app_config = yaml.safe_load(f) or {}
    app_log.info(f"Konfigurace načtena z {_CONFIG_PATH}")

ANCHOR_DIR = os.environ.get("ANCHOR_DIR", "anchor-databases")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "exported")

_API_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="api-worker")

# ---------------------------------------------------------------------------
# MIDI output pro preview (note on/off → ICR přes IAC)
# ---------------------------------------------------------------------------
import rtmidi as _rtmidi

_midi_out: _rtmidi.MidiOut | None = None

def _init_midi_out():
    global _midi_out
    port_name = _app_config.get("midi_port", "IAC Driver Bus 1")
    m = _rtmidi.MidiOut()
    ports = m.get_ports()
    for i, p in enumerate(ports):
        if port_name in p:
            m.open_port(i)
            _midi_out = m
            app_log.info(f"MIDI OUT otevřen: {p} (port {i})")
            return
    app_log.warning(f"MIDI port '{port_name}' nenalezen. Dostupné: {ports}")

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(ANCHOR_DIR).mkdir(parents=True, exist_ok=True)
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    _init_midi_out()
    app_log.info(f"Backend spusten  anchor={ANCHOR_DIR}  output={OUTPUT_DIR}")
    yield
    if _midi_out:
        _midi_out.close_port()
    _API_EXECUTOR.shutdown(wait=False)
    app_log.info("Backend ukoncen")

app = FastAPI(title="Piano Soundbank Editor API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"], allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Singleton sluzby
# ---------------------------------------------------------------------------
loader   = BankLoader()
fitter   = RelationFitter()
engine   = CorrectionEngine()
exporter = BankExporter()
mgr      = AnchorManager(ANCHOR_DIR)
bridge   = MidiBridge()

# ---------------------------------------------------------------------------
# Request / Response modely
# ---------------------------------------------------------------------------
class BankListItem(BaseModel):
    path: str; filename: str; instrument_name: str
    midi_range: str; sr: int; note_count: int; file_size_kb: float

class BankStateResponse(BaseModel):
    source_path: str; instrument_name: str
    midi_range_from: int; midi_range_to: int
    sr: int; k_max: int; note_count: int
    note_keys: list[str]
    stereo_config: Optional[dict] = None

class LoadResponse(BaseModel):
    loaded: list[str]; errors: list[str]
    states: dict[str, BankStateResponse]

class FitSummary(BaseModel):
    outlier_scores: dict[str, float]
    outlier_scores_per_vel: dict[str, float]
    b_curve: Optional[dict]
    fit_timestamp: str; anchor_db_name: Optional[str]

class FitDetailsResponse(BaseModel):
    damping: dict[int, dict]; gamma_k: dict[int, list[float]]
    attack_alpha: dict[int, float]; attack_tref: dict[int, float]
    shape_residuals: dict[str, float]

class AnchorListItem(BaseModel):
    name: str; path: str; description: Optional[str]
    instrument_hint: Optional[str]; modified: str; entry_count: int

class AnchorSaveRequest(BaseModel):
    db: dict; overwrite: bool = True

class AnchorEntryRequest(BaseModel):
    midi: int; vel: int; score: float; note: Optional[str] = None

class ApplyRequest(BaseModel):
    bank_path: str; correction_set: dict
    selected_fields: Optional[list[str]] = None

class ApplyResponse(BaseModel):
    output_path: str; corrections_applied: int; notes_affected: int

class MidiConnectRequest(BaseModel):
    port_name: str

class MidiStatusResponse(BaseModel):
    connected: bool; port_name: Optional[str]

class MidiPatchRequest(BaseModel):
    bank_path: str
    midi_range: Optional[tuple[int, int]] = None
    vel_range:  Optional[tuple[int, int]] = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _run_blocking(fn, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_API_EXECUTOR, lambda: fn(*args, **kwargs))

def _err(log, status: int, detail: str, exc: Exception = None) -> HTTPException:
    log.error(f"X  {detail}" + (f"  error={exc}" if exc else ""))
    return HTTPException(status_code=status, detail=detail)

def _bank_to_resp(bank: BankState) -> BankStateResponse:
    return BankStateResponse(
        source_path=bank.source_path,
        instrument_name=bank.metadata.instrument_name,
        midi_range_from=bank.metadata.midi_range_from,
        midi_range_to=bank.metadata.midi_range_to,
        sr=bank.metadata.sr, k_max=bank.metadata.k_max,
        note_count=bank.note_count(),
        note_keys=sorted(bank.notes.keys()),
        stereo_config=bank.stereo_config.model_dump() if bank.stereo_config else None,
    )

def _fit_to_summary(fit: FitResult, bank: BankState = None) -> FitSummary:
    # fit.outlier_scores má klíče "m060" (prefix per MIDI nota)
    per_pfx = dict(fit.outlier_scores)

    # Rozbal prefix skóre na per-vel klíče pro heatmap
    per_vel: dict[str, float] = {}
    if bank is not None:
        for note_key in bank.notes:
            pfx = note_key.split("_")[0]
            per_vel[note_key] = per_pfx.get(pfx, 0.0)
    else:
        # Fallback: expanduj na 8 velocity vrstev
        for pfx, score in per_pfx.items():
            for vel in range(8):
                per_vel[f"{pfx}_vel{vel}"] = score

    return FitSummary(
        outlier_scores=per_pfx,
        outlier_scores_per_vel=per_vel,
        b_curve=fit.b_curve.model_dump() if fit.b_curve else None,
        fit_timestamp=fit.fit_timestamp,
        anchor_db_name=fit.anchor_db_name,
    )

def _b_curve_points(fit: FitResult) -> list[list[float]]:
    if fit.b_curve is None:
        return []
    bf = BCurveFitter()
    return [[round(math.log10(440.0 * math.pow(2.0, (m-69)/12.0)), 4),
             round(math.log10(bf.predict_B(fit.b_curve, m)), 4)]
            for m in range(21, 109, 2)]

def _mean_fit_quality(bank: BankState) -> float:
    quals = [p.fit_quality for n in bank.notes.values()
             if n.vel in (4,5,6,7) for p in n.partials]
    return round(sum(quals)/len(quals), 3) if quals else 1.0

# ---------------------------------------------------------------------------
# Config endpoint
# ---------------------------------------------------------------------------
def _save_config():
    with open(_CONFIG_PATH, 'w') as f:
        yaml.dump(_app_config, f, default_flow_style=False, allow_unicode=True)

@app.get("/config")
async def get_config():
    return _app_config

@app.patch("/config")
async def update_config(updates: dict):
    _app_config.update(updates)
    await _run_blocking(_save_config)
    return _app_config

# ---------------------------------------------------------------------------
# Bank endpoints
# ---------------------------------------------------------------------------
@app.get("/bank/list", response_model=list[BankListItem])
async def list_banks(directory: str):
    log = get_logger("main", method="list_banks")
    try:
        infos = await _run_blocking(loader.list_banks, directory)
        return [BankListItem(**{**info.to_dict(),
            "midi_range": info.to_dict().get("midi_range", "?-?"),
            "file_size_kb": info.to_dict().get("file_size_kb", 0.0)})
            for info in infos]
    except Exception as e:
        raise _err(log, 500, str(e), e)

@app.post("/bank/load", response_model=LoadResponse)
async def load_banks(request: LoadBankRequest):
    log = get_logger("main", method="load_banks")
    log.info(f"paths={[Path(p).name for p in request.paths]}")
    try:
        banks, errors = await _run_blocking(loader.load_multiple, request.paths)
        # Uložit poslední načtenou banku do configu
        loaded_paths = list(banks.keys())
        if loaded_paths:
            _app_config["last_bank_path"] = loaded_paths[-1]
            await _run_blocking(_save_config)
        return LoadResponse(
            loaded=loaded_paths,
            errors=[str(e) for e in errors],
            states={p: _bank_to_resp(b) for p, b in banks.items()},
        )
    except Exception as e:
        raise _err(log, 500, str(e), e)

@app.get("/bank/state", response_model=BankStateResponse)
async def get_bank_state(bank_path: str):
    log = get_logger("main", method="get_bank_state")
    try:
        return _bank_to_resp(await _run_blocking(loader.load, bank_path))
    except BankLoadError as e:
        raise _err(log, 404, str(e), e)
    except Exception as e:
        raise _err(log, 500, str(e), e)

@app.get("/bank/note/{note_key}", response_model=NoteParams)
async def get_note(bank_path: str, note_key: str):
    log = get_logger("main", method="get_note")
    try:
        bank = await _run_blocking(loader.load, bank_path)
        note = bank.notes.get(note_key)
        if note is None:
            raise _err(log, 404, f"Nota {note_key!r} nenalezena")
        return note
    except HTTPException: raise
    except BankLoadError as e: raise _err(log, 404, str(e), e)
    except Exception as e:    raise _err(log, 500, str(e), e)

# ---------------------------------------------------------------------------
# Fit endpoints
# ---------------------------------------------------------------------------
@app.post("/fit", response_model=FitSummary)
async def run_fit(bank_path: str, request: FitRequest):
    log = get_logger("main", method="run_fit")
    log.info(f"bank={Path(bank_path).name}  anchor={request.anchor_db_name!r}")
    try:
        bank = await _run_blocking(loader.load, bank_path)
        anchor_db = None
        if request.anchor_db_name:
            try: anchor_db = await _run_blocking(mgr.load, request.anchor_db_name)
            except AnchorNotFoundError: pass
        fit = await _run_blocking(fitter.fit_all, bank, anchor_db)
        return _fit_to_summary(fit, bank)
    except BankLoadError as e: raise _err(log, 404, str(e), e)
    except Exception as e:     raise _err(log, 500, str(e), e)

@app.post("/fit/details", response_model=FitDetailsResponse)
async def get_fit_details(bank_path: str, request: FitRequest):
    log = get_logger("main", method="fit_details")
    try:
        bank = await _run_blocking(loader.load, bank_path)
        anchor_db = None
        if request.anchor_db_name:
            try: anchor_db = await _run_blocking(mgr.load, request.anchor_db_name)
            except AnchorNotFoundError: pass
        fit = await _run_blocking(fitter.fit_all, bank, anchor_db)
        return FitDetailsResponse(
            damping={m: p.model_dump() for m, p in fit.damping.items()},
            gamma_k=fit.gamma_k, attack_alpha=fit.attack_alpha,
            attack_tref=fit.attack_tref, shape_residuals=fit.shape_residuals,
        )
    except BankLoadError as e: raise _err(log, 404, str(e), e)
    except Exception as e:     raise _err(log, 500, str(e), e)

# ---------------------------------------------------------------------------
# Correction endpoints
# ---------------------------------------------------------------------------
@app.post("/corrections/propose", response_model=CorrectionSet)
async def propose_corrections(bank_path: str, fit_result: dict):
    log = get_logger("main", method="propose_corrections")
    try:
        bank = await _run_blocking(loader.load, bank_path)
        fit  = FitResult(**fit_result)
        cs   = await _run_blocking(engine.propose, bank, fit)
        log.info(f"corrections={len(cs.corrections)}")
        return cs
    except BankLoadError as e: raise _err(log, 404, str(e), e)
    except Exception as e:     raise _err(log, 500, str(e), e)

@app.post("/corrections/apply", response_model=ApplyResponse)
async def apply_corrections(request: ApplyRequest):
    log = get_logger("main", method="apply_corrections")
    log.info(f"bank={Path(request.bank_path).name}")
    try:
        bank = await _run_blocking(loader.load, request.bank_path)
        cs   = CorrectionSet(**request.correction_set)
        if request.selected_fields is not None:
            sel = set(request.selected_fields)
            cs  = cs.model_copy(update={"corrections": [c for c in cs.corrections if c.field in sel]})
        corrected = await _run_blocking(engine.apply, bank, cs)
        out = Path(OUTPUT_DIR) / f"{Path(request.bank_path).stem}-corrected.json"
        await _run_blocking(exporter.export, corrected, str(out), add_metadata=True, correction_set=cs)
        s = cs.summary()
        return ApplyResponse(output_path=str(out.resolve()),
                             corrections_applied=s["total_corrections"],
                             notes_affected=s["affected_notes"])
    except BankLoadError as e: raise _err(log, 404, str(e), e)
    except Exception as e:     raise _err(log, 500, str(e), e)

# ---------------------------------------------------------------------------
# Export endpoints
# ---------------------------------------------------------------------------
@app.post("/export")
async def export_bank(request: ExportRequest):
    log = get_logger("main", method="export_bank")
    try:
        bank = await _run_blocking(loader.load, request.source_path)
        out  = await _run_blocking(
            exporter.export, bank, request.output_path,
            request.diff_only, None, request.add_metadata,
        )
        return {"path": str(out), "size_kb": round(out.stat().st_size / 1024, 1)}
    except BankLoadError as e: raise _err(log, 404, str(e), e)
    except Exception as e:     raise _err(log, 500, str(e), e)

@app.post("/export/diff-report")
async def export_diff_report(bank_path: str, correction_set: dict):
    log = get_logger("main", method="export_diff_report")
    try:
        cs   = CorrectionSet(**correction_set)
        out  = Path(OUTPUT_DIR) / f"{Path(bank_path).stem}-corrections.csv"
        await _run_blocking(exporter.export_diff_report, cs, str(out))
        return FileResponse(path=str(out), filename=out.name, media_type="text/csv")
    except Exception as e:
        raise _err(log, 500, str(e), e)

# ---------------------------------------------------------------------------
# Anchor endpoints
# ---------------------------------------------------------------------------
@app.get("/anchors/list", response_model=list[AnchorListItem])
async def list_anchor_databases():
    log = get_logger("main", method="list_anchors")
    try:
        return [AnchorListItem(**{k: d.get(k) for k in AnchorListItem.model_fields})
                for d in await _run_blocking(mgr.list_databases)]
    except Exception as e:
        raise _err(log, 500, str(e), e)

@app.post("/anchors/save")
async def save_anchor_database(request: AnchorSaveRequest):
    log = get_logger("main", method="save_anchor")
    try:
        db   = AnchorDatabase(**request.db)
        path = await _run_blocking(mgr.save, db, request.overwrite)
        return {"path": str(path), "name": db.name}
    except Exception as e:
        raise _err(log, 500, str(e), e)

@app.get("/anchors/{name}")
async def load_anchor_database(name: str):
    log = get_logger("main", method="load_anchor")
    try:
        return await _run_blocking(mgr.load, name)
    except AnchorNotFoundError:
        raise _err(log, 404, f"Anchor DB {name!r} nenalezena")
    except AnchorError as e:
        raise _err(log, 422, str(e), e)
    except Exception as e:
        raise _err(log, 500, str(e), e)

@app.delete("/anchors/{name}")
async def delete_anchor_database(name: str):
    log = get_logger("main", method="delete_anchor")
    try:
        deleted = await _run_blocking(mgr.delete, name)
        return {"deleted": deleted, "name": name}
    except Exception as e:
        raise _err(log, 500, str(e), e)

@app.post("/anchors/{name}/entry")
async def add_anchor_entry(name: str, request: AnchorEntryRequest):
    log = get_logger("main", method="add_anchor_entry")
    try:
        db   = await _run_blocking(mgr.load, name)
        db   = mgr.add_entry(db, request.midi, request.vel, request.score, request.note)
        path = await _run_blocking(mgr.save, db, True)
        return {"saved": True, "path": str(path), "entry_count": len(db.entries)}
    except AnchorNotFoundError:
        raise _err(log, 404, f"Anchor DB {name!r} nenalezena")
    except Exception as e:
        raise _err(log, 500, str(e), e)

@app.delete("/anchors/{name}/entry")
async def remove_anchor_entry(name: str, midi: int, vel: int):
    log = get_logger("main", method="remove_anchor_entry")
    try:
        db   = await _run_blocking(mgr.load, name)
        db   = mgr.remove_entry(db, midi, vel)
        path = await _run_blocking(mgr.save, db, True)
        return {"saved": True, "entry_count": len(db.entries)}
    except AnchorNotFoundError:
        raise _err(log, 404, f"Anchor DB {name!r} nenalezena")
    except Exception as e:
        raise _err(log, 500, str(e), e)

@app.get("/anchors/{name}/coverage")
async def anchor_coverage(name: str, bank_path: Optional[str] = None):
    log = get_logger("main", method="anchor_coverage")
    try:
        db   = await _run_blocking(mgr.load, name)
        bank = None
        if bank_path:
            try: bank = await _run_blocking(loader.load, bank_path)
            except Exception: pass
        return mgr.coverage_report(db, bank)
    except AnchorNotFoundError:
        raise _err(log, 404, f"Anchor DB {name!r} nenalezena")
    except Exception as e:
        raise _err(log, 500, str(e), e)

@app.get("/anchors/{name}/suggest")
async def suggest_anchors(name: str, bank_path: str, n: int = 15):
    log = get_logger("main", method="suggest_anchors")
    try:
        bank = await _run_blocking(loader.load, bank_path)
        db   = None
        try: db = await _run_blocking(mgr.load, name)
        except AnchorNotFoundError: pass
        suggestions = await _run_blocking(mgr.suggest_anchors, bank, db, n)
        return {"suggestions": suggestions}
    except BankLoadError as e: raise _err(log, 404, str(e), e)
    except Exception as e:     raise _err(log, 500, str(e), e)

# ---------------------------------------------------------------------------
# MIDI endpoints
# ---------------------------------------------------------------------------
@app.get("/midi/status", response_model=MidiStatusResponse)
async def midi_status():
    return MidiStatusResponse(connected=bridge.is_connected, port_name=bridge.port_name)

@app.get("/midi/ports")
async def list_midi_ports():
    log = get_logger("main", method="list_midi_ports")
    try:
        return {"ports": await _run_blocking(bridge.list_ports)}
    except Exception as e:
        raise _err(log, 500, str(e), e)

@app.post("/midi/connect")
async def connect_midi(request: MidiConnectRequest):
    log = get_logger("main", method="connect_midi")
    try:
        await _run_blocking(bridge.connect, request.port_name)
        return {"connected": True, "port": request.port_name}
    except MidiConnectionError as e: raise _err(log, 422, str(e), e)
    except Exception as e:           raise _err(log, 500, str(e), e)

@app.post("/midi/disconnect")
async def disconnect_midi():
    log = get_logger("main", method="disconnect_midi")
    try:
        await _run_blocking(bridge.disconnect)
        return {"connected": False}
    except Exception as e:
        raise _err(log, 500, str(e), e)

class MidiPlayRequest(BaseModel):
    midi: int          # MIDI nota 21-108
    velocity: int = 100  # 1-127
    duration_s: float = 3.0

@app.post("/midi/play")
async def midi_play(request: MidiPlayRequest):
    """Pošle note_on, počká duration_s, pošle note_off. Async — neblokuje."""
    if not _midi_out:
        raise HTTPException(503, "MIDI OUT není připojen")
    midi = max(0, min(127, request.midi))
    vel = max(1, min(127, request.velocity))

    # Note On (status 0x90, channel 0)
    _midi_out.send_message([0x90, midi, vel])

    async def _send_note_off():
        await asyncio.sleep(request.duration_s)
        if _midi_out:
            _midi_out.send_message([0x80, midi, 0])

    asyncio.ensure_future(_send_note_off())
    return {"status": "playing", "midi": midi, "velocity": vel, "duration_s": request.duration_s}

@app.post("/midi/patch")
async def patch_synth(request: MidiPatchRequest):
    log = get_logger("main", method="patch_synth")
    if not bridge.is_connected:
        raise _err(log, 409, "MIDI neni pripojeno")
    try:
        bank = await _run_blocking(loader.load, request.bank_path)
        result = await _run_blocking(bridge.patch_bank, bank, request.midi_range, request.vel_range)
        return result.summary()
    except MidiConnectionError as e: raise _err(log, 422, str(e), e)
    except BankLoadError as e:       raise _err(log, 404, str(e), e)
    except Exception as e:           raise _err(log, 500, str(e), e)

# ---------------------------------------------------------------------------
# WebSocket — live preview
# ---------------------------------------------------------------------------
@app.websocket("/ws/preview")
async def preview_websocket(websocket: WebSocket):
    """
    Live preview. Session per pripojeni:
      bank_path, anchor_db, last_fit
    Akce: init | update_anchor | move_spline_node | drag_gamma_k
    """
    log = get_logger("main", method="ws_preview")
    await websocket.accept()
    log.info("WS pripojeno")
    session: dict = {"bank_path": None, "anchor_db": None, "last_fit": None}

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = WsMessage(**json.loads(raw))
            except Exception as e:
                await websocket.send_text(WsResponse(error=f"Neplatna zprava: {e}").model_dump_json())
                continue
            try:
                resp = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        _API_EXECUTOR, lambda m=msg: _ws_handle(m, session)
                    ),
                    timeout=0.100,
                )
            except asyncio.TimeoutError:
                resp = WsResponse(error="Timeout > 100ms")
            await websocket.send_text(resp.model_dump_json())
    except WebSocketDisconnect:
        log.info("WS odpojeno")
    except Exception as e:
        log.error(f"WS chyba  error={e}")


def _ws_handle(msg: WsMessage, session: dict) -> WsResponse:
    action  = msg.action
    payload = msg.payload
    log     = get_logger("main", method="_ws_handle")

    # --- init ---
    if action == "init":
        bp = payload.get("bank_path")
        if not bp:
            return WsResponse(error="init: chybi bank_path")
        try:
            bank = loader.load(bp)
        except Exception as e:
            return WsResponse(error=f"init: {e}")
        session["bank_path"] = bp
        db_name = payload.get("anchor_db_name")
        if db_name:
            try: session["anchor_db"] = mgr.load(db_name)
            except Exception: session["anchor_db"] = None
        qf = RelationFitter(plugins=[BCurveFitter(), VelocityModelFitter()], sigma_threshold=2.5)
        try:
            fit = qf.fit_all(bank, session["anchor_db"])
            session["last_fit"] = fit
            s = _fit_to_summary(fit, bank)
            return WsResponse(
                outlier_scores=s.outlier_scores,
                outlier_scores_per_vel=s.outlier_scores_per_vel,
                spline_points=_b_curve_points(fit),
                fit_quality=_mean_fit_quality(bank),
            )
        except Exception as e:
            return WsResponse(error=f"init fit: {e}")

    # Pro ostatni akce potrebujeme banku
    bp = session.get("bank_path")
    if not bp:
        return WsResponse(error="session neni inicializovana — posli 'init'")
    try:
        bank = loader.load(bp)
    except Exception as e:
        return WsResponse(error=f"nelze nacist banku: {e}")

    # --- update_anchor ---
    if action == "update_anchor":
        # Reload anchor DB z disku (REST API ji právě uložil)
        db = session.get("anchor_db")
        if db is not None:
            try:    db = mgr.load(db.name)
            except: pass
        else:
            db_name = payload.get("anchor_db_name")
            if db_name:
                try:    db = mgr.load(db_name)
                except: db = None
        session["anchor_db"] = db
        qf = RelationFitter(plugins=[BCurveFitter(), VelocityModelFitter()], sigma_threshold=2.5)
        try:
            fit = qf.fit_all(bank, db)
            session["last_fit"] = fit
            s = _fit_to_summary(fit, bank)
            return WsResponse(
                outlier_scores=s.outlier_scores,
                outlier_scores_per_vel=s.outlier_scores_per_vel,
                spline_points=_b_curve_points(fit),
                fit_quality=_mean_fit_quality(bank),
            )
        except Exception as e:
            return WsResponse(error=f"refit: {e}")

    # --- move_spline_node ---
    if action == "move_spline_node":
        bm = payload.get("break_midi")
        if bm is None:
            return WsResponse(error="chybi break_midi")
        db  = session.get("anchor_db")
        bf  = BCurveFitter(break_midi=int(bm))
        w   = {k: 1.0 for k in bank.notes}
        if db:
            from anchor_manager import AnchorManager as _AM
            _m = _AM.__new__(_AM)
            w = _m.to_weights(db, bank)
        try:
            res = bf.fit(bank, w)
            bc  = res.get("b_curve")
            pts = []
            if bc:
                for m in range(21, 109, 2):
                    f0 = 440.0 * math.pow(2.0, (m-69)/12.0)
                    pts.append([round(math.log10(f0), 4), round(math.log10(bf.predict_B(bc, m)), 4)])
            last = session.get("last_fit")
            return WsResponse(
                outlier_scores=last.outlier_scores if last else {},
                outlier_scores_per_vel={},
                spline_points=pts, fit_quality=1.0,
            )
        except Exception as e:
            return WsResponse(error=f"b_curve refit: {e}")

    # --- drag_gamma_k ---
    if action == "drag_gamma_k":
        midi      = payload.get("midi")
        k         = payload.get("k")
        new_gamma = payload.get("gamma")
        if None in (midi, k, new_gamma):
            return WsResponse(error="chybi midi/k/gamma")
        last = session.get("last_fit")
        if last is None:
            return WsResponse(error="session nema last_fit")
        vm   = VelocityModelFitter()
        pts  = [[vel, round(vm.predict_A0(float(new_gamma), 1.0, vel, vel_ref=4), 4)]
                for vel in range(8)]
        return WsResponse(
            outlier_scores=last.outlier_scores,
            outlier_scores_per_vel={},
            spline_points=pts, fit_quality=1.0,
        )

    log.warning(f"neznama akce={action!r}")
    return WsResponse(error=f"Neznama akce: '{action}'")
