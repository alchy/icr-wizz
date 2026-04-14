"""
logger.py — centrální logging infrastruktura Piano Soundbank Editoru

Poskytuje:
- Strukturovaný JSON formát pro strojové zpracování
- Čitelný konzolový formát pro development
- Kontext dekorátor: automaticky loguje vstup, výstup, výjimky metody
- OperationLogger: context manager pro víceřádkové operace
- Per-modul logger factory s konzistentním pojmenováním

Použití v každém modulu:
    from logger import get_logger, log_operation

    log = get_logger(__name__)          # logger pro modul

    class BankLoader:
        _log = get_logger(__name__, cls="BankLoader")

        @log_operation("load")
        def load(self, path: str) -> BankState:
            ...

        def load_multiple(self, paths):
            with OperationLogger(self._log, "load_multiple",
                                 input={"count": len(paths)}) as op:
                results = {}
                for p in paths:
                    try:
                        results[p] = self.load(p)
                        op.progress(f"načteno: {p}")
                    except Exception as e:
                        op.warn(f"přeskočeno: {p}", error=str(e))
                op.set_output({"loaded": len(results)})
                return results

Výstup konzole (DEV):
    2024-11-03 09:15:32.481 | INFO  | bank_loader.BankLoader.load
    → vstup:  path="ks-grand.json"
    ← výstup: notes=704, midi_range=21-108  [312ms]

Výstup JSON (PROD):
    {"ts":"2024-11-03T09:15:32.481Z","level":"INFO","module":"bank_loader",
     "class":"BankLoader","method":"load","event":"exit",
     "input":{"path":"ks-grand.json"},"output":{"notes":704},"elapsed_ms":312}

Changelog:
  2025-04-14 v0.1  — initial implementation, plně funkční
  2025-04-14 v0.2  — async_wrapper pro coroutine metody
  2025-04-14 v0.3  — app_log convenience logger, _describe_output helper
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# Konfigurace
# ---------------------------------------------------------------------------

# LOG_FORMAT: "console" (dev) nebo "json" (prod)
# Přečte se z env proměnné LOG_FORMAT, default = "console"
_LOG_FORMAT: str = os.environ.get("LOG_FORMAT", "console").lower()

# LOG_LEVEL: DEBUG, INFO, WARNING, ERROR, CRITICAL
_LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "DEBUG").upper()

# Maximální délka hodnoty pro inline zobrazení v logu
# Delší hodnoty jsou zkráceny s "..."
_MAX_VALUE_LEN: int = 120

# Zda logovat úspěšné vstupy/výstupy (False = pouze chyby a WARNING+)
_LOG_VERBOSE: bool = os.environ.get("LOG_VERBOSE", "1") != "0"


# ---------------------------------------------------------------------------
# Formátovač — konzolový
# ---------------------------------------------------------------------------

class _ConsoleFormatter(logging.Formatter):
    """
    Čitelný konzolový formát pro development:

        09:15:32.481 | INFO  | bank_loader.BankLoader.load → vstup: path="ks-grand.json"
        09:15:32.793 | INFO  | bank_loader.BankLoader.load ← výstup: notes=704  [312ms]
        09:15:33.001 | ERROR | bank_loader.BankLoader._parse_note ✗ FileNotFoundError: ...
    """

    LEVEL_COLORS = {
        "DEBUG":    "\033[37m",     # šedá
        "INFO":     "\033[36m",     # cyan
        "WARNING":  "\033[33m",     # žlutá
        "ERROR":    "\033[31m",     # červená
        "CRITICAL": "\033[35m",     # magenta
    }
    RESET = "\033[0m"
    DIM   = "\033[2m"
    BOLD  = "\033[1m"

    _use_color: bool = sys.stdout.isatty()

    def format(self, record: logging.LogRecord) -> str:
        ts    = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
        level = record.levelname.ljust(8)
        name  = record.name  # "bank_loader.BankLoader.load"

        if self._use_color:
            color = self.LEVEL_COLORS.get(record.levelname, "")
            level_str = f"{color}{level}{self.RESET}"
            name_str  = f"{self.BOLD}{name}{self.RESET}"
            ts_str    = f"{self.DIM}{ts}{self.RESET}"
        else:
            level_str = level
            name_str  = name
            ts_str    = ts

        msg = record.getMessage()

        # Přidej exception info pokud existuje
        if record.exc_info:
            exc_lines = traceback.format_exception(*record.exc_info)
            exc_str   = "".join(exc_lines).rstrip()
            msg = f"{msg}\n{exc_str}"

        return f"{ts_str} | {level_str} | {name_str}  {msg}"


# ---------------------------------------------------------------------------
# Formátovač — JSON (produkce / CI)
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    """
    Strukturovaný JSON formát — jeden JSON objekt per řádek.
    Vhodný pro agregátory logů (ELK, Loki, CloudWatch).
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts":      datetime.fromtimestamp(
                           record.created, tz=timezone.utc
                       ).isoformat(),
            "level":   record.levelname,
            "logger":  record.name,
        }

        # Strukturovaná extra pole (přidaná přes LoggerAdapter nebo extra=)
        for key in ("mod_name", "cls", "method", "event",
                    "input", "output", "elapsed_ms", "error", "detail"):
            if hasattr(record, key):
                # Publikujeme jako 'module' v JSON výstupu pro konzistenci
                out_key = "module" if key == "mod_name" else key
                payload[out_key] = getattr(record, key)

        # Hlavní zpráva pokud není zachycena ve strukturovaných polích
        msg = record.getMessage()
        if msg:
            payload["msg"] = msg

        if record.exc_info:
            payload["exception"] = traceback.format_exception(*record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Root logger setup — voláno jednou při importu
# ---------------------------------------------------------------------------

def _setup_root_logger() -> None:
    root = logging.getLogger("piano_editor")
    if root.handlers:
        return  # již inicializováno

    root.setLevel(getattr(logging, _LOG_LEVEL, logging.DEBUG))

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)

    if _LOG_FORMAT == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(_ConsoleFormatter())

    root.addHandler(handler)
    root.propagate = False


_setup_root_logger()


# ---------------------------------------------------------------------------
# Logger factory
# ---------------------------------------------------------------------------

def get_logger(
    module_name: str,
    cls: Optional[str] = None,
    method: Optional[str] = None,
) -> logging.LoggerAdapter:
    """
    Vrátí LoggerAdapter s kontextem modulu, třídy a metody.

    Pojmenování loggerů:
        get_logger("bank_loader")                  → "piano_editor.bank_loader"
        get_logger("bank_loader", cls="BankLoader") → "piano_editor.bank_loader.BankLoader"
        get_logger(__name__, cls="X", method="y")  → "piano_editor.<module>.X.y"

    Typické použití v modulu:
        log = get_logger(__name__)                   # modul-level
        class Foo:
            _log = get_logger(__name__, cls="Foo")   # třída-level

    Args:
        module_name: Jméno modulu — typicky __name__ (např. "bank_loader").
                     Prefix "piano_editor." se přidá automaticky.
        cls:         Název třídy (volitelné).
        method:      Název metody (volitelné, lze nastavit dynamicky).

    Returns:
        LoggerAdapter s extra kontextem.
    """
    # Sestav hierarchické jméno loggeru
    parts = ["piano_editor"]
    # Odstraň vlastní prefix pokud je přítomen
    clean = module_name.removeprefix("piano_editor.").removeprefix("piano_editor")
    if clean:
        parts.append(clean.lstrip("."))
    if cls:
        parts.append(cls)
    if method:
        parts.append(method)

    name   = ".".join(parts)
    logger = logging.getLogger(name)

    # Pozor: 'module' je rezervovaný klíč stdlib LogRecord → použijeme 'mod_name'
    extra  = {
        "mod_name": module_name,
        "cls":      cls or "",
        "method":   method or "",
    }
    return logging.LoggerAdapter(logger, extra)


# ---------------------------------------------------------------------------
# Pomocné formátovací funkce
# ---------------------------------------------------------------------------

def _fmt_value(v: Any) -> str:
    """Zkrátí dlouhé hodnoty pro inline zobrazení."""
    s = repr(v) if not isinstance(v, str) else f'"{v}"'
    if len(s) > _MAX_VALUE_LEN:
        return s[:_MAX_VALUE_LEN] + "…"
    return s


def _fmt_kwargs(d: dict) -> str:
    """Formátuje dict jako 'k=v, k=v' pro konzolový výstup."""
    return ", ".join(f"{k}={_fmt_value(v)}" for k, v in d.items())


# ---------------------------------------------------------------------------
# Dekorátor log_operation
# ---------------------------------------------------------------------------

def log_operation(
    operation_name: Optional[str] = None,
    log_input:  bool = True,
    log_output: bool = True,
    log_level:  int  = logging.INFO,
):
    """
    Dekorátor: automaticky loguje vstup, výstup a výjimky metody.

    Použití:
        class BankLoader:
            @log_operation("load")
            def load(self, path: str) -> BankState: ...

            @log_operation(log_output=False)  # skryje výstup (velká data)
            def load_multiple(self, paths): ...

    Logovaný kontext:
        → vstup:   argumenty metody (bez self)
        ← výstup:  typ + délka pokud je to kolekce, jinak repr
        ✗ chyba:   typ výjimky + zpráva + traceback na DEBUG úrovni

    Vstupní argumenty jsou logovány jako {jméno: hodnota} páry
    pomocí inspect.signature pro přesné mapování pozic → jmen.

    Args:
        operation_name: Přepíše název metody v logu (volitelné).
        log_input:      Logovat vstupní argumenty.
        log_output:     Logovat výstupní hodnotu.
        log_level:      Úroveň pro INFO zprávy (default INFO).
    """
    def decorator(func: Callable) -> Callable:
        sig = inspect.signature(func)

        @functools.wraps(func)
        def wrapper(self_or_first, *args, **kwargs):
            # Sestav logger s kontextem třídy a metody
            cls_name = type(self_or_first).__name__
            op_name  = operation_name or func.__name__
            module   = func.__module__
            log      = get_logger(module, cls=cls_name, method=op_name)

            # Mapuj poziční argumenty na jména parametrů
            all_args = (self_or_first,) + args
            try:
                bound    = sig.bind(*all_args, **kwargs)
                bound.apply_defaults()
                params   = dict(bound.arguments)
                params.pop("self", None)
            except Exception:
                params = {"args": args, "kwargs": kwargs}

            t_start = time.perf_counter()

            if _LOG_VERBOSE and log_input:
                input_str = _fmt_kwargs(params)
                log.log(log_level, f"→  {input_str}")

            try:
                result = func(self_or_first, *args, **kwargs)

                elapsed_ms = int((time.perf_counter() - t_start) * 1000)

                if _LOG_VERBOSE and log_output:
                    out_str = _describe_output(result)
                    log.log(log_level, f"←  {out_str}  [{elapsed_ms}ms]")

                return result

            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - t_start) * 1000)
                exc_type   = type(exc).__name__
                exc_msg    = str(exc)

                log.error(
                    f"✗  {exc_type}: {exc_msg}  [{elapsed_ms}ms]",
                    exc_info=True,
                )
                raise

        # Asynchronní varianta
        @functools.wraps(func)
        async def async_wrapper(self_or_first, *args, **kwargs):
            cls_name = type(self_or_first).__name__
            op_name  = operation_name or func.__name__
            module   = func.__module__
            log      = get_logger(module, cls=cls_name, method=op_name)

            all_args = (self_or_first,) + args
            try:
                bound  = sig.bind(*all_args, **kwargs)
                bound.apply_defaults()
                params = dict(bound.arguments)
                params.pop("self", None)
            except Exception:
                params = {"args": args, "kwargs": kwargs}

            t_start = time.perf_counter()

            if _LOG_VERBOSE and log_input:
                log.log(log_level, f"→  {_fmt_kwargs(params)}")

            try:
                result     = await func(self_or_first, *args, **kwargs)
                elapsed_ms = int((time.perf_counter() - t_start) * 1000)

                if _LOG_VERBOSE and log_output:
                    log.log(log_level, f"←  {_describe_output(result)}  [{elapsed_ms}ms]")

                return result

            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - t_start) * 1000)
                log.error(
                    f"✗  {type(exc).__name__}: {exc}  [{elapsed_ms}ms]",
                    exc_info=True,
                )
                raise

        return async_wrapper if inspect.iscoroutinefunction(func) else wrapper

    return decorator


def _describe_output(value: Any) -> str:
    """Stručný popis výstupní hodnoty pro log."""
    if value is None:
        return "None"
    if isinstance(value, dict):
        return f"dict({len(value)} klíčů)"
    if isinstance(value, (list, tuple, set)):
        return f"{type(value).__name__}({len(value)} položek)"
    if isinstance(value, str):
        return f'str({len(value)} znaků)'
    if hasattr(value, "__class__"):
        cls = type(value).__name__
        # Pydantic modely — zobraz klíčové atributy pokud existují
        for attr in ("note_key", "name", "source_path", "midi", "path"):
            if hasattr(value, attr):
                return f"{cls}({attr}={_fmt_value(getattr(value, attr))})"
        return cls
    return _fmt_value(value)


# ---------------------------------------------------------------------------
# OperationLogger — context manager pro složitější operace
# ---------------------------------------------------------------------------

class OperationLogger:
    """
    Context manager pro logování víceřádkových operací.

    Použití:
        with OperationLogger(log, "fit_all",
                             input={"notes": 704, "anchor_count": 12}) as op:
            for plugin in self.plugins:
                result = plugin.fit(bank, weights)
                op.progress(f"plugin {plugin.name} hotov")

            op.set_output({"outlier_count": 7})
        # Po __exit__: automaticky loguje celkový výsledek a elapsed time

    Metody:
        op.progress(msg, **extra)  — loguje průběžný stav (DEBUG)
        op.warn(msg, **extra)      — loguje varování (WARNING)
        op.set_output(data)        — nastaví výstup pro závěrečný log
    """

    def __init__(
        self,
        logger_adapter: logging.LoggerAdapter,
        operation: str,
        input: Optional[dict] = None,
        level: int = logging.INFO,
    ):
        self._log       = logger_adapter
        self._operation = operation
        self._input     = input or {}
        self._level     = level
        self._output: dict = {}
        self._warnings: list[str] = []
        self._t_start: float = 0.0

    def __enter__(self) -> "OperationLogger":
        self._t_start = time.perf_counter()
        if _LOG_VERBOSE and self._input:
            self._log.log(
                self._level,
                f"[{self._operation}] start  {_fmt_kwargs(self._input)}"
            )
        else:
            self._log.log(self._level, f"[{self._operation}] start")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        elapsed_ms = int((time.perf_counter() - self._t_start) * 1000)

        if exc_type is not None:
            self._log.error(
                f"[{self._operation}] ✗  {exc_type.__name__}: {exc_val}"
                f"  [{elapsed_ms}ms]  varování: {len(self._warnings)}",
                exc_info=(exc_type, exc_val, exc_tb),
            )
            return False  # výjimku nepotlačujeme

        warn_note = f"  ({len(self._warnings)} varování)" if self._warnings else ""
        out_str   = f"  {_fmt_kwargs(self._output)}" if self._output else ""
        self._log.log(
            self._level,
            f"[{self._operation}] ✓{out_str}  [{elapsed_ms}ms]{warn_note}",
        )
        return False

    def progress(self, msg: str, **extra) -> None:
        """Průběžná informace — loguje na DEBUG."""
        extra_str = f"  {_fmt_kwargs(extra)}" if extra else ""
        self._log.debug(f"[{self._operation}] ·  {msg}{extra_str}")

    def warn(self, msg: str, **extra) -> None:
        """Varování — loguje na WARNING, přidá do seznamu varování."""
        extra_str = f"  {_fmt_kwargs(extra)}" if extra else ""
        full_msg  = f"[{self._operation}] ⚠  {msg}{extra_str}"
        self._log.warning(full_msg)
        self._warnings.append(msg)

    def set_output(self, data: dict) -> None:
        """Nastaví výstupní data pro závěrečný log."""
        self._output = data


# ---------------------------------------------------------------------------
# Modul-level convenience logger
# ---------------------------------------------------------------------------

# Použij přímo pro jednoduché logy mimo třídy:
#   from logger import app_log
#   app_log.info("Server spuštěn na portu 8000")
app_log = get_logger("piano_editor")
