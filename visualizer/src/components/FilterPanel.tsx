import type { Filters, TriState, Vehicle } from '../types'
import { DEFAULT_FILTERS } from '../types'
import { TAG_META } from '../hooks/useAnnotations'
import type { Tag } from '../hooks/useAnnotations'
import { OWNER_COUNT_BUCKETS } from '../hooks/useVehicles'

const OWNER_BUCKET_LABEL: Record<string, string> = {
  '1': '1 owner',
  '2+': '2+ owners',
  unknown: 'Unknown',
}

interface Props {
  filters: Filters
  onChange: (f: Filters) => void
  vehicles: Vehicle[]   // full unfiltered set for deriving options
}

function MultiSelect({
  label, options, selected, onChange,
}: { label: string; options: string[]; selected: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="mb-4">
      <label className="block text-xs font-semibold text-gray-500 uppercase mb-1">{label}</label>
      <div className="flex flex-wrap gap-1">
        {options.map(opt => {
          const active = selected.includes(opt)
          return (
            <button
              key={opt}
              onClick={() => onChange(active ? selected.filter(s => s !== opt) : [...selected, opt])}
              className={`px-2 py-0.5 rounded text-xs border transition-colors ${
                active
                  ? 'bg-blue-600 text-white border-blue-600'
                  : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400'
              }`}
            >
              {opt}
            </button>
          )
        })}
      </div>
    </div>
  )
}

/**
 * Tri-state chip selector. Clicking an option cycles:
 *   default → include (blue) → exclude (red) → default.
 * Used for the State filter so the user can both narrow to a state and
 * blacklist states in one control.
 */
function TriStateSelect({
  label, options, selected, onChange, renderLabel,
}: {
  label: string
  options: string[]
  selected: Record<string, TriState>
  onChange: (v: Record<string, TriState>) => void
  /** Optional map from option key → display label. Defaults to the key itself. */
  renderLabel?: (opt: string) => string
}) {
  const cycle = (opt: string) => {
    const cur = selected[opt]
    const next: Record<string, TriState> = { ...selected }
    if (!cur) next[opt] = 'include'
    else if (cur === 'include') next[opt] = 'exclude'
    else delete next[opt]
    onChange(next)
  }

  return (
    <div className="mb-4">
      <div className="flex items-baseline justify-between mb-1">
        <label className="block text-xs font-semibold text-gray-500 uppercase">{label}</label>
        <span className="text-[10px] text-gray-400">click: only → exclude → off</span>
      </div>
      <div className="flex flex-wrap gap-1">
        {options.map(opt => {
          const state = selected[opt]
          const display = renderLabel ? renderLabel(opt) : opt
          const cls =
            state === 'include'
              ? 'bg-blue-600 text-white border-blue-600'
            : state === 'exclude'
              ? 'bg-red-500 text-white border-red-500 line-through'
              : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400'
          return (
            <button
              key={opt}
              onClick={() => cycle(opt)}
              title={
                state === 'include' ? `Only ${display}` :
                state === 'exclude' ? `Excluding ${display}` :
                display
              }
              className={`px-2 py-0.5 rounded text-xs border transition-colors ${cls}`}
            >
              {state === 'exclude' ? `−${display}` : display}
            </button>
          )
        })}
      </div>
    </div>
  )
}

const TAG_FILTER_OPTIONS: string[] = [
  'interesting',
  'shortlisted',
  'contacted',
  'negotiating',
  'purchased',
  'negotiated_pass',
  'nonleasable_pass',
  'pass',
  'untagged',
]
const tagOptionLabel = (key: string): string =>
  key === 'untagged' ? 'Untagged' : TAG_META[key as Tag]?.label ?? key

function RangeRow({ label, minKey, maxKey, filters, onChange, placeholder = '' }: {
  label: string; minKey: keyof Filters; maxKey: keyof Filters
  filters: Filters; onChange: (f: Filters) => void; placeholder?: string
}) {
  return (
    <div className="mb-4">
      <label className="block text-xs font-semibold text-gray-500 uppercase mb-1">{label}</label>
      <div className="flex gap-2">
        <input
          type="number"
          placeholder={`Min${placeholder}`}
          value={String(filters[minKey])}
          onChange={e => onChange({ ...filters, [minKey]: e.target.value })}
          className="w-full border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:border-blue-400"
        />
        <input
          type="number"
          placeholder={`Max${placeholder}`}
          value={String(filters[maxKey])}
          onChange={e => onChange({ ...filters, [maxKey]: e.target.value })}
          className="w-full border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:border-blue-400"
        />
      </div>
    </div>
  )
}

export default function FilterPanel({ filters, onChange, vehicles }: Props) {
  const states = [...new Set(vehicles.map(v => v.dealerState).filter(Boolean) as string[])].sort()
  const models = [...new Set(vehicles.map(v => v.model).filter(Boolean) as string[])].sort()
  const trims  = [...new Set(vehicles.map(v => v.trim).filter(Boolean) as string[])].sort()
  const years  = [...new Set(vehicles.map(v => v.year).filter((y): y is number => y !== null))]
    .sort((a, b) => b - a)   // newest first

  const dirty = JSON.stringify(filters) !== JSON.stringify(DEFAULT_FILTERS)

  return (
    <aside className="w-64 shrink-0 bg-white border-r border-gray-200 overflow-y-auto flex flex-col">
      <div className="p-4 border-b border-gray-100 flex items-center justify-between">
        <span className="font-semibold text-gray-800">Filters</span>
        {dirty && (
          <button
            onClick={() => onChange(DEFAULT_FILTERS)}
            className="text-xs text-blue-600 hover:underline"
          >
            Reset all
          </button>
        )}
      </div>

      <div className="p-4 flex-1">
        {/* Search */}
        <div className="mb-4">
          <label className="block text-xs font-semibold text-gray-500 uppercase mb-1">Search</label>
          <input
            type="text"
            placeholder="VIN, dealer, color…"
            value={filters.search}
            onChange={e => onChange({ ...filters, search: e.target.value })}
            className="w-full border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:border-blue-400"
          />
        </div>

        <TriStateSelect label="State" options={states} selected={filters.states}
          onChange={v => onChange({ ...filters, states: v })} />

        <TriStateSelect label="Tag" options={TAG_FILTER_OPTIONS}
          selected={filters.tags} renderLabel={tagOptionLabel}
          onChange={v => onChange({ ...filters, tags: v })} />

        <MultiSelect label="Year" options={years.map(String)}
          selected={filters.years.map(String)}
          onChange={v => onChange({ ...filters, years: v.map(Number) })} />

        <MultiSelect label="Model" options={models} selected={filters.models}
          onChange={v => onChange({ ...filters, models: v })} />

        <MultiSelect label="Trim" options={trims} selected={filters.trims}
          onChange={v => onChange({ ...filters, trims: v })} />

        <RangeRow label="Miles" minKey="minMiles" maxKey="maxMiles" filters={filters} onChange={onChange} />
        <RangeRow label="Days on Lot" minKey="minDays" maxKey="maxDays" filters={filters} onChange={onChange} />
        <RangeRow label="Price ($)" minKey="minPrice" maxKey="maxPrice" filters={filters} onChange={onChange} />

        {/* Certified */}
        <div className="mb-4">
          <label className="block text-xs font-semibold text-gray-500 uppercase mb-1">Certified</label>
          <div className="flex gap-1">
            {(['All', 'Yes', 'No'] as const).map(opt => {
              const val = opt === 'All' ? null : opt === 'Yes'
              const active = filters.certified === val
              return (
                <button key={opt} onClick={() => onChange({ ...filters, certified: val })}
                  className={`px-3 py-0.5 rounded text-xs border transition-colors ${active ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400'}`}>
                  {opt}
                </button>
              )
            })}
          </div>
        </div>

        {/* Owner count — tri-state so the user can both narrow to a count
            (click once = only) and eliminate counts (click twice = exclude). */}
        <TriStateSelect
          label="Owner Count"
          options={[...OWNER_COUNT_BUCKETS]}
          selected={filters.ownerCounts}
          renderLabel={k => OWNER_BUCKET_LABEL[k] ?? k}
          onChange={v => onChange({ ...filters, ownerCounts: v })}
        />

        {/* BMW Dealer — checks dealerName.includes('bmw'), so it works across
            DDC, DealerInspire, Autotrader, and Cars.com regardless of platform. */}
        <div className="mb-4">
          <label className="block text-xs font-semibold text-gray-500 uppercase mb-1">BMW Dealer</label>
          <div className="flex gap-1">
            {(['All', 'Yes', 'No'] as const).map(opt => {
              const val = opt === 'All' ? null : opt === 'Yes'
              const active = filters.bmwDealer === val
              return (
                <button key={opt} onClick={() => onChange({ ...filters, bmwDealer: val })}
                  className={`px-3 py-0.5 rounded text-xs border transition-colors ${active ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400'}`}>
                  {opt}
                </button>
              )
            })}
          </div>
        </div>

        {/* Platform */}
        <div className="mb-4">
          <label className="block text-xs font-semibold text-gray-500 uppercase mb-1">Platform</label>
          <div className="flex gap-1">
            {[['Any', ''], ['Dealer.com', 'dealercom'], ['DealerInspire', 'dealerinspire']].map(([label, val]) => (
              <button key={val} onClick={() => onChange({ ...filters, platform: val })}
                className={`px-2 py-0.5 rounded text-xs border transition-colors ${filters.platform === val ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400'}`}>
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </aside>
  )
}
