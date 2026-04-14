// components/FitHeatmap.tsx — spojitý heatmap pás 88 not × 8 velocity vrstev
// Responsivní — vyplní dostupný prostor, buňky se škálují

import React, { useRef, useEffect, useCallback } from 'react'
import { useFitStore }    from '../store/fitStore'
import { useBankStore }   from '../store/bankStore'
import { useUiStore }     from '../store/uiStore'

const MIDI_MIN = 21
const MIDI_MAX = 108
const NOTE_COUNT = MIDI_MAX - MIDI_MIN + 1  // 88
const VEL_COUNT = 8
const VEL_LABELS = ['pp', 'p', 'mp', 'mf', 'mf+', 'f', 'ff-', 'ff']
const GAP = 2       // mezera mezi buňkami
const LABEL_W = 24  // prostor pro velocity labely vlevo

/** Interpolace barvy: fit quality 0→červená, 0.5→žlutá, 1→zelená */
function fitColor(quality: number): string {
  const q = Math.max(0, Math.min(1, quality))
  const hue = q * 145
  const sat = 55 + (1 - q) * 15
  const lit = 25 + q * 13
  return `hsl(${hue}, ${sat}%, ${lit}%)`
}

const COLOR_MISSING = '#1A1C20'

export const FitHeatmap: React.FC = () => {
  const canvasRef    = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  const outlierScoreVel = useFitStore(s => s.outlierScoreVel)
  const summary         = useFitStore(s => s.summary)
  const noteKeys        = useBankStore(s => s.activeState())?.note_keys ?? []
  const selectedVel     = useUiStore(s => s.selectedVel)
  const selectedMidi    = useUiStore(s => s.selectedMidi)
  const selectNote      = useUiStore(s => s.selectNote)
  const setVelocity     = useUiStore(s => s.setVelocity)

  const presentKeys = React.useMemo(() => new Set(noteKeys), [noteKeys])

  // Vypočítá rozměry buněk na základě dostupného prostoru
  const getLayout = useCallback(() => {
    const container = containerRef.current
    if (!container) return null
    const w = container.clientWidth
    const h = container.clientHeight

    const availW = w - LABEL_W
    const availH = h

    const cellW = Math.max(2, (availW - GAP * (NOTE_COUNT - 1)) / NOTE_COUNT)
    const cellH = Math.max(2, (availH - GAP * (VEL_COUNT - 1)) / VEL_COUNT)

    return { w, h, cellW, cellH }
  }, [])

  const draw = useCallback(() => {
    const canvas = canvasRef.current
    const layout = getLayout()
    if (!canvas || !layout) return

    const { w, h, cellW, cellH } = layout
    const dpr = window.devicePixelRatio || 1
    canvas.width  = w * dpr
    canvas.height = h * dpr
    canvas.style.width  = `${w}px`
    canvas.style.height = `${h}px`
    const ctx = canvas.getContext('2d')!
    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, w, h)

    // Velocity labely vlevo
    ctx.font = `${Math.min(10, cellH - 1)}px monospace`
    ctx.textAlign = 'right'
    ctx.textBaseline = 'middle'
    for (let vel = 0; vel < VEL_COUNT; vel++) {
      const row = VEL_COUNT - 1 - vel  // ff nahoře
      const y = row * (cellH + GAP)
      const isActive = vel === selectedVel
      ctx.fillStyle = isActive ? '#E8E6E0' : '#5C5A55'
      ctx.fillText(VEL_LABELS[vel], LABEL_W - 4, y + cellH / 2)
    }

    // Heatmap buňky
    for (let i = 0; i < NOTE_COUNT; i++) {
      const midi = MIDI_MIN + i
      const x = LABEL_W + i * (cellW + GAP)
      for (let vel = 0; vel < VEL_COUNT; vel++) {
        const row = VEL_COUNT - 1 - vel
        const y = row * (cellH + GAP)
        const noteKey = `m${String(midi).padStart(3, '0')}_vel${vel}`
        const present = presentKeys.has(noteKey)

        if (present) {
          const quality = 1 - outlierScoreVel(noteKey)
          ctx.fillStyle = fitColor(quality)
        } else {
          ctx.fillStyle = COLOR_MISSING
        }

        ctx.fillRect(x, y, cellW, cellH)
      }
    }

    // Zvýraznění řádku vybrané velocity
    {
      const row = VEL_COUNT - 1 - selectedVel
      const y = row * (cellH + GAP)
      ctx.strokeStyle = '#BA7517'
      ctx.lineWidth = 1.5
      ctx.strokeRect(
        LABEL_W - 1.5,
        y - 1.5,
        NOTE_COUNT * (cellW + GAP) - GAP + 3,
        cellH + 3,
      )
    }

    // Zvýraznění sloupce vybrané noty
    if (selectedMidi !== null && selectedMidi >= MIDI_MIN && selectedMidi <= MIDI_MAX) {
      const col = selectedMidi - MIDI_MIN
      const x = LABEL_W + col * (cellW + GAP)
      ctx.strokeStyle = '#378ADD'
      ctx.lineWidth = 1.5
      ctx.strokeRect(
        x - 1.5,
        -1.5,
        cellW + 3,
        VEL_COUNT * (cellH + GAP) - GAP + 3,
      )
    }
  }, [outlierScoreVel, summary, presentKeys, selectedVel, selectedMidi, getLayout])

  useEffect(() => { draw() }, [draw])

  // Resize observer — překreslit při změně velikosti kontejneru
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const ro = new ResizeObserver(() => draw())
    ro.observe(container)
    return () => ro.disconnect()
  }, [draw])

  const handleClick = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const layout = getLayout()
    if (!layout) return
    const { cellW, cellH } = layout
    const canvas = canvasRef.current!
    const rect = canvas.getBoundingClientRect()
    const dpr = canvas.width / rect.width
    const cx = (e.clientX - rect.left) * dpr / (window.devicePixelRatio || 1)
    const cy = (e.clientY - rect.top) * dpr / (window.devicePixelRatio || 1)

    if (cx < LABEL_W) return

    const col = Math.floor((cx - LABEL_W) / (cellW + GAP))
    const row = Math.floor(cy / (cellH + GAP))
    const midi = MIDI_MIN + col
    const vel  = VEL_COUNT - 1 - row

    if (midi >= MIDI_MIN && midi <= MIDI_MAX && vel >= 0 && vel <= 7) {
      selectNote(midi)
      setVelocity(vel)
    }
  }, [selectNote, setVelocity, getLayout])

  return (
    <div ref={containerRef} style={{ width: '100%', height: '100%', minHeight: 48 }}>
      <canvas
        ref={canvasRef}
        onClick={handleClick}
        style={{ cursor: 'pointer', display: 'block' }}
      />
    </div>
  )
}
