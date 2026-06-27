import { useEffect, useRef, useState } from 'react'

/**
 * `idle`   → draft matches the saved value, nothing to do.
 * `pending` → draft differs, debounce timer is running (or save in flight).
 * `saved`  → flashed briefly right after a successful commit.
 */
export type AutoSaveStatus = 'idle' | 'pending' | 'saved'

/**
 * Debounced auto-save with an unmount flush.
 *
 * Whenever `draft` differs from `savedValue`, schedules `onSave(draft)` after
 * `delayMs`; each subsequent change resets the timer. On unmount, if there's
 * a pending change that hasn't flushed yet, saves it immediately so edits
 * aren't lost when the host component remounts (e.g. user selects a different
 * record).
 *
 * Returns a status string for rendering feedback: `pending` while debouncing,
 * `saved` briefly (~1.5s) after each commit, `idle` otherwise.
 *
 * `onSave` is captured via a ref so the debounce timer isn't reset just
 * because the caller passes a fresh callback identity each render.
 */
export function useDebouncedAutoSave<T>(
  draft: T,
  savedValue: T,
  onSave: (value: T) => void,
  delayMs: number = 300,
): AutoSaveStatus {
  const [status, setStatus] = useState<AutoSaveStatus>('idle')

  const draftRef = useRef(draft)
  const onSaveRef = useRef(onSave)
  const pendingRef = useRef(false)
  draftRef.current = draft
  onSaveRef.current = onSave

  useEffect(() => {
    if (Object.is(draft, savedValue)) {
      pendingRef.current = false
      // Don't stomp the post-save `saved` flash; let its own timer clear it.
      setStatus(prev => (prev === 'saved' ? prev : 'idle'))
      return
    }
    pendingRef.current = true
    setStatus('pending')
    const timer = setTimeout(() => {
      onSaveRef.current(draft)
      pendingRef.current = false
      setStatus('saved')
    }, delayMs)
    return () => clearTimeout(timer)
  }, [draft, savedValue, delayMs])

  // Auto-clear the `saved` flash ~1.5s after it appears.
  useEffect(() => {
    if (status !== 'saved') return
    const t = setTimeout(() => {
      setStatus(prev => (prev === 'saved' ? 'idle' : prev))
    }, 1500)
    return () => clearTimeout(t)
  }, [status])

  // Flush any pending save on unmount. Empty deps = runs only on mount/unmount,
  // so the cleanup fires exactly once when the host component goes away.
  useEffect(() => {
    return () => {
      if (pendingRef.current) {
        onSaveRef.current(draftRef.current)
        pendingRef.current = false
      }
    }
  }, [])

  return status
}
