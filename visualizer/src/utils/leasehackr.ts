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
