/** Human labels + styling for CarFax value-badge slugs and Autotrader VHR flags. */

const BADGE_COLORS: Record<string, string> = {
  '1own_great': 'bg-green-100 text-green-800',
  '1own_good': 'bg-green-100 text-green-800',
  '1own': 'bg-blue-100 text-blue-800',
  '1own_fair': 'bg-yellow-100 text-yellow-800',
  '1own_fair_black': 'bg-yellow-100 text-yellow-800',
  noaccident: 'bg-green-100 text-green-800',
  great: 'bg-green-100 text-green-800',
  fair: 'bg-yellow-100 text-yellow-800',
  showme: 'bg-gray-100 text-gray-700',
}

/** Normalize a raw slug / VHR key for filter grouping. */
export function badgeFilterKey(slug: string | null | undefined): string {
  if (!slug) return 'none'
  if (slug.startsWith('vhr:')) return slug
  let s = slug.toLowerCase().replace(/^valuebadge_/, '').replace(/_black$/, '')
  return s || slug
}

function humanizeVhrFlag(flag: string): string {
  return flag
    .replace(/_/g, ' ')
    .toLowerCase()
    .replace(/\b\w/g, c => c.toUpperCase())
}

/** Compact label for table cells (especially long Autotrader VHR strings). */
export function badgeLabelShort(slug: string | null | undefined): string {
  if (!slug) return '—'
  if (slug.startsWith('vhr:')) {
    const flags = slug.slice(4).split(',')
    const parts: string[] = []
    if (flags.some(f => f.includes('ONE_OWNER') || f.includes('1_OWNER'))) parts.push('1-Owner')
    if (flags.some(f => f.includes('NO_ACCIDENT'))) parts.push('No Accidents')
    if (flags.some(f => f.includes('FREE_REPORT'))) parts.push('Free Report')
    if (flags.some(f => f.includes('NO_SALVAGE'))) parts.push('No Salvage')
    return parts.join(' · ') || 'Vehicle History'
  }
  return badgeLabel(slug)
}

/** Human-readable labels for CarFax badge slugs and Autotrader VHR flags. */
export function badgeLabel(slug: string | null | undefined): string {
  if (!slug) return '—'
  if (slug.startsWith('vhr:')) {
    return slug
      .slice(4)
      .split(',')
      .map(humanizeVhrFlag)
      .join(' · ')
  }

  const s = slug.toLowerCase().replace(/^valuebadge_/, '').replace(/_black$/, '')
  if (s.startsWith('1own_great')) return '1-Owner · Great Value'
  if (s.startsWith('1own_good')) return '1-Owner · Good Value'
  if (s.startsWith('1own_fair')) return '1-Owner · Fair Value'
  if (s === '1own') return '1-Owner'
  if (s.startsWith('1own')) return '1-Owner'
  if (s === 'showme') return 'Show Me the CarFax'
  if (s === 'noaccident') return 'No Accidents Reported'
  if (s === 'great') return 'Great Value'
  if (s === 'fair') return 'Fair Value'
  return slug.replace(/_/g, ' ')
}

export function badgeClass(slug: string | null | undefined): string {
  if (!slug) return ''
  const key = badgeFilterKey(slug)
  if (key.startsWith('vhr:')) {
    if (key.includes('ONE_OWNER') || key.includes('1_OWNER')) {
      return 'bg-blue-100 text-blue-800'
    }
    if (key.includes('NO_ACCIDENT')) {
      return 'bg-green-100 text-green-800'
    }
    return 'bg-gray-100 text-gray-700'
  }
  for (const [prefix, cls] of Object.entries(BADGE_COLORS)) {
    if (key === prefix || key.startsWith(prefix + '_')) return cls
  }
  return 'bg-gray-100 text-gray-700'
}

/** All distinct badge filter keys present in the vehicle set (+ ``none``). */
export function badgeFilterOptions(vehicles: { carfaxBadge?: string | null }[]): string[] {
  const keys = new Set<string>()
  for (const v of vehicles) {
    keys.add(badgeFilterKey(v.carfaxBadge))
  }
  keys.add('none')
  return [...keys].sort((a, b) => {
    if (a === 'none') return 1
    if (b === 'none') return -1
    return badgeLabel(a).localeCompare(badgeLabel(b))
  })
}

export function badgeFilterLabel(key: string): string {
  return key === 'none' ? 'No badge' : badgeLabel(key)
}
