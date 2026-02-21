import { useState, useEffect, useCallback, useRef } from 'react'

export type Tag = 'shortlisted' | 'contacted' | 'purchased' | 'pass'

export interface Annotation {
  tag: Tag | null
  comment: string
}

export type AnnotationMap = Record<string, Annotation>  // vin → annotation

const TAG_CYCLE: (Tag | null)[] = [null, 'shortlisted', 'contacted', 'purchased', 'pass']

export const TAG_META: Record<Tag, { label: string; color: string; bg: string }> = {
  shortlisted: { label: 'Shortlisted', color: 'text-yellow-800', bg: 'bg-yellow-100 border-yellow-300' },
  contacted:   { label: 'Contacted',   color: 'text-blue-800',   bg: 'bg-blue-100 border-blue-300'   },
  purchased:   { label: 'Purchased',   color: 'text-green-800',  bg: 'bg-green-100 border-green-300'  },
  pass:        { label: 'Pass',        color: 'text-gray-500',   bg: 'bg-gray-100 border-gray-300'    },
}

export function nextTag(current: Tag | null): Tag | null {
  const idx = TAG_CYCLE.indexOf(current)
  return TAG_CYCLE[(idx + 1) % TAG_CYCLE.length]
}

export function useAnnotations() {
  const [annotations, setAnnotations] = useState<AnnotationMap>({})
  const [loaded, setLoaded] = useState(false)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Load on mount
  useEffect(() => {
    fetch('/api/annotations')
      .then(r => r.json())
      .then((data: AnnotationMap) => {
        setAnnotations(data)
        setLoaded(true)
      })
      .catch(() => setLoaded(true))
  }, [])

  // Debounced save to disk
  const persist = useCallback((next: AnnotationMap) => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      fetch('/api/annotations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(next),
      }).catch(() => {})
    }, 400)
  }, [])

  const setTag = useCallback((vin: string, tag: Tag | null) => {
    setAnnotations(prev => {
      const current = prev[vin] ?? { tag: null, comment: '' }
      const next = { ...prev, [vin]: { ...current, tag } }
      // Remove empty entries to keep the file clean
      if (!next[vin].tag && !next[vin].comment) delete next[vin]
      persist(next)
      return next
    })
  }, [persist])

  const setComment = useCallback((vin: string, comment: string) => {
    setAnnotations(prev => {
      const current = prev[vin] ?? { tag: null, comment: '' }
      const next = { ...prev, [vin]: { ...current, comment } }
      if (!next[vin].tag && !next[vin].comment) delete next[vin]
      persist(next)
      return next
    })
  }, [persist])

  const cycleTag = useCallback((vin: string) => {
    setAnnotations(prev => {
      const current = prev[vin]?.tag ?? null
      const tag = nextTag(current)
      const entry = prev[vin] ?? { tag: null, comment: '' }
      const next = { ...prev, [vin]: { ...entry, tag } }
      if (!next[vin].tag && !next[vin].comment) delete next[vin]
      persist(next)
      return next
    })
  }, [persist])

  return { annotations, loaded, setTag, setComment, cycleTag }
}
