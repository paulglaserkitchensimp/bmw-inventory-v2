import type { Filters, Vehicle } from '../types'
import { DEFAULT_FILTERS } from '../types'

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
  const states  = [...new Set(vehicles.map(v => v.dealerState).filter(Boolean) as string[])].sort()
  const trims   = [...new Set(vehicles.map(v => v.trim).filter(Boolean) as string[])].sort()

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

        <MultiSelect label="State" options={states} selected={filters.states}
          onChange={v => onChange({ ...filters, states: v })} />

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

        {/* Owner count */}
        <div className="mb-4">
          <label className="block text-xs font-semibold text-gray-500 uppercase mb-1">Owner Count</label>
          <div className="flex flex-wrap gap-1">
            {[['Any', ''], ['1 owner', '1'], ['2+ owners', '2+'], ['Unknown', 'unknown']].map(([label, val]) => (
              <button key={val} onClick={() => onChange({ ...filters, ownerCount: val })}
                className={`px-2 py-0.5 rounded text-xs border transition-colors ${filters.ownerCount === val ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400'}`}>
                {label}
              </button>
            ))}
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
