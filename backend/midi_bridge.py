"""
midi_bridge.py — SysEx patch interface pro cílový syntetizér

Paralelizace: SysEx je sekvenční protokol — syntetizér zpracovává
jednu zprávu najednou. Paralelní odesílání by způsobilo konflikty.
Optimalizace: batch mode s minimálním inter-message delay.

SysEx protokol cílového syntetizéru: PENDING SPEC.
Metody patch_note, _encode_note_params, _send_sysex jsou mockupy —
logují co by odeslaly, vrací úspěch, a jsou nahraditelné real impl.

Changelog:
  2025-04-14 v0.1  — initial scaffold
  2025-04-14 v0.2  — logging integrace, PatchResult, BankPatchResult
  2025-04-14 v0.3  — patch_bank: sekvenční loop s progress callback
  2025-04-14 v0.4  — midi_range/vel_range filtry v patch_bank
  2025-04-14 v0.5  — IMPLEMENTOVÁNO: list_ports, connect, disconnect (rtmidi)
  2025-04-14 v0.6  — MOCKUP: send_identity_request, patch_note, _send_sysex,
                     _encode_note_params — placeholder čeká na SysEx spec
"""

from __future__ import annotations

from typing import Callable, Optional

from logger import OperationLogger, get_logger, log_operation
from models import BankState, NoteParams


class MidiConnectionError(Exception):
    pass

class MidiPatchError(Exception):
    pass

class SysExProtocolError(Exception):
    pass


class PatchResult:
    def __init__(
        self, midi: int, vel: int, success: bool,
        error: Optional[str] = None, bytes_sent: int = 0,
    ):
        self.midi       = midi
        self.vel        = vel
        self.success    = success
        self.error      = error
        self.bytes_sent = bytes_sent


class BankPatchResult:
    def __init__(self, results: list[PatchResult]):
        self.results = results

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if not r.success)

    @property
    def errors(self) -> list[str]:
        return [r.error for r in self.results if r.error]

    def summary(self) -> dict:
        return {
            "total":   len(self.results),
            "success": self.success_count,
            "failed":  self.failed_count,
            "errors":  self.errors,
        }


class MidiBridge:
    """
    Komunikace se syntetizérem přes MIDI/SysEx.

    SysEx protokol cílového syntetizéru bude doplněn v samostatné spec.

    Použití:
        bridge = MidiBridge()
        bridge.connect(bridge.list_ports()[0])
        result = bridge.patch_bank(corrected_bank)
        bridge.disconnect()
    """

    _log = get_logger(__name__, cls="MidiBridge")

    def __init__(self, response_timeout_ms: int = 500):
        self.response_timeout_ms = response_timeout_ms
        self._port               = None
        self._port_name: Optional[str] = None
        self._connected: bool    = False
        self._log.debug(
            f"inicializován  response_timeout_ms={response_timeout_ms}"
        )

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def port_name(self) -> Optional[str]:
        return self._port_name

    # ------------------------------------------------------------------
    # Port management
    # ------------------------------------------------------------------

    @log_operation("list_ports")
    def list_ports(self) -> list[str]:
        """
        Vrátí seznam MIDI výstupních portů přes python-rtmidi.
        Pokud rtmidi není nainstalováno, vrátí prázdný seznam s varováním.
        """
        log = get_logger(__name__, cls="MidiBridge", method="list_ports")
        try:
            import rtmidi
            out = rtmidi.MidiOut()
            ports = out.get_ports()
            del out
            log.info(f"nalezeno {len(ports)} MIDI portů: {ports}")
            return ports
        except ImportError:
            log.warning("python-rtmidi není nainstalováno — vrácen prázdný seznam")
            return []
        except Exception as e:
            log.error(f"✗  chyba při výpisu portů  error={e}")
            return []

    @log_operation("connect")
    def connect(self, port_name: str) -> None:
        """
        Připojí se k MIDI portu podle jména.
        Raises MidiConnectionError pokud port neexistuje nebo nelze otevřít.
        """
        log = get_logger(__name__, cls="MidiBridge", method="connect")
        log.info(f"připojuji se  port={port_name!r}")

        try:
            import rtmidi
        except ImportError:
            raise MidiConnectionError(
                "python-rtmidi není nainstalováno. "
                "Nainstalujte: pip install python-rtmidi"
            )

        try:
            out   = rtmidi.MidiOut()
            ports = out.get_ports()
        except Exception as e:
            raise MidiConnectionError(f"Nelze inicializovat MIDI: {e}")

        # Hledej port podle přesného jména nebo prefixu
        idx = None
        for i, p in enumerate(ports):
            if p == port_name or p.startswith(port_name):
                idx = i
                break

        if idx is None:
            available = ", ".join(ports) or "(žádné)"
            raise MidiConnectionError(
                f"Port {port_name!r} nenalezen. "
                f"Dostupné porty: {available}"
            )

        try:
            out.open_port(idx)
        except Exception as e:
            raise MidiConnectionError(f"Nelze otevřít port {port_name!r}: {e}")

        self._port      = out
        self._port_name = port_name
        self._connected = True
        log.info(f"připojeno  port={port_name!r}  idx={idx}")

    @log_operation("disconnect")
    def disconnect(self) -> None:
        """Bezpečně zavře MIDI port. Idempotentní."""
        log = get_logger(__name__, cls="MidiBridge", method="disconnect")
        if not self._connected:
            log.debug("není připojen, nic se nestane")
            return
        log.info(f"odpojuji  port={self._port_name!r}")
        try:
            if self._port is not None:
                self._port.close_port()
                del self._port
                self._port = None
        except Exception as e:
            log.warning(f"chyba při odpojování  error={e}")
        finally:
            self._connected = False
            self._port_name = None

    # ------------------------------------------------------------------
    # Diagnostika
    # ------------------------------------------------------------------

    @log_operation("send_identity_request")
    def send_identity_request(self) -> dict:
        """
        Universal SysEx Identity Request (F0 7E 7F 06 01 F7).

        Odešle standardní GM Identity Request a čeká na Identity Reply
        (F0 7E <device_id> 06 02 <mfr_id> <family> <member> <version> F7).

        MOCKUP: dokud není k dispozici SysEx spec cílového syntetizéru,
        metoda odešle skutečný Identity Request přes rtmidi a vrátí
        mock response. Real parsing odpovědi bude doplněn ze spec.

        Raises:
            MidiConnectionError: není připojen.
        """
        log = get_logger(__name__, cls="MidiBridge", method="send_identity_request")
        if not self._connected:
            log.error("✗  není připojen")
            raise MidiConnectionError("Není připojen k MIDI portu")

        # GM Universal SysEx Identity Request
        IDENTITY_REQUEST = [0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7]

        try:
            self._port.send_message(IDENTITY_REQUEST)
            log.info(f"Identity Request odesláno  hex={bytes(IDENTITY_REQUEST).hex(' ')}")
        except Exception as e:
            log.warning(f"odesílání selhalo  error={e}")

        # MOCKUP response — bude nahrazeno parsováním skutečné MIDI odpovědi
        # dle SysEx spec cílového syntetizéru
        mock_response = {
            "status":       "mock",
            "note":         "SysEx spec pending — odpověď nebyla parsována",
            "device_id":    0x7F,
            "manufacturer": "unknown",
            "family":       0x0000,
            "member":       0x0000,
            "version":      "?.?.?.?",
        }
        log.info(f"Identity Response (mock)  {mock_response}")
        return mock_response

    # ------------------------------------------------------------------
    # Patch operace
    # ------------------------------------------------------------------

    @log_operation("patch_note")
    def patch_note(
        self,
        midi: int,
        vel: int,
        note_params: NoteParams,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> PatchResult:
        """
        Odešle SysEx patch pro jednu notu + velocity vrstvu.

        MOCKUP: kóduje parametry do strukturovaného bytearray (viz
        _encode_note_params), loguje hex dump co by bylo odesláno,
        ale bez skutečné SysEx specifikace neodesílá na syntetizér.

        Jakmile bude k dispozici spec, stačí odkomentovat volání
        _send_sysex() a nahradit _encode_note_params() real enkodérem.

        Raises:
            MidiConnectionError: není připojen.
        """
        log = get_logger(__name__, cls="MidiBridge", method="patch_note")
        if not self._connected:
            log.error(f"✗  není připojen  midi={midi}  vel={vel}")
            raise MidiConnectionError("Není připojen k MIDI portu")

        n_partials = len(note_params.partials)
        log.debug(
            f"patch_note  midi={midi}  vel={vel}  "
            f"f0={note_params.f0_hz:.2f}Hz  "
            f"B={note_params.B:.2e}  "
            f"partials={n_partials}"
        )

        try:
            payload = self._encode_note_params(midi, vel, note_params)
            log.debug(
                f"encoded  bytes={len(payload)}  "
                f"hex_head={payload[:16].hex(' ')}…"
            )
        except Exception as e:
            log.warning(f"enkódování selhalo  error={e}")
            # Mock pokračuje i bez enkódování
            payload = bytes(8)

        # --- Skutečné odeslání (zakomentováno — čeká na SysEx spec) ---
        # response = self._send_sysex(payload)
        # if response is None:
        #     return PatchResult(midi, vel, False, "timeout — žádná odpověď")
        # if not self._verify_ack(response):
        #     return PatchResult(midi, vel, False, f"NACK: {response.hex()}")
        # ------------------------------------------------------------

        log.info(
            f"✓  mock patch  midi={midi}  vel={vel}  "
            f"would_send={len(payload)}B"
        )
        return PatchResult(
            midi=midi,
            vel=vel,
            success=True,
            bytes_sent=len(payload),
        )

    @log_operation("patch_bank")
    def patch_bank(
        self,
        bank: BankState,
        midi_range: Optional[tuple[int, int]] = None,
        vel_range:  Optional[tuple[int, int]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> BankPatchResult:
        """
        Odešle SysEx patche pro celou banku.

        Sekvenční — SysEx protokol vyžaduje sekvenční zpracování.
        Optimalizace: minimální inter-message delay dle spec syntetizéru.

        TODO: implementovat po přijetí SysEx spec
        """
        log = get_logger(__name__, cls="MidiBridge", method="patch_bank")

        with OperationLogger(
            log, "patch_bank",
            input={
                "notes":       bank.note_count(),
                "midi_range":  midi_range,
                "vel_range":   vel_range,
            }
        ) as op:
            if not self._connected:
                log.error("✗  není připojen")
                raise MidiConnectionError("Není připojen k MIDI portu")

            notes_to_patch = [
                n for n in bank.notes.values()
                if (midi_range is None or midi_range[0] <= n.midi <= midi_range[1])
                and (vel_range is None or vel_range[0] <= n.vel <= vel_range[1])
            ]
            op.progress("noty připraveny", count=len(notes_to_patch))

            results: list[PatchResult] = []
            for i, note in enumerate(notes_to_patch):
                try:
                    r = self.patch_note(note.midi, note.vel, note)
                    results.append(r)
                    if progress_callback:
                        progress_callback(
                            i + 1, len(notes_to_patch),
                            f"m{note.midi:03d}_vel{note.vel}"
                        )
                except Exception as e:
                    op.warn("patch selhal",
                            midi=note.midi, vel=note.vel, error=str(e))
                    results.append(
                        PatchResult(note.midi, note.vel, False, str(e))
                    )

            bpr = BankPatchResult(results)
            op.set_output(bpr.summary())
            return bpr

    # ------------------------------------------------------------------
    # SysEx helpers
    # ------------------------------------------------------------------

    def _encode_note_params(
        self, midi: int, vel: int, note_params: NoteParams
    ) -> bytes:
        """
        Kóduje NoteParams do SysEx bytearray.

        MOCKUP struktura (bude nahrazena real spec):

          Offset  Bytes  Obsah
          ------  -----  -----
          0       1      0xF0  SysEx start
          1       1      0x00  Manufacturer ID byte 1  (placeholder)
          2       1      0x00  Manufacturer ID byte 2  (placeholder)
          3       1      0x00  Manufacturer ID byte 3  (placeholder)
          4       1      0x01  Device ID               (placeholder)
          5       1      0x10  Command: SET_NOTE_PARAMS (placeholder)
          6       1      midi  MIDI note number (0–127)
          7       1      vel   Velocity layer  (0–7)
          8       4      f0_hz jako IEEE 754 float32 big-endian
          12      4      B     jako IEEE 754 float32 big-endian
          16      2      n_partials jako uint16 big-endian
          18      k*16   partials: k(1B), A0(4B f32), tau1(4B f32),
                                   tau2(4B f32), beat_hz(3B f24-fixed)
          ...     ...    [ostatní parametry dle spec]
          last    1      0xF7  SysEx end

        Parametry jsou kódovány jako 7-bit MIDI safe hodnoty nebo
        ve vyhrazeném SysEx datovém prostoru dle spec syntetizéru.

        POZOR: tato implementace je POUZE pro ladění a logging.
        Skutečný formát bude definován SysEx specifikací.
        """
        import struct

        log = get_logger(__name__, cls="MidiBridge", method="_encode_note_params")

        # Hlavička (mock manufacturer + command)
        header = bytes([
            0xF0,        # SysEx start
            0x00, 0x00, 0x00,  # Manufacturer ID — PENDING SPEC
            0x01,        # Device ID
            0x10,        # Command: SET_NOTE_PARAMS
            midi & 0x7F, # MIDI note
            vel  & 0x07, # Velocity layer 0–7
        ])

        # Základní parametry noty
        note_data = struct.pack(
            ">ff",
            note_params.f0_hz,
            note_params.B,
        )
        note_data += struct.pack(">H", len(note_params.partials))
        note_data += struct.pack(">f", note_params.attack_tau)
        note_data += struct.pack(">f", note_params.A_noise)

        # Parciály (zkrácená verze — spec definuje přesný layout)
        partials_data = bytearray()
        for p in note_params.partials[:64]:  # max 64 parciálů
            partials_data += struct.pack(
                ">Bffff",
                p.k & 0x7F,
                p.A0,
                p.tau1,
                p.tau2,
                p.beat_hz,
            )

        payload = header + note_data + bytes(partials_data) + bytes([0xF7])

        log.debug(
            f"encoded  midi={midi}  vel={vel}  "
            f"partials={len(note_params.partials)}  "
            f"total_bytes={len(payload)}"
        )
        return payload

    def _send_sysex(self, data: bytes) -> Optional[bytes]:
        """
        Odešle SysEx message přes rtmidi a čeká na odpověď.

        MOCKUP: odesílá zprávu přes _port.send_message() ale
        neimplementuje příjem odpovědi (vyžaduje MidiIn port a
        callback — bude doplněno ze SysEx spec).

        Skutečná implementace:
          1. Otevřít MidiIn port se stejným device ID
          2. Zaregistrovat callback pro příjem odpovědi
          3. Odeslat zprávu
          4. Čekat na odpověď max response_timeout_ms
          5. Parsovat ACK/NACK dle spec
          6. Vrátit response bytes nebo None při timeoutu

        Raises:
            MidiConnectionError: není připojen.
            SysExProtocolError:  neočekávaný formát odpovědi.
        """
        log = get_logger(__name__, cls="MidiBridge", method="_send_sysex")

        if not self._connected or self._port is None:
            raise MidiConnectionError("Není připojen k MIDI portu")

        if not data or data[0] != 0xF0 or data[-1] != 0xF7:
            raise SysExProtocolError(
                f"Neplatný SysEx frame: "
                f"start={data[0]:02X}  end={data[-1]:02X}"
            )

        log.debug(
            f"odeslání  bytes={len(data)}  "
            f"hex_head={data[:8].hex(' ')}  "
            f"hex_tail=...{data[-4:].hex(' ')}"
        )

        try:
            # Odeslání SysEx jako seznam int (rtmidi API)
            self._port.send_message(list(data))
        except Exception as e:
            raise MidiConnectionError(f"rtmidi send_message selhal: {e}")

        # MOCKUP: odpověď není implementována — vrátíme None
        # Real impl: čekat na MidiIn callback max response_timeout_ms
        log.debug(
            f"zpráva odeslána  bytes={len(data)}  "
            f"response=mock_none (SysEx spec pending)"
        )
        return None   # None = timeout nebo mock; real impl vrátí bytes

    def _verify_ack(self, response: bytes) -> bool:
        """
        Ověří ACK response ze syntetizéru.

        MOCKUP: vždy vrátí True.
        Real impl: parsovat dle SysEx spec cílového syntetizéru.
        Typicky: F0 <mfr> <dev> <cmd_ack> F7  nebo  NACK s error code.
        """
        log = get_logger(__name__, cls="MidiBridge", method="_verify_ack")
        # PENDING: parsování ACK/NACK dle spec
        log.debug(f"ACK check (mock)  bytes={len(response)}  -> True")
        return True
