/**
 * Exterior-colour bucketing.
 *
 * Dealers write free-text marketing names ("Black Sapphire Metallic",
 * "Alpine White", "Frozen Portimao Blue"), so a useful colour filter has to
 * normalize first. Order matters: the first pattern that matches wins, so the
 * specific names sit above the generic words they contain.
 */
export const COLOR_BUCKETS = [
  'black', 'white', 'red', 'blue', 'gray', 'silver', 'green', 'other',
] as const

export type ColorBucket = (typeof COLOR_BUCKETS)[number]

const PATTERNS: [ColorBucket, RegExp][] = [
  ['black',  /\b(black|sapphire|carbon\s*black|jet\s*black|midnight)\b/i],
  ['white',  /\b(white|alpine|mineral\s*white|snapper\s*rocks?\s*white)\b/i],
  ['red',    /\b(red|melbourne|imola|aventurin|burgundy|crimson|maroon)\b/i],
  ['blue',   /\b(blue|portimao|tanzanite|phytonic|marina\s*bay|estoril|navy)\b/i],
  ['gray',   /\b(gray|grey|graphite|dravit|skyscraper|brooklyn|frozen\s*pure|donington)\b/i],
  ['silver', /\b(silver|glacier|titanium|platinum)\b/i],
  ['green',  /\b(green|isle\s*of\s*man|san\s*remo|verde|oxide)\b/i],
]

/** Map a raw dealer colour string onto one of COLOR_BUCKETS. */
export function colorBucket(raw: string | null | undefined): ColorBucket {
  if (!raw || !raw.trim()) return 'other'
  for (const [bucket, re] of PATTERNS) {
    if (re.test(raw)) return bucket
  }
  return 'other'
}

export const COLOR_BUCKET_LABEL: Record<string, string> = {
  black: 'Black', white: 'White', red: 'Red', blue: 'Blue',
  gray: 'Gray', silver: 'Silver', green: 'Green', other: 'Other/unknown',
}
