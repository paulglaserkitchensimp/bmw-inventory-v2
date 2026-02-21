import { useState, useEffect } from 'react'
import type { Vehicle, Filters } from '../types'

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

export function applyFilters(vehicles: Vehicle[], filters: Filters): Vehicle[] {
  return vehicles.filter(v => {
    if (filters.search) {
      const q = filters.search.toLowerCase()
      const haystack = [v.vin, v.dealerName, v.dealerCity, v.dealerState, v.trim, v.extColor]
        .join(' ').toLowerCase()
      if (!haystack.includes(q)) return false
    }
    if (filters.states.length && v.dealerState && !filters.states.includes(v.dealerState)) return false
    if (filters.trims.length && v.trim && !filters.trims.includes(v.trim)) return false
    if (filters.minMiles && v.odometer !== null && v.odometer < Number(filters.minMiles)) return false
    if (filters.maxMiles && v.odometer !== null && v.odometer > Number(filters.maxMiles)) return false
    if (filters.minDays && v.daysOnLot !== null && v.daysOnLot < Number(filters.minDays)) return false
    if (filters.maxDays && v.daysOnLot !== null && v.daysOnLot > Number(filters.maxDays)) return false
    if (filters.minPrice && v.internetPrice !== null && v.internetPrice < Number(filters.minPrice)) return false
    if (filters.maxPrice && v.internetPrice !== null && v.internetPrice > Number(filters.maxPrice)) return false
    if (filters.certified !== null && v.certified !== filters.certified) return false
    if (filters.platform && v.platform !== filters.platform) return false
    if (filters.ownerCount) {
      if (filters.ownerCount === '1' && v.ownerCount !== 1) return false
      if (filters.ownerCount === '2+' && (v.ownerCount === null || v.ownerCount < 2)) return false
      if (filters.ownerCount === 'unknown' && v.ownerCount !== null) return false
    }
    return true
  })
}
