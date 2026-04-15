// types.ts — TypeScript mirror Pydantic modelů backendu
// Changelog:
//   2025-04-14 v0.1 — initial, mirror models.py

// ---------------------------------------------------------------------------
// Parametrické typy
// ---------------------------------------------------------------------------

export interface PartialParams {
  k: number
  f_hz: number
  A0: number
  tau1: number
  tau2: number
  a1: number
  beat_hz: number
  beat_depth: number
  phi: number
  fit_quality: number
}

export interface EqBiquad {
  b: [number, number, number]
  a: [number, number]
}

export interface SpectralEq {
  freqs_hz: number[]
  gains_db: number[]
  stereo_width_factor: number
}

export interface NoteParams {
  midi: number
  vel: number
  f0_hz: number
  B: number
  phi_diff: number
  attack_tau: number
  A_noise: number
  noise_centroid_hz: number
  rms_gain: number
  n_strings: number
  rise_tau: number
  stereo_width: number
  pan_correction: number
  partials: PartialParams[]
  eq_biquads: EqBiquad[]
  spectral_eq?: SpectralEq
}

// ---------------------------------------------------------------------------
// Banka
// ---------------------------------------------------------------------------

export interface BankMetadata {
  instrument_name: string
  midi_range_from: number
  midi_range_to: number
  sr: number
  target_rms: number
  vel_gamma: number
  k_max: number
  rng_seed: number
  duration_s: number
}

export interface StereoConfig {
  keyboard_spread: number
  pan_spread: number
  stereo_decorr: number
}

/** Lehký přehled — bez plných dat not */
export interface BankStateResponse {
  source_path: string
  instrument_name: string
  midi_range_from: number
  midi_range_to: number
  sr: number
  k_max: number
  note_count: number
  note_keys: string[]          // "m060_vel4"
  stereo_config?: StereoConfig
}

export interface BankListItem {
  path: string
  filename: string
  instrument_name: string
  midi_range: string
  sr: number
  note_count: number
  file_size_kb: number
}

export interface LoadResponse {
  loaded: string[]
  errors: string[]
  states: Record<string, BankStateResponse>
}

// ---------------------------------------------------------------------------
// Fitting
// ---------------------------------------------------------------------------

export interface BCurveParams {
  alpha_bass: number
  beta_bass: number
  alpha_treble: number
  beta_treble: number
  break_midi: number
  residuals: Record<number, number>  // {midi: sigma}
}

export interface DampingParams {
  R: number
  eta: number
  residuals: Record<number, number>  // {k: residual}
}

/** Lehký výsledek fitu pro KeyboardMap */
export interface FitSummary {
  outlier_scores: Record<string, number>          // "m060" → 0–1
  outlier_scores_per_vel: Record<string, number>  // "m060_vel4" → 0–1
  b_curve?: BCurveParams
  fit_timestamp: string
  anchor_db_name?: string
}

/** Detailní výsledek — lazy load pro NoteDetail */
export interface FitDetailsResponse {
  damping: Record<number, DampingParams>          // {midi: params}
  gamma_k: Record<number, number[]>               // {midi: [γ_k]}
  attack_alpha: Record<number, number>
  attack_tref: Record<number, number>
  shape_residuals: Record<string, number>         // {note_key: dB}
}

// ---------------------------------------------------------------------------
// Anchor
// ---------------------------------------------------------------------------

export interface AnchorEntry {
  midi: number
  vel: number          // -1 = wildcard
  score: number        // 0–9
  note?: string
  timestamp: string
}

export interface AnchorDatabase {
  name: string
  description?: string
  created: string
  modified: string
  instrument_hint?: string
  entries: AnchorEntry[]
}

export interface AnchorListItem {
  name: string
  path: string
  description?: string
  instrument_hint?: string
  modified: string
  entry_count: number
}

export interface CoverageReport {
  bass: number
  mid: number
  treble: number
  vel_low: number
  vel_high: number
  total: number
  warnings: string[]
  ok: boolean
}

export interface AnchorSuggestion {
  midi: number
  vel: number
  region: string
  quality: number
  reason: string
  priority: number
}

// ---------------------------------------------------------------------------
// Korekce
// ---------------------------------------------------------------------------

export type CorrectionSource =
  | 'b_curve_fit'
  | 'damping_law'
  | 'anchor_interp'
  | 'spectral_shape'
  | 'velocity_model'
  | 'manual'

export interface Correction {
  midi: number
  vel: number
  field: string            // "B" | "tau1_k3" | "attack_tau" | ...
  original: number
  corrected: number
  source: CorrectionSource
  delta_pct: number
}

export interface CorrectionSet {
  corrections: Correction[]
  created: string
  description: string
  anchor_db_name?: string
}

export interface CorrectionSummary {
  total_corrections: number
  affected_notes: number
  max_delta_pct: number
  by_source: Record<string, number>
}

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------

export interface WsMessage {
  action: 'init' | 'update_anchor' | 'move_spline_node' | 'drag_gamma_k'
  payload: Record<string, unknown>
}

export interface WsResponse {
  outlier_scores: Record<string, number>
  outlier_scores_per_vel: Record<string, number>
  spline_points: [number, number][]
  fit_quality: number
  error?: string
}

// ---------------------------------------------------------------------------
// UI state pomocné typy
// ---------------------------------------------------------------------------

export type PanelView =
  | 'extract'
  | 'relation'
  | 'note_detail'
  | 'velocity_editor'
  | 'anchor_panel'
  | 'diff_preview'
  | 'param_space'
  | 'param_space_3d'
  | 'midi_panel'

export type OutlierLevel = 'ok' | 'mild' | 'warn' | 'crit'

export function outlierLevel(score: number): OutlierLevel {
  if (score < 0.2) return 'ok'
  if (score < 0.5) return 'mild'
  if (score < 0.8) return 'warn'
  return 'crit'
}

export function outlierColor(score: number): string {
  const level = outlierLevel(score)
  return {
    ok:   'var(--c-ok)',
    mild: 'var(--c-mild)',
    warn: 'var(--c-warn)',
    crit: 'var(--c-crit)',
  }[level]
}

export function noteKeyToMidiVel(key: string): [number, number] {
  const m = key.match(/m(\d+)_vel(\d+)/)
  if (!m) return [0, 0]
  return [parseInt(m[1]), parseInt(m[2])]
}

export function midiToNoteName(midi: number): string {
  const notes = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B']
  return `${notes[midi % 12]}${Math.floor(midi / 12) - 1}`
}

export function midiToF0(midi: number): number {
  return 440 * Math.pow(2, (midi - 69) / 12)
}
