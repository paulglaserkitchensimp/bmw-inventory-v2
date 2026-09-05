import { useState, useEffect } from 'react'
import type { Vehicle, Filters } from '../types'
import type { AnnotationMap } from './useAnnotations'
import { badgeFilterKey } from '../utils/carfaxBadge'
import { colorBucket } from '../utils/color'
import { vehicleDistance } from '../utils/distance'
import type { GeoMap } from './useGeocoder'

/** Ordered list of owner-count buckets the filter panel exposes. */
export const OWNER_COUNT_BUCKETS = ['1', '2+', 'unknown'] as const
export type OwnerBucket = (typeof OWNER_COUNT_BUCKETS)[number]

/** Map a vehicle's raw ownerCount → one of the filter buckets. */
export function ownerBucket(count: number | null | undefined): OwnerBucket {
  // Most crawler records omit ownerCount entirely (undefined), not null.
  // Treat both as unknown — otherwise undefined falls through to '2+' because
  // `undefined <= 1` is false in JS.
  if (count == null) return 'unknown'
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
  geoMap: GeoMap = {},
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
  const includeModels = Object.entries(filters.models)
    .filter(([, s]) => s === 'include').map(([k]) => k)
  const excludeModels = Object.entries(filters.models)
    .filter(([, s]) => s === 'exclude').map(([k]) => k)
  const includeTrims = Object.entries(filters.trims)
    .filter(([, s]) => s === 'include').map(([k]) => k)
  const excludeTrims = Object.entries(filters.trims)
    .filter(([, s]) => s === 'exclude').map(([k]) => k)
  const includeColors = Object.entries(filters.colors)
    .filter(([, s]) => s === 'include').map(([k]) => k)
  const excludeColors = Object.entries(filters.colors)
    .filter(([, s]) => s === 'exclude').map(([k]) => k)
  const maxDistance = filters.maxDistance ? Number(filters.maxDistance) : null
  const includeBadges = Object.entries(filters.carfaxBadges)
    .filter(([, s]) => s === 'include').map(([k]) => k)
  const excludeBadges = Object.entries(filters.carfaxBadges)
    .filter(([, s]) => s === 'exclude').map(([k]) => k)

  return vehicles.filter(v => {
    if (filters.search) {
      const q = filters.search.toLowerCase()
      const haystack = [v.vin, v.dealerName, v.dealerCity, v.dealerState, v.trim, v.extColor,
                        annotations[v.vin]?.comment]
        .join(' ').toLowerCase()
      if (!haystack.includes(q)) return false
    }
    if (includeStates.length && v.dealerState && !includeStates.includes(v.dealerState)) return false
    if (excludeStates.length && v.dealerState && excludeStates.includes(v.dealerState)) return false
    if (filters.years.length && v.year !== null && !filters.years.includes(v.year)) return false
    if (includeModels.length && v.model && !includeModels.includes(v.model)) return false
    if (excludeModels.length && v.model && excludeModels.includes(v.model)) return false
    if (includeTrims.length && v.trim && !includeTrims.includes(v.trim)) return false
    if (excludeTrims.length && v.trim && excludeTrims.includes(v.trim)) return false
    if (includeBadges.length || excludeBadges.length) {
      const key = badgeFilterKey(v.carfaxBadge)
      if (includeBadges.length && !includeBadges.includes(key)) return false
      if (excludeBadges.length && excludeBadges.includes(key)) return false
    }
    if (filters.minMiles && v.odometer !== null && v.odometer < Number(filters.minMiles)) return false
    if (filters.maxMiles && v.odometer !== null && v.odometer > Number(filters.maxMiles)) return false
    if (filters.minDays && v.daysOnLot !== null && v.daysOnLot < Number(filters.minDays)) return false
    if (filters.maxDays && v.daysOnLot !== null && v.daysOnLot > Number(filters.maxDays)) return false
    if (filters.minPrice && v.internetPrice !== null && v.internetPrice < Number(filters.minPrice)) return false
    if (filters.maxPrice && v.internetPrice !== null && v.internetPrice > Number(filters.maxPrice)) return false
    if (includeColors.length || excludeColors.length) {
      const bucket = colorBucket(v.extColor)
      if (includeColors.length && !includeColors.includes(bucket)) return false
      if (excludeColors.length && excludeColors.includes(bucket)) return false
    }
    if (maxDistance !== null && Number.isFinite(maxDistance)) {
      // null distance = dealer city not geocoded yet; keep it rather than
      // hiding a car we simply haven't located.
      const d = vehicleDistance(v, geoMap)
      if (d !== null && d > maxDistance) return false
    }
    if (filters.mSport !== null) {
      // `undefined`/`null` mSport means "never checked". Under the
      // M-Sport-only filter those stay visible (they're leads to verify);
      // under the explicit "no M Sport" filter they don't.
      if (filters.mSport === true && v.mSport === false) return false
      if (filters.mSport === false && v.mSport !== false) return false
    }
    if (filters.isNew !== null) {
      // v.type is always populated by every crawler source ('new' | 'used' |
      // 'cpo') — unlike distance/mSport there's no "unknown" case to protect.
      const isNew = (v.type ?? '').toLowerCase() === 'new'
      if (isNew !== filters.isNew) return false
    }
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
