import { useState, useEffect } from 'react'
import type { Filters } from '../types'
import { DEFAULT_FILTERS } from '../types'

/**
 * Persisted-filters hook: mirrors React's `useState<Filters>` API but
 * round-trips through `localStorage` so the user's filter selections
 * survive a page reload (or browser restart).
 *
 * Storage strategy:
 *   - Keyed by a versioned name so future breaking schema changes can be
 *     handled cleanly by bumping the version suffix.
 *   - On load we shallow-merge the stored object into `DEFAULT_FILTERS`,
 *     which makes adding a new filter field (e.g. `bmwDealer`,
 *     `ownerCounts`) forward-compatible — old persisted state is augmented
 *     with the new field's default rather than throwing or coming back
 *     with a missing property.
 *   - Writes are wrapped in try/catch so private-browsing modes (where
 *     localStorage is read-only or unavailable) degrade silently to
 *     in-memory state instead of crashing the app.
 */
const STORAGE_KEY = 'bmw-visualizer/filters/v1'

function loadFilters(): Filters {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_FILTERS
    const stored = JSON.parse(raw) as Partial<Filters>
    return { ...DEFAULT_FILTERS, ...stored }
  } catch {
    return DEFAULT_FILTERS
  }
}

export function usePersistedFilters() {
  const [filters, setFilters] = useState<Filters>(loadFilters)

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(filters))
    } catch {
      // localStorage unavailable (private mode, quota exceeded, etc.).
      // Filters still work in-session, just won't survive a reload.
    }
  }, [filters])

  return [filters, setFilters] as const
}
