# Piano Soundbank Editor — architektura systému

> Verze: 0.1-draft  
> Stav: pracovní dokument, průběžně aktualizováno

> Changelog:  
> 2025-04-14 v0.1  — initial draft  
> 2025-04-14 v0.2  — přidán logger.py do stacku a adresáře  
> 2025-04-14 v0.3  — tabulka paralelizace M4, oprava API endpointů  
> 2025-04-14 v0.4  — sekce stav backendu → singleton+executor vzor, dep-order graf

---

## Přehled

Piano Soundbank Editor je nástroj pro identifikaci, editaci a korekci fyzikálních vztahů v parametrických bankách additivní syntézy piana. Vstupem jsou JSON banky ve formátu `AdditiveSynthesisPianoCore`, výstupem jsou opravené banky a volitelně SysEx patche pro cílový syntetizér.

Systém je postaven na třech vrstvách:

```
[ Python backend ]  ←→  [ FastAPI bridge ]  ←→  [ React frontend ]
  fitting · modely        REST + WebSocket        views · stores · UI
  korekce · export        undo stack
```

---

## Technologický stack

| Vrstva | Technologie | Zdůvodnění |
|--------|-------------|------------|
| Backend | Python 3.11+ | numpy/scipy pro fitting, přirozená práce s JSON |
| Logging | `logger.py` (stdlib) | strukturovaný logging, konzole/JSON, žádné závislosti |
| Fitting | scipy.optimize, scipy.interpolate | deterministické výsledky, váhované fity, GIL-friendly |
| Paralelizace | ThreadPoolExecutor + ProcessPoolExecutor | viz tabulka níže |
| API | FastAPI + uvicorn | async, WebSocket pro live preview |
| Frontend | React + Vite + TypeScript | komponentní architektura, rychlý dev cyklus |
| State | Zustand | jednoduchý store, undo stack bez Redux overhead |
| Grafy | Plotly.js | subplot systém, box select, hover events |
| MIDI/SysEx | python-rtmidi (backend) | přímý přístup k MIDI portům |

### Paralelizační strategie (M4 15-core)

| Operace | Executor | Workers | Zdůvodnění |
|---------|----------|---------|------------|
| `BankLoader.list_banks` | ThreadPool | 12 | I/O-bound peek metadat |
| `BankLoader.load_multiple` | ThreadPool | 12 | I/O-bound čtení souborů |
| `BankLoader._parse_note_chunk` | ProcessPool | 14 | CPU-bound Pydantic validace |
| `DampingLawFitter` per-nota | ThreadPool | 14 | scipy linregress uvolňuje GIL |
| `VelocityModelFitter` per-nota | ThreadPool | 14 | scipy curve_fit uvolňuje GIL |
| `SpectralShapeFitter` per-k | ThreadPool | 14 | scipy interpolace uvolňuje GIL |
| `BankExporter._serialize_notes` | ThreadPool | 14 | Pydantic v2 Rust core, GIL-free |
| FastAPI endpointy (CPU ops) | ThreadPool | 4 | offload z asyncio event loop |
| SysEx patch | sekvenční | — | protokol vyžaduje sekvenci |

Env proměnné pro ladění: `BANK_IO_WORKERS`, `BANK_CPU_WORKERS`, `FIT_WORKERS`, `EXPORT_WORKERS`.

---

## Adresářová struktura

```
piano-editor/
├── backend/
│   ├── main.py                   # FastAPI app, WS endpoint, async orchestrace
│   ├── logger.py                 # Centrální logging — get_logger, @log_operation, OperationLogger
│   ├── models.py                 # Pydantic v2 schémata — sdílená závislost všech modulů
│   ├── bank_loader.py            # BankLoader — parse, validace, multi-file, paralelní
│   ├── relation_fitter.py        # RelationFitter + FitPlugin architektura
│   ├── outlier_detector.py       # OutlierDetector — MAD-sigma, OutlierReport
│   ├── anchor_manager.py         # AnchorManager — databáze korekcí, score → váhy
│   ├── correction_engine.py      # CorrectionEngine — propose, apply, diff
│   ├── bank_exporter.py          # BankExporter — JSON export, diff mode, CSV report
│   ├── midi_bridge.py            # MidiBridge — SysEx patch interface
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── store/
│   │   │   ├── bankStore.ts      # načtená banka, výběr souborů
│   │   │   ├── anchorStore.ts    # anchor databáze, score 0–9
│   │   │   └── correctionStore.ts # undo stack, pending corrections
│   │   ├── views/
│   │   │   ├── FileSelector.tsx  # výběr adresáře a JSON souborů
│   │   │   ├── KeyboardMap.tsx   # 88 kláves, outlier heat
│   │   │   ├── RelationView.tsx  # B-curve, τ profil, spektrální tvar
│   │   │   ├── NoteDetail.tsx    # parciály, decay, vel profil
│   │   │   ├── VelocityEditor.tsx # γ_k, attack_tau křivky
│   │   │   ├── AnchorPanel.tsx   # anchor databáze, score, export
│   │   │   ├── DiffPreview.tsx   # před/po, export trigger
│   │   │   └── MidiPanel.tsx     # SysEx patch interface
│   │   ├── api/
│   │   │   └── client.ts         # REST + WS wrapper
│   │   └── utils/
│   │       └── physics.ts        # lokální fyzikální výpočty
│   └── package.json
├── anchor-databases/             # uložené databáze korekcí (JSON)
├── soundbanks-corrected/         # exportované opravené banky
└── docs/
    ├── README-architecture.md    # tento soubor
    ├── README-analysis.md
    ├── README-ui.md
    ├── README-implementation.md
    └── README-visualization.md
```

---

## Datový tok — hlavní workflow

```
1. File selection
   uživatel vybere adresář → FileSelector zobrazí JSON soubory
   → POST /bank/load (jeden nebo více souborů)
   → BankLoader.parse() → validace schématu → BankState

2. Initial fit
   → POST /fit (anchor váhy = 1.0 pro všechny noty)
   → RelationFitter.fit_all() → FitResult (residuály per nota)
   → KeyboardMap se zbarví podle outlier skóre

3. Anchor marking
   uživatel klikne klávesu + přidělí score 0–9 → AnchorPanel
   → WS /ws/preview → live update spline a residuálů

4. Correction proposal
   → POST /corrections/propose
   → CorrectionEngine → list[Correction]
   → DiffPreview zobrazí diff (original → corrected)

5. Manual refinement (volitelné)
   VelocityEditor — drag γ_k bodů → WS preview
   RelationView — drag spline uzlů → live fit update

6. Export
   → POST /export (diff_only=True nebo full bank)
   → BankExporter → JSON soubor do soundbanks-corrected/
   → volitelně: POST /midi/patch → MidiBridge → SysEx

7. Anchor database
   kdykoliv: uložit aktuální anchor set jako pojmenovanou databázi
   → POST /anchors/save → anchor-databases/{name}.json
```

---

## API endpoints — přehled

| Metoda | Endpoint | Popis |
|--------|----------|-------|
| GET | `/bank/list?directory=...` | seznam JSON souborů v adresáři |
| POST | `/bank/load` | načtení jednoho nebo více souborů |
| POST | `/fit?bank_path=...` | spustit RelationFitter s anchor váhami |
| POST | `/corrections/propose?bank_path=...` | navrhnout korekce z FitResult |
| POST | `/corrections/apply` | aplikovat korekce (undo checkpoint) |
| POST | `/export` | exportovat opravenou banku |
| GET | `/anchors/list` | seznam uložených anchor databází |
| POST | `/anchors/save` | uložit anchor databázi (name v těle) |
| GET | `/anchors/{name}` | načíst anchor databázi podle názvu |
| DELETE | `/anchors/{name}` | smazat anchor databázi |
| GET | `/midi/ports` | seznam dostupných MIDI portů |
| POST | `/midi/connect` | připojit se k MIDI portu |
| POST | `/midi/disconnect` | odpojit MIDI port |
| POST | `/midi/patch` | odeslat SysEx patch na syntetizér |
| WS | `/ws/preview` | live preview při editaci |

---

## Principy modularity

Každý backend modul je nezávislý — lze ho testovat samostatně a vyměnit bez zásahu do ostatních. Komunikace mezi moduly probíhá výhradně přes datové třídy (`FitResult`, `Correction`, `AnchorDatabase`), ne přes sdílený stav.

Frontend views jsou odděleny od dat přes Zustand stores. View komponenta nikdy nevolá API přímo — vždy přes store akci. To umožňuje testovat views s mock daty a přidávat nové views bez zásahu do stávající logiky.

Plugin architektura pro fitting metody: `RelationFitter` přijímá seznam `FitPlugin` objektů, každý plugin implementuje `fit(notes, weights) → partial FitResult`. Nové metody (např. jiný spline algoritmus, jiný outlier model) se přidají jako nový plugin bez modifikace existujícího kódu.

---

## Stav backendu — sdílené singletons

Backend používá sdílené singleton instance služeb (`BankLoader`, `RelationFitter`, `CorrectionEngine`, `BankExporter`, `AnchorManager`, `MidiBridge`) a sdílený `ThreadPoolExecutor` pro offload CPU operací z asyncio event loop. Tyto objekty jsou bezstavové samy o sobě — nenesou stav konkrétní editační session.

Veškerý stav editace (načtená banka, anchor scores, undo stack, pending corrections) žije na frontendu v Zustand stores. Backend při každém requestu dostane kompletní kontext v těle requestu a vrátí výsledek — nevyžaduje server-side session.

Výjimka: WebSocket spojení `/ws/preview` udržuje `session_state` dict po dobu připojení (`bank`, `anchor_db`, `last_fit`) pro inkrementální live preview výpočty bez opakovaného přenosu celé banky.

### Závislostní pořadí modulů

```
models.py           ← nulové závislosti, základ všeho
logger.py           ← nulové závislosti, importuje stdlib only
    ↓
bank_loader.py      ← models, logger
anchor_manager.py   ← models, logger
    ↓
relation_fitter.py  ← models, logger, (anchor_manager lazy)
    ↓
outlier_detector.py ← models, logger, (fit result)
correction_engine.py← models, logger, relation_fitter
    ↓
bank_exporter.py    ← models, logger
midi_bridge.py      ← models, logger
    ↓
main.py             ← vše výše + fastapi
```
