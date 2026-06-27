import { useState, useEffect } from 'react'
import type { Vehicle, Filters } from '../types'
import type { AnnotationMap } from './useAnnotations'

/** Ordered list of owner-count buckets the filter panel exposes. */
export const OWNER_COUNT_BUCKETS = ['1', '2+', 'unknown'] as const
export type OwnerBucket = (typeof OWNER_COUNT_BUCKETS)[number]

/** Map a vehicle's raw ownerCount → one of the filter buckets. */
export function ownerBucket(count: number | null): OwnerBucket {
  if (count === null) return 'unknown'
  if (count <= 1) return '1'
  return '2+'
}

export function useVehicles() {
  const [vehicles, setVehicles] = useState<Vehicle[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/data/results.json')
      .then(r => r.json())
      .then((data: Vehicle[]) => {
        setVehicles(data)
        setLoading(false)
      })
      .catch(e => {
        setError(String(e))
        setLoading(false)
      })
  }, [])

  return { vehicles, loading, error }
}

export function applyFilters(
  vehicles: Vehicle[],
  filters: Filters,
  annotations: AnnotationMap = {},
): Vehicle[] {
  const includeStates = Object.entries(filters.states)
    .filter(([, s]) => s === 'include').map(([k]) => k)
  const excludeStates = Object.entries(filters.states)
    .filter(([, s]) => s === 'exclude').map(([k]) => k)
  const includeTags = Object.entries(filters.tags)
    .filter(([, s]) => s === 'include').map(([k]) => k)
  const excludeTags = Object.entries(filters.tags)
    .filter(([, s]) => s === 'exclude').map(([k]) => k)
  const includeOwnerBuckets = Object.entries(filters.ownerCounts)
    .filter(([, s]) => s === 'include').map(([k]) => k)
  const excludeOwnerBuckets = Object.entries(filters.ownerCounts)
    .filter(([, s]) => s === 'exclude').map(([k]) => k)

  return vehicles.filter(v => {
    if (filters.search) {
      const q = filters.search.toLowerCase()
      const haystack = [v.vin, v.dealerName, v.dealerCity, v.dealerState, v.trim, v.extColor]
        .join(' ').toLowerCase()
      if (!haystack.includes(q)) return false
    }
    if (includeStates.length && v.dealerState && !includeStates.includes(v.dealerState)) return false
    if (excludeStates.length && v.dealerState && excludeStates.includes(v.dealerState)) return false
    if (filters.years.length && v.year !== null && !filters.years.includes(v.year)) return false
    if (filters.models.length && v.model && !filters.models.includes(v.model)) return false
    if (filters.trims.length && v.trim && !filters.trims.includes(v.trim)) return false
    if (filters.minMiles && v.odometer !== null && v.odometer < Number(filters.minMiles)) return false
    if (filters.maxMiles && v.odometer !== null && v.odometer > Number(filters.maxMiles)) return false
    if (filters.minDays && v.daysOnLot !== null && v.daysOnLot < Number(filters.minDays)) return false
    if (filters.maxDays && v.daysOnLot !== null && v.daysOnLot > Number(filters.maxDays)) return false
    if (filters.minPrice && v.internetPrice !== null && v.internetPrice < Number(filters.minPrice)) return false
    if (filters.maxPrice && v.internetPrice !== null && v.internetPrice > Number(filters.maxPrice)) return false
    if (filters.certified !== null && v.certified !== filters.certified) return false
    if (filters.platform && v.platform !== filters.platform) return false
    if (filters.bmwDealer !== null) {
      const isBmw = (v.dealerName ?? '').toLowerCase().includes('bmw')
      if (isBmw !== filters.bmwDealer) return false
    }
    if (includeOwnerBuckets.length || excludeOwnerBuckets.length) {
      const bucket = ownerBucket(v.ownerCount)
      if (includeOwnerBuckets.length && !includeOwnerBuckets.includes(bucket)) return false
      if (excludeOwnerBuckets.length && excludeOwnerBuckets.includes(bucket)) return false
    }
    if (includeTags.length || excludeTags.length) {
      const tag = annotations[v.vin]?.tag ?? 'untagged'
      if (includeTags.length && !includeTags.includes(tag)) return false
      if (excludeTags.length && excludeTags.includes(tag)) return false
    }
    return true
  })
}
