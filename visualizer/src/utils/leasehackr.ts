/**
 * Parses Leasehackr calculator URLs (https://calculator.leasehackr.com?...)
 * into a minimal deal summary. All values may be null if the URL lacks the
 * corresponding query param or the URL doesn't parse.
 */

export interface LeasehackrDeal {
  msrp: number | null
  salesPrice: number | null
  mf: number | null
  months: number | null
  miles: number | null   // annual mileage allowance (7500, 10000, 12000, 15000…)
}

function parseNumParam(params: URLSearchParams, key: string): number | null {
  const raw = params.get(key)
  if (raw === null || raw === '') return null
  const n = Number(raw)
  return Number.isFinite(n) ? n : null
}

export function parseLeasehackrUrl(url: string | undefined | null): LeasehackrDeal | null {
  if (!url) return null
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return null
  }
  if (!parsed.hostname.toLowerCase().includes('leasehackr')) return null
  const q = parsed.searchParams
  return {
    msrp: parseNumParam(q, 'msrp'),
    salesPrice: parseNumParam(q, 'sales_price'),
    mf: parseNumParam(q, 'mf'),
    months: parseNumParam(q, 'months'),
    miles: parseNumParam(q, 'miles'),
  }
}

/**
 * Discount off MSRP as a percentage, i.e. (1 - salesPrice / msrp) * 100.
 * For a $92,500 sale on a $121,005 MSRP this returns ~23.6.
 */
export function discountPercent(d: LeasehackrDeal): number | null {
  if (d.msrp === null || d.salesPrice === null || d.msrp === 0) return null
  return (1 - d.salesPrice / d.msrp) * 100
}

/**
 * The two lease structures actually under consideration for this hunt (see
 * docs/330I_DEAL_FINDER.md). Kept as a named list rather than scattered
 * literals so a change of term/mileage happens in one place.
 */
export const LEASE_TERMS_MONTHS = [36, 39] as const
export const LEASE_ANNUAL_MILES = 12_000

/**
 * Build a calculator.leasehackr.com URL prefilled with the listing's selling
 * price and the term/mileage above, so the negotiation-grade calculation
 * (money factor, residual, taxes, fees — all of which change monthly and
 * aren't available from any crawler source) happens on Leasehackr's own
 * calculator rather than being guessed here. The user fills in MSRP, MF, and
 * residual from the dealer worksheet or BMU rate sheet and pastes the
 * resulting URL back into the annotation field (parsed by parseLeasehackrUrl
 * above) to get it to persist in the table.
 *
 * Deliberately does NOT estimate a monthly payment from assumed MF/residual:
 * BMW's money factor and residual percentages change monthly and are
 * program/region-specific, and getting them wrong would produce a number
 * that looks authoritative but isn't. Better to hand off to the real
 * calculator with the two knobs we do know (price, term/mileage) already set.
 */
export function buildLeasehackrPrefillUrl(
  salesPrice: number | null | undefined,
  months: number,
  miles: number = LEASE_ANNUAL_MILES,
): string {
  const params = new URLSearchParams()
  if (salesPrice != null) params.set('sales_price', String(Math.round(salesPrice)))
  params.set('months', String(months))
  params.set('miles', String(miles))
  return `https://calculator.leasehackr.com?${params.toString()}`
}
