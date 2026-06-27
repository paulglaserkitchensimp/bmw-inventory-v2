export interface Vehicle {
  vin: string
  stockNumber: string | null
  year: number | null
  make: string | null
  model: string | null
  trim: string | null
  odometer: number | null
  internetPrice: number | null
  extColor: string | null
  interiorColor: string | null
  certified: boolean
  daysOnLot: number | null
  dateInStock: string | null
  nhtsaUrl: string | null
  carfaxPaywallUrl: string | null
  carfaxUrl: string | null
  carfaxBadge: string | null
  ownerCount: number | null
  dealerName: string | null
  dealerCity: string | null
  dealerState: string | null
  dealerUrl: string | null
  dealerPhone: string | null
  platform: 'dealercom' | 'dealerinspire' | string
  type: string | null
  link: string | null
  vinLink: string | null
  resolvedLink: string | null
  vdpStatus: 'ok' | 'not_found' | 'blocked' | null
  carfaxHistory: string | null
  ownershipAssessment: 'dealer_only' | 'likely_private' | 'unknown' | null
  ownershipConfidence: 'high' | 'medium' | 'low' | null
  ownershipReasoning: string | null
}

export type TriState = 'include' | 'exclude'

export interface Filters {
  search: string
  /**
   * Per-state tri-state toggle:
   *   absent from map  → ignored (no filtering on this state)
   *   'include'        → keep only vehicles in `include` states (when any set)
   *   'exclude'        → drop vehicles in these states
   * 3-click cycle: default → include → exclude → default.
   */
  states: Record<string, TriState>
  /**
   * Per-tag tri-state toggle, same semantics as `states`. Keys are tag names
   * from useAnnotations (`shortlisted` | `contacted` | `purchased` | `pass`)
   * plus the sentinel `untagged` for vehicles with no tag set.
   */
  tags: Record<string, TriState>
  years: number[]
  /** Tri-state include/exclude per model name (same semantics as `states`). */
  models: Record<string, TriState>
  /** Tri-state include/exclude per trim name. */
  trims: Record<string, TriState>
  /** Tri-state filter on normalized CarFax badge keys (`none` = no badge). */
  carfaxBadges: Record<string, TriState>
  minMiles: string
  maxMiles: string
  minDays: string
  maxDays: string
  minPrice: string
  maxPrice: string
  certified: boolean | null
  /**
   * Per-owner-count-bucket tri-state toggle, same semantics as `states` /
   * `tags`. Keys are the bucket ids `'1' | '2+' | 'unknown'`. Lets the user
   * either narrow to specific owner counts (include) or eliminate them
   * (exclude) — and combine both, e.g. "include 1-owner AND exclude unknown".
   */
  ownerCounts: Record<string, TriState>
  /**
   * Restrict to BMW dealerships only (true), non-BMW only (false), or all
   * dealers (null). Detected by case-insensitive `'bmw'` match on
   * `dealerName`, which works uniformly across DDC, DealerInspire,
   * Autotrader, and Cars.com sources.
   */
  bmwDealer: boolean | null
  platform: string     // '' | 'dealercom' | 'dealerinspire'
}

export const DEFAULT_FILTERS: Filters = {
  search: '',
  states: {},
  tags: {},
  years: [],
  models: {},
  trims: {},
  carfaxBadges: {},
  minMiles: '',
  maxMiles: '',
  minDays: '',
  maxDays: '',
  minPrice: '',
  maxPrice: '',
  certified: null,
  ownerCounts: {},
  bmwDealer: null,
  platform: '',
}
