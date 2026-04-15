"""
extract_runner.py — spouští a sleduje extrakční subprocesy

Spouští icr-engine/run-extract-additive.py jako child process.
Podporuje více paralelních extrakcí (multi-job).
Čte stdout v pozadí, parsuje progress milníky, poskytuje status API.

Pipeline kroky (z pipeline_v2.py):
  Step 1/7: Extract partials
  Step 2/7: Export RAW bank
  Step 3/7: Reference spectral correction
  Step 4/7: Outlier filter
  Step 5/7: Spectral EQ fitting
  Step 6/7: Export RELAXED bank
  Step 7/7: Pan correction calibration
  (+ IR extraction)
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from logger import get_logger

_log = get_logger(__name__, cls="ExtractRunner")

# Progress patterns z pipeline_v2.py stdout
_RE_STEP = re.compile(r"=+\s*Step\s+(\d+)/(\d+):\s*(.+?)\s*=+")
_RE_EXPORTED = re.compile(r"(?:Exported|Soundbank)\s*(?:\(.*?\))?\s*->\s*(.+)")
_RE_COMPLETE = re.compile(r"Pipeline complete in ([\d.]+)s")
_RE_ERROR = re.compile(r"(?:Error|Exception|Traceback|FAILED)", re.IGNORECASE)

_MAX_LOG_LINES = 200
_DEFAULT_WORKERS = max((os.cpu_count() or 4) - 1, 1)


class _ExtractJob:
    """Stav jedné extrakční úlohy."""
    __slots__ = (
        "job_id", "bank_dir", "bank_name", "out_dir",
        "proc", "reader_thread",
        "status", "step", "step_total", "step_label",
        "log_lines", "output_paths", "start_time", "elapsed",
        "error_msg", "lock",
    )

    def __init__(self, job_id: str, bank_dir: str, bank_name: str, out_dir: str):
        self.job_id = job_id
        self.bank_dir = bank_dir
        self.bank_name = bank_name
        self.out_dir = out_dir
        self.proc: Optional[subprocess.Popen] = None
        self.reader_thread: Optional[threading.Thread] = None
        self.status = "pending"  # pending | running | done | error
        self.step = 0
        self.step_total = 7
        self.step_label = "Čekám…"
        self.log_lines: list[str] = []
        self.output_paths: list[str] = []
        self.start_time: Optional[float] = None
        self.elapsed = 0.0
        self.error_msg: Optional[str] = None
        self.lock = threading.Lock()

    def to_dict(self) -> dict:
        with self.lock:
            if self.start_time and self.status == "running":
                self.elapsed = time.time() - self.start_time
            return {
                "job_id": self.job_id,
                "bank_dir": self.bank_dir,
                "bank_name": self.bank_name,
                "status": self.status,
                "step": self.step,
                "step_total": self.step_total,
                "step_label": self.step_label,
                "elapsed_s": round(self.elapsed, 1),
                "log_tail": self.log_lines[-30:],
                "output_paths": list(self.output_paths),
                "error": self.error_msg,
            }


class ExtractRunner:
    """Spouští a sleduje extrakční subprocesy. Podporuje více paralelních jobů."""

    def __init__(self, icr_engine_dir: str, venv_python: str, extracts_dir: str):
        self._icr_engine_dir = Path(icr_engine_dir)
        self._venv_python = venv_python
        self._extracts_dir = Path(extracts_dir)
        self._extracts_dir.mkdir(parents=True, exist_ok=True)

        self._jobs: dict[str, _ExtractJob] = {}
        self._lock = threading.Lock()

    def start(
        self,
        bank_dir: str,
        sr_tag: str = "f48",
        workers: Optional[int] = None,
        skip_eq: bool = False,
        skip_ir: bool = False,
        skip_pan_cal: bool = False,
    ) -> dict:
        """Spustí novou extrakci. Vrátí {started, job_id, ...}."""

        script = self._icr_engine_dir / "run-extract-additive.py"
        if not script.exists():
            _log.error(f"Script nenalezen: {script}")
            return {"started": False, "reason": f"script not found: {script}"}

        bank_name = Path(bank_dir).name
        ts = datetime.now().strftime("%m%d%H%M")
        out_path = str(self._extracts_dir / f"{bank_name}-{ts}.json")
        job_id = f"{bank_name}-{ts}-{uuid.uuid4().hex[:6]}"

        job = _ExtractJob(job_id, bank_dir, bank_name, str(self._extracts_dir))

        cmd = [
            self._venv_python,
            str(script),
            "analyze",
            "--bank", str(bank_dir),
            "--out", out_path,
            "--sr-tag", sr_tag,
            "--workers", str(workers or _DEFAULT_WORKERS),
        ]
        if skip_eq:
            cmd.append("--skip-eq")
        if skip_ir:
            cmd.append("--skip-ir")
        if skip_pan_cal:
            cmd.append("--skip-pan-cal")

        _log.info(f"[{job_id}] Spouštím extrakci: {' '.join(cmd)}")

        try:
            job.proc = subprocess.Popen(
                cmd,
                cwd=str(self._icr_engine_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        except Exception as e:
            _log.error(f"[{job_id}] Subprocess start selhal: {e}")
            job.status = "error"
            job.error_msg = str(e)
            with self._lock:
                self._jobs[job_id] = job
            return {"started": False, "job_id": job_id, "reason": str(e)}

        job.status = "running"
        job.step_label = "Spouštím…"
        job.start_time = time.time()

        job.reader_thread = threading.Thread(
            target=self._read_output, args=(job,), daemon=True
        )
        job.reader_thread.start()

        with self._lock:
            self._jobs[job_id] = job

        _log.info(f"[{job_id}] Extrakce spuštěna pid={job.proc.pid}")
        return {"started": True, "job_id": job_id, "pid": job.proc.pid}

    def status(self, job_id: Optional[str] = None) -> dict:
        """Vrátí stav jednoho jobu nebo všech."""
        with self._lock:
            if job_id:
                job = self._jobs.get(job_id)
                if not job:
                    return {"error": f"job {job_id} not found"}
                return job.to_dict()
            return {
                "jobs": [j.to_dict() for j in self._jobs.values()],
            }

    def cancel(self, job_id: str) -> dict:
        """Ukončí běžící extrakci."""
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            return {"cancelled": False, "reason": f"job {job_id} not found"}

        with job.lock:
            if job.status != "running" or job.proc is None:
                return {"cancelled": False, "reason": "not running"}

        _log.info(f"[{job_id}] Ukončuji extrakci…")
        job.proc.terminate()
        try:
            job.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            job.proc.kill()
            job.proc.wait()

        with job.lock:
            job.status = "error"
            job.step_label = "Zrušeno"
            job.error_msg = "Cancelled by user"
            if job.start_time:
                job.elapsed = time.time() - job.start_time

        _log.info(f"[{job_id}] Extrakce zrušena")
        return {"cancelled": True, "job_id": job_id}

    def _read_output(self, job: _ExtractJob):
        """Čte stdout subprocess v pozadí, parsuje progress."""
        assert job.proc and job.proc.stdout

        for line in job.proc.stdout:
            line = line.rstrip("\n")
            with job.lock:
                if len(job.log_lines) >= _MAX_LOG_LINES:
                    job.log_lines = job.log_lines[-_MAX_LOG_LINES // 2:]
                job.log_lines.append(line)

            # Step progress
            m = _RE_STEP.match(line)
            if m:
                with job.lock:
                    job.step = int(m.group(1))
                    job.step_total = int(m.group(2))
                    job.step_label = m.group(3).strip()
                _log.info(f"[{job.job_id}] Step {job.step}/{job.step_total}: {job.step_label}")
                continue

            # Exported paths
            m = _RE_EXPORTED.search(line)
            if m:
                path = m.group(1).strip()
                with job.lock:
                    job.output_paths.append(path)
                _log.info(f"[{job.job_id}] Output: {path}")
                continue

            # Completion
            if _RE_COMPLETE.search(line):
                _log.info(f"[{job.job_id}] {line}")
                continue

            # Errors
            if _RE_ERROR.search(line):
                _log.warning(f"[{job.job_id}] {line[:200]}")

        # Subprocess finished
        returncode = job.proc.wait()
        with job.lock:
            if job.start_time:
                job.elapsed = time.time() - job.start_time
            if returncode == 0:
                job.status = "done"
                job.step_label = "Hotovo"
                _log.info(f"[{job.job_id}] Extrakce dokončena za {job.elapsed:.1f}s, "
                          f"výstupy: {job.output_paths}")
            else:
                job.status = "error"
                job.error_msg = f"Exit code {returncode}"
                job.step_label = f"Chyba (exit {returncode})"
                _log.error(f"[{job.job_id}] Extrakce selhala: exit {returncode}")
