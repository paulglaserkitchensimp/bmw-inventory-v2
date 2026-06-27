import { useState, useEffect } from 'react'
import type { Filters, TriState } from '../types'
import { DEFAULT_FILTERS } from '../types'

/**
 * Persisted-filters hook: mirrors React's `useState<Filters>` API but
 * round-trips through `localStorage` so the user's filter selections
 * survive a page reload (or browser restart).
 */
const STORAGE_KEY = 'bmw-visualizer/filters/v2'
const LEGACY_KEY = 'bmw-visualizer/filters/v1'

function arrayToTriState(values: string[]): Record<string, TriState> {
  return Object.fromEntries(values.map(v => [v, 'include' as TriState]))
}

function migrateStored(stored: Partial<Filters> & Record<string, unknown>): Filters {
  const f: Filters = { ...DEFAULT_FILTERS, ...stored }

  // v1 stored models/trims as string[] include-only lists.
  if (Array.isArray(stored.models)) {
    f.models = arrayToTriState(stored.models as string[])
  }
  if (Array.isArray(stored.trims)) {
    f.trims = arrayToTriState(stored.trims as string[])
  }
  if (!stored.carfaxBadges || Array.isArray(stored.carfaxBadges)) {
    f.carfaxBadges = {}
  }

  return f
}

function loadFilters(): Filters {
  try {
    const raw = localStorage.getItem(STORAGE_KEY) ?? localStorage.getItem(LEGACY_KEY)
    if (!raw) return DEFAULT_FILTERS
    const stored = JSON.parse(raw) as Partial<Filters> & Record<string, unknown>
    return migrateStored(stored)
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
    }
  }, [filters])

  return [filters, setFilters] as const
}
