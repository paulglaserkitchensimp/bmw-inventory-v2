import { useState, useEffect } from 'react'
import type { Vehicle } from '../types'

export type GeoMap = Record<string, [number, number]>  // "City, ST" → [lat, lng]

const CACHE_KEY = 'bmw_dealer_geocache_v1'

function loadCache(): GeoMap {
  try {
    return JSON.parse(localStorage.getItem(CACHE_KEY) ?? '{}')
  } catch {
    return {}
  }
}

function saveCache(map: GeoMap) {
  localStorage.setItem(CACHE_KEY, JSON.stringify(map))
}

async function geocode(city: string, state: string): Promise<[number, number] | null> {
  const params = new URLSearchParams({ city, state })
  try {
    const res = await fetch(`/api/geocode?${params}`)
    if (!res.ok) return null
    const data = await res.json() as { coords: [number, number] | null }
    return data.coords
  } catch {
    return null
  }
}

export function useGeocoder(vehicles: Vehicle[]) {
  const [geoMap, setGeoMap] = useState<GeoMap>(() => loadCache())

  useEffect(() => {
    if (!vehicles.length) return

    const missing = new Map<string, { city: string; state: string }>()
    for (const v of vehicles) {
      if (!v.dealerCity || !v.dealerState) continue
      const key = `${v.dealerCity}, ${v.dealerState}`
      if (!geoMap[key]) missing.set(key, { city: v.dealerCity, state: v.dealerState })
    }
    if (!missing.size) return

    let cancelled = false
    ;(async () => {
      const updates: GeoMap = {}
      for (const [key, { city, state }] of missing) {
        if (cancelled) break
        const coords = await geocode(city, state)
        if (coords) updates[key] = coords
      }
      if (!cancelled && Object.keys(updates).length) {
        setGeoMap(prev => {
          const next = { ...prev, ...updates }
          saveCache(next)
          return next
        })
      }
    })()

    return () => { cancelled = true }
  }, [vehicles])  // eslint-disable-line react-hooks/exhaustive-deps

  return geoMap
}
