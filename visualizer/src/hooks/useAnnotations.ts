import { useState, useEffect, useCallback, useRef } from 'react'

export type Tag =
  | 'interesting'
  | 'shortlisted'
  | 'contacted'
  | 'negotiating'
  | 'purchased'
  | 'negotiated_pass'
  | 'nonleasable_pass'
  | 'pass'

export interface Annotation {
  tag: Tag | null
  comment: string
  /** Full Leasehackr calculator URL — parsed on render via utils/leasehackr.ts. */
  leasehackrUrl?: string
  /**
   * User-corrected listing URL. Overrides the crawler-derived
   * `resolvedLink`/`link` for the "View Listing" button when the scraped URL
   * is stale or wrong.
   */
  listingUrl?: string
}

export type AnnotationMap = Record<string, Annotation>  // vin → annotation

/**
 * Workflow-ordered cycle: none → interesting → shortlisted → contacted →
 * negotiating → purchased → negotiated-pass → nonleasable-pass → pass → none. Keeps the table's
 * pass-to-bottom behaviour and provides natural funnel progression when
 * clicking the pill.
 */
const TAG_CYCLE: (Tag | null)[] = [
  null,
  'interesting',
  'shortlisted',
  'contacted',
  'negotiating',
  'purchased',
  'negotiated_pass',
  'nonleasable_pass',
  'pass',
]

export const TAG_META: Record<Tag, { label: string; color: string; bg: string }> = {
  interesting:     { label: 'Interesting',       color: 'text-purple-800', bg: 'bg-purple-100 border-purple-300' },
  shortlisted:     { label: 'Shortlisted',       color: 'text-yellow-800', bg: 'bg-yellow-100 border-yellow-300' },
  contacted:       { label: 'Contacted',         color: 'text-blue-800',   bg: 'bg-blue-100 border-blue-300'     },
  negotiating:     { label: 'Negotiating',       color: 'text-orange-800', bg: 'bg-orange-100 border-orange-300' },
  purchased:       { label: 'Purchased',         color: 'text-green-800',  bg: 'bg-green-100 border-green-300'   },
  negotiated_pass:  { label: 'Negotiated - Pass', color: 'text-rose-800',  bg: 'bg-rose-100 border-rose-300'     },
  nonleasable_pass: { label: 'Pass - Nonleasable', color: 'text-stone-700', bg: 'bg-stone-100 border-stone-300'  },
  pass:             { label: 'Pass',              color: 'text-gray-500',  bg: 'bg-gray-100 border-gray-300'     },
}

export function nextTag(current: Tag | null): Tag | null {
  const idx = TAG_CYCLE.indexOf(current)
  return TAG_CYCLE[(idx + 1) % TAG_CYCLE.length]
}

/** How often we refetch the full map to pick up other browsers' edits. */
const POLL_INTERVAL_MS = 10_000

/** Per-VIN debounce before a write lands on disk. */
const SAVE_DEBOUNCE_MS = 400

/**
 * Centralised store for user-authored annotations (tag, note, Leasehackr URL).
 *
 * Persistence model:
 *   - Initial load: GET /api/annotations (whole map)
 *   - Edits: POST /api/annotations/:vin with just that VIN's entry
 *   - Sync:  poll the whole map every POLL_INTERVAL_MS and merge in any
 *            entries we don't have a local pending write for
 *
 * Per-VIN writes replaced an earlier whole-map POST that caused lost updates
 * when multiple browsers had the app open — one browser's save would blow
 * away another browser's unrelated edits.
 */
export function useAnnotations() {
  const [annotations, setAnnotations] = useState<AnnotationMap>({})
  const [loaded, setLoaded] = useState(false)
  // One debounce timer per VIN so rapid edits on the SAME vin coalesce, but
  // back-to-back edits on DIFFERENT vins don't cancel each other's save.
  const saveTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())
  // VINs with a scheduled or in-flight write. Poll-driven refreshes skip
  // these so we don't stomp the user's unsaved local state with the pre-write
  // server value.
  const pendingVins = useRef<Set<string>>(new Set())

  useEffect(() => {
    let cancelled = false
    fetch('/api/annotations')
      .then(r => r.json())
      .then((data: AnnotationMap) => {
        if (cancelled) return
        setAnnotations(data)
        setLoaded(true)
      })
      .catch(() => { if (!cancelled) setLoaded(true) })
    return () => { cancelled = true }
  }, [])

  // Poll for edits made in other browsers and merge them in.
  useEffect(() => {
    let cancelled = false
    const refresh = async () => {
      try {
        const r = await fetch('/api/annotations')
        if (!r.ok) return
        const server = await r.json() as AnnotationMap
        if (cancelled) return
        setAnnotations(prev => mergeRemote(prev, server, pendingVins.current))
      } catch {/* network blip; try again next tick */}
    }
    const t = setInterval(refresh, POLL_INTERVAL_MS)
    return () => { cancelled = true; clearInterval(t) }
  }, [])

  const persistVin = useCallback((vin: string, entry: Annotation | null) => {
    const existing = saveTimers.current.get(vin)
    if (existing) clearTimeout(existing)
    pendingVins.current.add(vin)

    const timer = setTimeout(async () => {
      saveTimers.current.delete(vin)
      try {
        await fetch(`/api/annotations/${encodeURIComponent(vin)}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(entry),
        })
      } catch {/* swallow; next edit will retry */}
      // Only clear the pending flag if no newer edit got scheduled in the
      // meantime (that newer edit will clear it when its own save completes).
      if (!saveTimers.current.has(vin)) pendingVins.current.delete(vin)
    }, SAVE_DEBOUNCE_MS)
    saveTimers.current.set(vin, timer)
  }, [])

  const commitVin = useCallback((vin: string, entry: Annotation) => {
    const toPersist = isEmptyEntry(entry) ? null : entry
    persistVin(vin, toPersist)
  }, [persistVin])

  const setTag = useCallback((vin: string, tag: Tag | null) => {
    setAnnotations(prev => {
      const current = prev[vin] ?? { tag: null, comment: '' }
      const entry = { ...current, tag }
      const next = { ...prev, [vin]: entry }
      if (isEmptyEntry(entry)) delete next[vin]
      commitVin(vin, entry)
      return next
    })
  }, [commitVin])

  const setComment = useCallback((vin: string, comment: string) => {
    setAnnotations(prev => {
      const current = prev[vin] ?? { tag: null, comment: '' }
      const entry = { ...current, comment }
      const next = { ...prev, [vin]: entry }
      if (isEmptyEntry(entry)) delete next[vin]
      commitVin(vin, entry)
      return next
    })
  }, [commitVin])

  const setLeasehackrUrl = useCallback((vin: string, leasehackrUrl: string) => {
    setAnnotations(prev => {
      const current = prev[vin] ?? { tag: null, comment: '' }
      const entry = { ...current, leasehackrUrl }
      const next = { ...prev, [vin]: entry }
      if (isEmptyEntry(entry)) delete next[vin]
      commitVin(vin, entry)
      return next
    })
  }, [commitVin])

  const setListingUrl = useCallback((vin: string, listingUrl: string) => {
    setAnnotations(prev => {
      const current = prev[vin] ?? { tag: null, comment: '' }
      const entry = { ...current, listingUrl }
      const next = { ...prev, [vin]: entry }
      if (isEmptyEntry(entry)) delete next[vin]
      commitVin(vin, entry)
      return next
    })
  }, [commitVin])

  const cycleTag = useCallback((vin: string) => {
    setAnnotations(prev => {
      const current = prev[vin] ?? { tag: null, comment: '' }
      const entry = { ...current, tag: nextTag(current.tag ?? null) }
      const next = { ...prev, [vin]: entry }
      if (isEmptyEntry(entry)) delete next[vin]
      commitVin(vin, entry)
      return next
    })
  }, [commitVin])

  return { annotations, loaded, setTag, setComment, setLeasehackrUrl, setListingUrl, cycleTag }
}

/** Empty = no tag, no comment, no leasehackr URL, no listing URL; safe to drop the entry. */
function isEmptyEntry(entry: Annotation | undefined | null): boolean {
  if (!entry) return true
  return !entry.tag && !entry.comment && !entry.leasehackrUrl && !entry.listingUrl
}

/**
 * Merge a freshly-fetched server map onto local state. VINs with pending
 * local writes are preserved as-is so we don't flash the user's unsaved draft
 * back to the stale server value; every other VIN is taken from the server
 * (including deletions made by other clients).
 */
function mergeRemote(
  local: AnnotationMap,
  remote: AnnotationMap,
  pending: Set<string>,
): AnnotationMap {
  const merged: AnnotationMap = { ...remote }
  for (const vin of pending) {
    if (local[vin]) merged[vin] = local[vin]
    else delete merged[vin]
  }
  return merged
}
