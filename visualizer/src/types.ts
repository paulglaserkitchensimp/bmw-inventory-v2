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
  platform: 'dealercom' | 'dealerinspire' | string
  type: string | null
  link: string | null
  vinLink: string | null
  resolvedLink: string | null
  carfaxHistory: string | null
  ownershipAssessment: 'dealer_only' | 'likely_private' | 'unknown' | null
  ownershipConfidence: 'high' | 'medium' | 'low' | null
  ownershipReasoning: string | null
}

export interface Filters {
  search: string
  states: string[]
  trims: string[]
  minMiles: string
  maxMiles: string
  minDays: string
  maxDays: string
  minPrice: string
  maxPrice: string
  certified: boolean | null
  ownerCount: string   // '' | '1' | '2+' | 'unknown'
  platform: string     // '' | 'dealercom' | 'dealerinspire'
}

export const DEFAULT_FILTERS: Filters = {
  search: '',
  states: [],
  trims: [],
  minMiles: '',
  maxMiles: '',
  minDays: '',
  maxDays: '',
  minPrice: '',
  maxPrice: '',
  certified: null,
  ownerCount: '',
  platform: '',
}
