// utils/format.ts — formátovací pomocné funkce pro UI
// Changelog:
//   2025-04-14 v0.1 — initial
//   2025-04-14 v0.2 — midiVelToLayer, layerToMidiVelRange, VEL_LAYER_LABELS

/** Číslo na exponenciální notaci: 0.000123 → "1.23e-4" */
export function fmtExp(n: number, sig = 3): string {
  return n.toExponential(sig - 1)
}

/** Číslo na N platných číslic */
export function fmtSig(n: number, sig = 4): string {
  return n.toPrecision(sig)
}

/** Milisekundy ze sekund */
export function fmtMs(s: number, decimals = 1): string {
  return `${(s * 1000).toFixed(decimals)} ms`
}

/** Hz — přidá jednotku */
export function fmtHz(hz: number): string {
  if (hz >= 1000) return `${(hz / 1000).toFixed(2)} kHz`
  return `${hz.toFixed(1)} Hz`
}

/** Delta procenta s + znaménkem */
export function fmtDelta(pct: number): string {
  return `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`
}

/** Zkrátí cestu na filename */
export function fmtPath(path: string): string {
  return path.split(/[/\\]/).pop() ?? path
}

/** ISO timestamp → lokální datum/čas */
export function fmtTimestamp(iso: string): string {
  return new Date(iso).toLocaleString('cs', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

/** Score 0–9 → textový popis */
export function fmtScore(score: number): string {
  if (score <= 0) return 'ignorovat'
  if (score <= 2) return 'velmi nízká'
  if (score <= 4) return 'nízká'
  if (score <= 6) return 'průměrná'
  if (score <= 8) return 'dobrá'
  return 'referenční'
}

// ---------------------------------------------------------------------------
// MIDI velocity konverze
// ---------------------------------------------------------------------------

/**
 * MIDI velocity 0–127 → velocity layer 0–7
 *
 * Banka používá 8 velocity vrstev (0–7).
 * MIDI velocity 0–127 se mapuje lineárně: layer = floor(midiVel / 16)
 *
 *  MIDI vel  →  layer  →  label
 *   0–15     →   0     →  pp
 *  16–31     →   1     →  p
 *  32–47     →   2     →  mp
 *  48–63     →   3     →  mf
 *  64–79     →   4     →  mf+
 *  80–95     →   5     →  f
 *  96–111    →   6     →  ff-
 * 112–127    →   7     →  ff
 */
export function midiVelToLayer(midiVel: number): number {
  return Math.min(7, Math.floor(Math.max(0, midiVel) / 16))
}

/** Velocity layer 0–7 → střední MIDI velocity pro danou vrstvu */
export function layerToMidiVelMid(layer: number): number {
  return layer * 16 + 8
}

/** Velocity layer 0–7 → MIDI rozsah [min, max] */
export function layerToMidiVelRange(layer: number): [number, number] {
  return [layer * 16, Math.min(127, layer * 16 + 15)]
}

/** Formátuje velocity layer jako "vel 3 (mf) · MIDI 48–63" */
export function fmtVelLayer(layer: number, labels = VEL_LAYER_LABELS): string {
  if (layer === -1) return 'všechny velocity'
  const [lo, hi] = layerToMidiVelRange(layer)
  return `${labels[layer]} · MIDI ${lo}–${hi}`
}

export const VEL_LAYER_LABELS = ['pp','p','mp','mf','mf+','f','ff-','ff'] as const
