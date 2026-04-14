"""
outlier_detector.py — detekce anomálních not z FitResult residuálů

Paralelizace: není potřeba — operace jsou vektorizované přes numpy,
88 not × 4 zdroje = 352 hodnot. Sekvenční numpy je ~1ms.

Status: scaffold s loggingem — implementace TODO

Changelog:
  2025-04-14 v0.1  — initial scaffold
  2025-04-14 v0.2  — logging integrace, OutlierFlag, OutlierReport třídy
  2025-04-14 v0.3  — SOURCE_WEIGHTS, OperationLogger s progress per zdroj
  2025-04-14 v0.4  — IMPLEMENTOVÁNO: mad_sigma, _get_residuals, _score_from_residuals
"""

from __future__ import annotations

from typing import Optional
import numpy as np

from logger import OperationLogger, get_logger, log_operation
from models import FitResult


class OutlierFlag:
    def __init__(self, source: str, sigma: float, description: str):
        self.source      = source
        self.sigma       = sigma
        self.description = description

    def __repr__(self) -> str:
        return f"OutlierFlag({self.source}, {self.sigma:.2f}σ: {self.description})"


class OutlierReport:
    def __init__(
        self,
        scores:    dict[str, float],
        flags:     dict[str, list[OutlierFlag]],
        threshold: float,
    ):
        self.scores    = scores
        self.flags     = flags
        self.threshold = threshold

    def outlier_notes(self) -> list[str]:
        return [k for k, v in self.scores.items() if v >= self.threshold]

    def summary(self) -> dict:
        total   = len(self.scores)
        flagged = len(self.outlier_notes())
        return {
            "total_notes":  total,
            "flagged":      flagged,
            "flagged_pct":  round(100 * flagged / total, 1) if total else 0,
            "max_score":    round(max(self.scores.values(), default=0), 3),
        }


class OutlierDetector:
    """
    Detekuje anomální noty z FitResult residuálů pomocí MAD-sigma.

    Použití:
        detector = OutlierDetector(sigma_threshold=2.5)
        report   = detector.detect(fit_result)
        scores   = report.scores   # → KeyboardMap zbarvení
    """

    _log = get_logger(__name__, cls="OutlierDetector")

    SOURCE_WEIGHTS = {
        "b_curve":        0.30,
        "damping_law":    0.30,
        "spectral_shape": 0.25,
        "velocity_model": 0.15,
    }

    def __init__(self, sigma_threshold: float = 2.5):
        self.sigma_threshold = sigma_threshold

    @log_operation("detect")
    def detect(self, fit: FitResult) -> OutlierReport:
        """
        Agreguje outlier skóre přes všechny residuální zdroje.

        TODO: implementovat
        """
        log = get_logger(__name__, cls="OutlierDetector", method="detect")

        with OperationLogger(
            log, "detect",
            input={"sigma_threshold": self.sigma_threshold}
        ) as op:
            all_scores: dict[str, float] = {}

            for source, src_weight in self.SOURCE_WEIGHTS.items():
                residuals = self._get_residuals(fit, source)
                if not residuals:
                    op.progress("zdroj bez dat", source=source)
                    continue

                scores = self._score_from_residuals(residuals, source)
                for key, s in scores.items():
                    all_scores[key] = all_scores.get(key, 0.0) + s * src_weight

                flagged = sum(1 for s in scores.values() if s > 0.5)
                op.progress("zdroj zpracován",
                            source=source, notes=len(residuals), flagged=flagged)

            # Normalizace 0–1
            mx = max(all_scores.values(), default=1.0)
            if mx > 0:
                all_scores = {k: v / mx for k, v in all_scores.items()}

            flagged_total = sum(1 for s in all_scores.values() if s > 0.5)
            op.set_output({"total": len(all_scores), "flagged": flagged_total})

            flags = {}  # TODO: build_flags
            return OutlierReport(all_scores, flags, self.sigma_threshold)

    def mad_sigma(self, values: list[float]) -> tuple[float, float]:
        """
        Median Absolute Deviation estimátor.
        Returns: (median, sigma) kde sigma = 1.4826 * MAD.
        """
        arr = np.array(values, dtype=float)
        if len(arr) == 0:
            return 0.0, 1.0
        med = float(np.median(arr))
        mad = float(np.median(np.abs(arr - med)))
        return med, max(1.4826 * mad, 1e-9)  # guard against zero sigma

    def _get_residuals(
        self, fit: FitResult, source: str
    ) -> dict[str, float]:
        """
        Extrahuje residuály pro daný zdroj z FitResult.

        Mapování source → FitResult pole:
          "b_curve"        → fit.b_curve.residuals  {midi: sigma}
          "damping_law"    → fit.damping residuály   {midi_kN: sigma}
          "spectral_shape" → fit.shape_residuals     {note_key: dB}
          "velocity_model" → approx. z gamma_k variance (placeholder)
        """
        if source == "b_curve":
            if fit.b_curve is None:
                return {}
            # residuals jsou keyed by midi (int) — převedeme na note prefix
            return {
                f"m{midi:03d}": float(r)
                for midi, r in fit.b_curve.residuals.items()
            }

        if source == "damping_law":
            result: dict[str, float] = {}
            for midi, params in fit.damping.items():
                for k, r in params.residuals.items():
                    result[f"m{midi:03d}_k{k}"] = float(abs(r))
            return result

        if source == "spectral_shape":
            return {k: float(abs(v)) for k, v in fit.shape_residuals.items()}

        if source == "velocity_model":
            # Proxy: variance gamma_k per nota jako míra nestability
            result = {}
            for midi, gk in fit.gamma_k.items():
                if len(gk) > 1:
                    result[f"m{midi:03d}"] = float(np.std(gk))
            return result

        return {}

    def _score_from_residuals(
        self, residuals: dict[str, float], source_name: str
    ) -> dict[str, float]:
        """
        Převede residuály jednoho zdroje na normalizované skóre 0–1.

        Metoda: MAD-sigma z-score, clamp na [0, 4σ], normalizace na [0, 1].
        Klíče jsou normalizovány na note_prefix "m{midi:03d}".
        """
        if not residuals:
            return {}

        values = list(residuals.values())
        med, sigma = self.mad_sigma(values)

        scores: dict[str, float] = {}
        for raw_key, r in residuals.items():
            z = abs(r - med) / sigma
            # Normalizujeme na [0, 1] kde 4σ = 1.0
            score = min(z / (self.sigma_threshold * 2), 1.0)
            # Normalizuj klíč na "m{midi:03d}" prefix
            norm_key = raw_key.split("_")[0]  # "m060_vel4" → "m060"
            # Akumuluj max přes parciály/velocity téže noty
            scores[norm_key] = max(scores.get(norm_key, 0.0), score)

        return scores
