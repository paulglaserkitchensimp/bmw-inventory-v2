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
  /**
   * Option packages spotted in the VDP body text by the crawler's
   * `extract_packages()` (e.g. `m_sport`, `m_sport_pro`, `premium`).
   * Empty when the page was fetched and nothing matched; absent when no VDP
   * was ever fetched.
   */
  packageSignals?: string[]
  /**
   * Tri-state M Sport verdict:
   *   true  – "M Sport Package" (or option 337 / M Sport Pro) found on the VDP
   *   false – VDP fetched, no marker found
   *   null/undefined – never fetched or blocked; unknown, verify by hand
   */
  mSport?: boolean | null
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
   * Max straight-line miles from ORIGIN_COORDS. Empty string = no limit.
   * Vehicles whose dealer city hasn't been geocoded yet have an unknown
   * distance and are always kept, so the table doesn't empty out while the
   * geocoder is still catching up.
   */
  maxDistance: string
  /** Tri-state include/exclude per normalized exterior colour bucket. */
  colors: Record<string, TriState>
  /**
   * M Sport package filter: true = only cars where the crawler found an
   * M Sport marker on the VDP, false = only cars where it explicitly didn't,
   * null = no filter. Cars with an unknown verdict (no VDP fetched) are kept
   * under `true` so you can still see and check them manually.
   */
  mSport: boolean | null
  /**
   * Lease-eligibility gate: BMW Financial can only lease a car titled "new"
   * in the dealer's own inventory system — a service loaner that hasn't been
   * retailed yet still counts as new regardless of odometer, but a car that
   * was retailed once and bought back is "used" no matter how few miles it
   * has. true = only `type === 'new'`, false = only not-new (used/CPO), null
   * = no filter. Defaults to true; toggle to null/false to browse the
   * used/CPO market (e.g. after an --any-condition crawler sweep).
   */
  isNew: boolean | null
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
  minDays: '',
  maxDays: '',
  minPrice: '',
  maxPrice: '',
  certified: null,
  // Pre-set to the 2026 330i hunt: 750-mile radius around 47119, the two
  // colours that are non-starters excluded, a 0-5,000 mile window (new-loaner
  // range), M Sport required, and condition locked to "new" (lease
  // eligibility). Every one of these is a single click to relax in the filter
  // panel — none of them require a re-crawl. "Reset all" restores exactly this.
  maxDistance: '750',
  colors: { white: 'exclude', red: 'exclude' },
  minMiles: '0',
  maxMiles: '5000',
  mSport: true,
  isNew: true,
  ownerCounts: {},
  bmwDealer: null,
  platform: '',
}
