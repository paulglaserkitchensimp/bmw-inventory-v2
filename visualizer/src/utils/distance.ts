import type { Vehicle } from '../types'
import type { GeoMap } from '../hooks/useGeocoder'

/**
 * Proximity-sort / radius-filter origin.
 *
 * 47119 = Floyds Knobs, IN (Louisville metro). Change these three constants to
 * re-home the whole app; nothing else hardcodes a location. The crawler has its
 * own copy of the ZIP in `crawler/search_330i.sh` (ZIP / RADIUS) — keep the two
 * in sync or the table will show cars the sweep never looked for.
 */
export const ORIGIN_COORDS: [number, number] = [38.3345, -85.8983]
export const ORIGIN_LABEL = '47119'

/** Default shopping radius in miles, used as the initial `maxDistance` filter. */
export const DEFAULT_RADIUS_MILES = 750

export function haversineMiles(a: [number, number], b: [number, number]): number {
  const R = 3958.8
  const toRad = (x: number) => (x * Math.PI) / 180
  const dLat = toRad(b[0] - a[0])
  const dLon = toRad(b[1] - a[1])
  const lat1 = toRad(a[0])
  const lat2 = toRad(b[0])
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(h))
}

/**
 * Straight-line miles from the origin to a vehicle's dealer city.
 *
 * `null` means "unknown", not "far": the dealer city hasn't been geocoded yet
 * (or the record has no city/state at all). Callers must treat null as
 * un-filterable rather than dropping the row, otherwise the table empties out
 * while Nominatim is still working through the geocode queue.
 */
export function vehicleDistance(v: Vehicle, geoMap: GeoMap): number | null {
  if (!v.dealerCity || !v.dealerState) return null
  const coords = geoMap[`${v.dealerCity}, ${v.dealerState}`]
  if (!coords) return null
  return haversineMiles(ORIGIN_COORDS, coords)
}
