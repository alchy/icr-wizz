#!/usr/bin/env python3
"""
run-wizz.py — spouští backend, frontend a ICR engine v jednom procesu.

Použití:
    python run-wizz.py          # spustí vše
    kill -USR1 <pid>            # restart backend
    kill -USR2 <pid>            # restart frontend
    kill -HUP  <pid>            # restart vše (backend + frontend + ICR)
    kill -TERM <pid>            # ukončí vše

PID wrapperu se zapíše do run-wizz.pid.
Veškerý stdout/stderr všech procesů jde do run-wizz.log.
"""

import os
import sys
import signal
import subprocess
import time
import yaml
from pathlib import Path

ROOT     = Path(__file__).resolve().parent
PID_FILE = ROOT / "run-wizz.pid"
LOG_FILE = ROOT / "run-wizz.log"
CFG_FILE = ROOT / "run-wizz.yaml"
VENV_PY  = ROOT / ".venv" / "bin" / "python"

backend_proc: subprocess.Popen | None = None
frontend_proc: subprocess.Popen | None = None
icr_proc: subprocess.Popen | None = None
log_fh = None
shutting_down = False
cfg: dict = {}


def load_config():
    global cfg
    if CFG_FILE.exists():
        with open(CFG_FILE) as f:
            cfg = yaml.safe_load(f) or {}


def log(msg: str):
    line = f"[run-wizz {time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if log_fh:
        log_fh.write(line + "\n")
        log_fh.flush()


def start_backend():
    global backend_proc
    log("starting backend …")
    backend_proc = subprocess.Popen(
        [
            str(VENV_PY), "-m", "uvicorn",
            "main:app",
            "--workers", "1",
            "--loop", "uvloop",
            "--port", "8000",
            "--reload",
        ],
        cwd=ROOT / "backend",
        stdout=log_fh,
        stderr=log_fh,
    )
    log(f"backend pid={backend_proc.pid}")


def start_frontend():
    global frontend_proc
    log("starting frontend …")
    frontend_proc = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=ROOT / "frontend",
        stdout=log_fh,
        stderr=log_fh,
    )
    log(f"frontend pid={frontend_proc.pid}")


def start_icr():
    global icr_proc
    load_config()

    icr_bin = cfg.get("icr_binary", "")
    if not icr_bin or not Path(icr_bin).exists():
        log(f"ICR binary not found: {icr_bin!r}, skipping")
        return

    core = cfg.get("icr_core", "AdditiveSynthesisPianoCore")
    params = cfg.get("last_bank_path", "")
    midi_port_name = cfg.get("midi_port", "IAC Driver Bus 1")

    # Najdi index MIDI portu podle názvu
    port_index = _find_midi_port_index(midi_port_name)

    cmd = [icr_bin, "--core", core, "--port", str(port_index)]
    if params and Path(params).exists():
        cmd += ["--params", params]

    log(f"starting ICR: {' '.join(cmd)}")
    icr_proc = subprocess.Popen(
        cmd,
        cwd=Path(icr_bin).parent,
        stdout=log_fh,
        stderr=log_fh,
    )
    log(f"ICR pid={icr_proc.pid}")


def _find_midi_port_index(name: str) -> int:
    """Najde index MIDI IN portu podle názvu (ICR poslouchá na IN)."""
    try:
        import rtmidi
        m = rtmidi.MidiIn()
        ports = m.get_ports()
        for i, p in enumerate(ports):
            if name in p:
                log(f"MIDI IN port {i}: {p}")
                return i
        log(f"MIDI port '{name}' not found, using 0. Available: {ports}")
    except Exception as e:
        log(f"rtmidi error: {e}, using port 0")
    return 0


def stop(proc: subprocess.Popen | None, name: str):
    if proc is None or proc.poll() is not None:
        return
    log(f"stopping {name} pid={proc.pid} …")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        log(f"killing {name} pid={proc.pid}")
        proc.kill()
        proc.wait()
    log(f"{name} stopped")


def restart_backend(*_):
    global backend_proc
    if shutting_down:
        return
    stop(backend_proc, "backend")
    start_backend()


def restart_frontend(*_):
    global frontend_proc
    if shutting_down:
        return
    stop(frontend_proc, "frontend")
    start_frontend()


def restart_icr(*_):
    global icr_proc
    if shutting_down:
        return
    stop(icr_proc, "ICR")
    start_icr()


def restart_all(*_):
    restart_backend()
    restart_frontend()
    restart_icr()


def shutdown(*_):
    global shutting_down
    if shutting_down:
        return
    shutting_down = True
    log("shutting down …")
    stop(icr_proc, "ICR")
    stop(backend_proc, "backend")
    stop(frontend_proc, "frontend")
    PID_FILE.unlink(missing_ok=True)
    log("done")
    sys.exit(0)


def main():
    global log_fh

    log_fh = open(LOG_FILE, "a", buffering=1)
    load_config()

    # Zapsat PID
    PID_FILE.write_text(str(os.getpid()))
    log(f"wrapper pid={os.getpid()}, log → {LOG_FILE}")

    # Signály
    signal.signal(signal.SIGUSR1, restart_backend)
    signal.signal(signal.SIGUSR2, restart_frontend)
    signal.signal(signal.SIGHUP,  restart_all)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT,  shutdown)

    start_icr()
    start_backend()
    start_frontend()

    # Čekat — pokud proces spadne, restartovat
    while not shutting_down:
        try:
            time.sleep(2)
        except (KeyboardInterrupt, SystemExit):
            shutdown()
            break

        if backend_proc and backend_proc.poll() is not None:
            log(f"backend exited ({backend_proc.returncode}), restarting …")
            start_backend()

        if frontend_proc and frontend_proc.poll() is not None:
            log(f"frontend exited ({frontend_proc.returncode}), restarting …")
            start_frontend()

        if icr_proc and icr_proc.poll() is not None:
            log(f"ICR exited ({icr_proc.returncode}), restarting …")
            start_icr()


if __name__ == "__main__":
    main()
