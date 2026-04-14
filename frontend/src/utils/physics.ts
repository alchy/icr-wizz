// utils/physics.ts — lokální fyzikální výpočty
// Mirror klíčových funkcí z Python backendu pro okamžitý UI feedback
// Changelog: 2025-04-14 v0.1 — initial

import type { PartialParams, BCurveParams, DampingParams } from '../types'

// ---------------------------------------------------------------------------
// Inharmonicita
// ---------------------------------------------------------------------------

/** f_k = k * f0 * sqrt(1 + B*k^2) */
export function partialFreq(k: number, f0: number, B: number): number {
  return k * f0 * Math.sqrt(1 + B * k * k)
}

/** B pro MIDI notu z fitted B-curve */
export function predictB(params: BCurveParams, midi: number): number {
  const f0  = midiToF0(midi)
  const lf0 = Math.log10(f0)
  const lbk = Math.log10(midiToF0(params.break_midi))
  if (lf0 < lbk) {
    return Math.pow(10, params.alpha_bass * lf0 + params.beta_bass)
  }
  return Math.pow(10, params.alpha_treble * lf0 + params.beta_treble)
}

// ---------------------------------------------------------------------------
// Damping law
// ---------------------------------------------------------------------------

/** tau = 1 / (R + eta * f^2) */
export function predictTau(params: DampingParams, fHz: number): number {
  const denom = params.R + params.eta * fHz * fHz
  return 1 / Math.max(denom, 1e-9)
}

// ---------------------------------------------------------------------------
// Bi-exponenciální decay
// ---------------------------------------------------------------------------

/** A(t) = A0 * [a1 * exp(-t/tau1) + (1-a1) * exp(-t/tau2)] */
export function biExpEnvelope(p: PartialParams, t: number): number {
  return p.A0 * (
    p.a1 * Math.exp(-t / p.tau1) +
    (1 - p.a1) * Math.exp(-t / p.tau2)
  )
}

/** Envelope v dB, clampováno na -80 dB */
export function biExpEnvelopeDb(p: PartialParams, t: number): number {
  const A = biExpEnvelope(p, t)
  return Math.max(20 * Math.log10(Math.max(A / p.A0, 1e-9)), -80)
}

/** Sada bodů (t, dB) pro kreslení decay envelope */
export function decayEnvelopePoints(
  p: PartialParams,
  nPoints = 200,
  tMax?: number,
): [number, number][] {
  const tEnd = tMax ?? Math.min(p.tau2 * 3, 30)
  return Array.from({ length: nPoints }, (_, i) => {
    const t = (i / (nPoints - 1)) * tEnd
    return [t, biExpEnvelopeDb(p, t)] as [number, number]
  })
}

// ---------------------------------------------------------------------------
// Velocity model
// ---------------------------------------------------------------------------

/** A0(vel) = A0_ref * (S(vel)/S(vel_ref))^gamma */
export function predictA0(gamma: number, A0ref: number, vel: number, velRef = 4): number {
  const s    = (vel + 1) / 8
  const sRef = (velRef + 1) / 8
  return A0ref * Math.pow(s / sRef, gamma)
}

/** attack_tau(vel) = tau_ref * (v/v_ref)^(-alpha), max 0.1s */
export function predictAttackTau(
  tauRef: number, alpha: number, vel: number, velRef = 4
): number {
  const v    = (vel + 1) / 8
  const vRef = (velRef + 1) / 8
  return Math.min(tauRef * Math.pow(v / vRef, -alpha), 0.1)
}

// ---------------------------------------------------------------------------
// Outlier skóre pomocné funkce
// ---------------------------------------------------------------------------

/** MAD-sigma odhad (client-side, pro preview) */
export function madSigma(values: number[]): [number, number] {
  if (values.length === 0) return [0, 1]
  const sorted = [...values].sort((a, b) => a - b)
  const med = sorted[Math.floor(sorted.length / 2)]
  const absDevs = values.map(v => Math.abs(v - med)).sort((a, b) => a - b)
  const mad = absDevs[Math.floor(absDevs.length / 2)]
  return [med, Math.max(1.4826 * mad, 1e-9)]
}

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

export function midiToF0(midi: number): number {
  return 440 * Math.pow(2, (midi - 69) / 12)
}

export function f0ToMidi(f0: number): number {
  return 69 + 12 * Math.log2(f0 / 440)
}

export function centsOffset(f0: number, midi: number): number {
  const expected = midiToF0(midi)
  return 1200 * Math.log2(f0 / expected)
}

/** Normalizovaná velocity S(vel) = (vel+1)/8 */
export function velNorm(vel: number): number {
  return (vel + 1) / 8
}

/** Harmonický index k → nominal frequency s inharmonicitou */
export function nominalPartialFreq(k: number, midi: number, B?: number): number {
  const f0  = midiToF0(midi)
  const bVal = B ?? 0
  return partialFreq(k, f0, bVal)
}
