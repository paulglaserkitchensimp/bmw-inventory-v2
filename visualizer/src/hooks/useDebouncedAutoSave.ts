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
  // `pending` is pure derived state (draft ≠ saved), so it is computed during
  // render instead of being pushed into an effect. Only the post-commit
  // "saved" flash needs real state, and it is set from timer callbacks — never
  // synchronously inside an effect body, which cascades renders.
  const [flash, setFlash] = useState(false)

  const draftRef = useRef(draft)
  const onSaveRef = useRef(onSave)
  const pendingRef = useRef(false)

  // Mirror the latest props into refs from an effect rather than during
  // render: writing refs in the render body is unsafe when a render is thrown
  // away. Both refs are only read after commit (debounce timer, unmount
  // cleanup), so an effect is early enough.
  useEffect(() => {
    draftRef.current = draft
    onSaveRef.current = onSave
  })

  const dirty = !Object.is(draft, savedValue)

  useEffect(() => {
    if (!dirty) {
      pendingRef.current = false
      return
    }
    pendingRef.current = true
    const timer = setTimeout(() => {
      onSaveRef.current(draftRef.current)
      pendingRef.current = false
      setFlash(true)
    }, delayMs)
    return () => clearTimeout(timer)
  }, [draft, dirty, delayMs])

  // Auto-clear the `saved` flash ~1.5s after it appears.
  useEffect(() => {
    if (!flash) return
    const t = setTimeout(() => setFlash(false), 1500)
    return () => clearTimeout(t)
  }, [flash])

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

  if (dirty) return 'pending'
  return flash ? 'saved' : 'idle'
}
