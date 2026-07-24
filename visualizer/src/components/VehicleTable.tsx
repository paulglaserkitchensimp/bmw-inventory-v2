import { useState, useRef, useMemo } from 'react'
import type { Vehicle } from '../types'
import type { AnnotationMap, Tag } from '../hooks/useAnnotations'
import { TAG_META } from '../hooks/useAnnotations'
import type { GeoMap } from '../hooks/useGeocoder'
import { badgeClass, badgeLabel, badgeLabelShort } from '../utils/carfaxBadge'

type SortKey = 'distance' | 'odometer' | 'daysOnLot' | 'internetPrice' | 'year' | 'dealerState' | 'dealerName'
type SortDir = 'asc' | 'desc'

// Proximity sort origin — change these coords to recalibrate.
// 48226 = Detroit, MI
export const ORIGIN_COORDS: [number, number] = [42.3316, -83.0466]
export const ORIGIN_LABEL = '48226'

function haversineMiles(a: [number, number], b: [number, number]): number {
  const R = 3958.8
  const toRad = (x: number) => (x * Math.PI) / 180
  const dLat = toRad(b[0] - a[0])
  const dLon = toRad(b[1] - a[1])
  const lat1 = toRad(a[0])
  const lat2 = toRad(b[0])
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(h))
}

function vehicleDistance(v: Vehicle, geoMap: GeoMap): number | null {
  if (!v.dealerCity || !v.dealerState) return null
  const coords = geoMap[`${v.dealerCity}, ${v.dealerState}`]
  if (!coords) return null
  return haversineMiles(ORIGIN_COORDS, coords)
}

function SortHeader({ label, col, sort, onSort }: {
  label: string; col: SortKey
  sort: { key: SortKey; dir: SortDir }
  onSort: (k: SortKey) => void
}) {
  const active = sort.key === col
  return (
    <th
      onClick={() => onSort(col)}
      className="px-3 py-2 text-left text-xs font-semibold text-gray-500 uppercase cursor-pointer select-none hover:text-gray-800 whitespace-nowrap"
    >
      {label}
      {active && (
        <span className="ml-1 text-blue-500">{sort.dir === 'asc' ? '↑' : '↓'}</span>
      )}
    </th>
  )
}

// Tag pill — click cycles through statuses
function TagPill({ vin, tag, onCycle }: { vin: string; tag: Tag | null; onCycle: (vin: string) => void }) {
  if (!tag) {
    return (
      <button
        onClick={e => { e.stopPropagation(); onCycle(vin) }}
        title="Click to tag"
        className="text-xs px-1.5 py-0.5 rounded border border-dashed border-gray-300 text-gray-400 hover:border-gray-400 hover:text-gray-600 transition-colors whitespace-nowrap"
      >
        + tag
      </button>
    )
  }
  const meta = TAG_META[tag]
  return (
    <button
      onClick={e => { e.stopPropagation(); onCycle(vin) }}
      title={`Click to cycle tag (current: ${meta.label})`}
      className={`text-xs px-1.5 py-0.5 rounded border font-medium transition-colors whitespace-nowrap ${meta.bg} ${meta.color}`}
    >
      {meta.label}
    </button>
  )
}

// Inline comment cell — click to edit, blur/enter to save
function CommentCell({ vin, comment, onChange }: {
  vin: string
  comment: string
  onChange: (vin: string, val: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(comment)
  const inputRef = useRef<HTMLInputElement>(null)

  function startEdit(e: React.MouseEvent) {
    e.stopPropagation()
    setDraft(comment)
    setEditing(true)
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  function commit() {
    setEditing(false)
    onChange(vin, draft.trim())
  }

  function handleKey(e: React.KeyboardEvent) {
    if (e.key === 'Enter') commit()
    if (e.key === 'Escape') { setDraft(comment); setEditing(false) }
  }

  if (editing) {
    return (
      <input
        ref={inputRef}
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={handleKey}
        onClick={e => e.stopPropagation()}
        placeholder="Add note…"
        className="w-full min-w-[120px] text-xs border border-blue-400 rounded px-1.5 py-0.5 outline-none bg-white"
      />
    )
  }

  return (
    <span
      onClick={startEdit}
      title={comment || 'Click to edit note'}
      className={`text-xs cursor-text rounded px-1 py-0.5 hover:bg-gray-100 transition-colors block truncate ${
        comment ? 'text-gray-700' : 'text-gray-300 italic'
      }`}
    >
      {comment || 'note…'}
    </span>
  )
}

interface Props {
  vehicles: Vehicle[]
  onSelect: (v: Vehicle) => void
  selected: Vehicle | null
  annotations: AnnotationMap
  onCycleTag: (vin: string) => void
  onSetComment: (vin: string, comment: string) => void
  geoMap: GeoMap
}

export default function VehicleTable({
  vehicles, onSelect, selected, annotations, onCycleTag, onSetComment, geoMap,
}: Props) {
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: 'distance', dir: 'asc' })

  function toggleSort(key: SortKey) {
    setSort(prev => prev.key === key
      ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
      : { key, dir: 'asc' })
  }

  // Precompute distance per VIN so sort + render share one calculation.
  const distanceByVin = useMemo(() => {
    const m: Record<string, number | null> = {}
    for (const v of vehicles) m[v.vin] = vehicleDistance(v, geoMap)
    return m
  }, [vehicles, geoMap])

  const sorted = useMemo(() => {
    const getKey = (v: Vehicle): number | string | null => {
      if (sort.key === 'distance') return distanceByVin[v.vin]
      if (sort.key === 'dealerName') return v.dealerName?.toLowerCase() ?? null
      return v[sort.key] as number | string | null
    }

    const cmp = (a: Vehicle, b: Vehicle) => {
      const va = getKey(a) ?? (sort.dir === 'asc' ? Infinity : -Infinity)
      const vb = getKey(b) ?? (sort.dir === 'asc' ? Infinity : -Infinity)
      if (va < vb) return sort.dir === 'asc' ? -1 : 1
      if (va > vb) return sort.dir === 'asc' ? 1 : -1
      return 0
    }

    // Always pin terminal-rejected vehicles (pass + negotiated_pass +
    // nonleasable_pass) to the bottom, then sort within each group.
    const keep: Vehicle[] = []
    const passed: Vehicle[] = []
    for (const v of vehicles) {
      const t = annotations[v.vin]?.tag
      if (t === 'pass' || t === 'negotiated_pass' || t === 'nonleasable_pass') passed.push(v)
      else keep.push(v)
    }
    keep.sort(cmp)
    passed.sort(cmp)
    return [...keep, ...passed]
  }, [vehicles, sort, annotations, distanceByVin])

  return (
    <div className="overflow-auto flex-1">
      <table className="w-full text-sm border-collapse">
        <thead className="bg-gray-50 sticky top-0 z-10">
          <tr>
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-500 uppercase whitespace-nowrap">Tag</th>
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-500 uppercase whitespace-nowrap">Note</th>
            <SortHeader label="Year" col="year" sort={sort} onSort={toggleSort} />
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-500 uppercase whitespace-nowrap">Model</th>
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-500 uppercase whitespace-nowrap">Trim</th>
            <SortHeader label="Miles" col="odometer" sort={sort} onSort={toggleSort} />
            <SortHeader label="Days" col="daysOnLot" sort={sort} onSort={toggleSort} />
            <SortHeader label="Price" col="internetPrice" sort={sort} onSort={toggleSort} />
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-500 uppercase">Color</th>
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-500 uppercase">CarFax</th>
            <SortHeader label="Dealer" col="dealerName" sort={sort} onSort={toggleSort} />
            <SortHeader label="State" col="dealerState" sort={sort} onSort={toggleSort} />
            <SortHeader label={`Dist (${ORIGIN_LABEL})`} col="distance" sort={sort} onSort={toggleSort} />
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-500 uppercase">Links</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map(v => {
            const ann = annotations[v.vin]
            const tag = ann?.tag ?? null
            const comment = ann?.comment ?? ''
            const isSelected = selected?.vin === v.vin
            return (
              <tr
                key={v.vin}
                onClick={() => onSelect(v)}
                className={`border-b border-gray-100 cursor-pointer transition-colors ${
                  isSelected ? 'bg-blue-50' : 'hover:bg-gray-50'
                } ${v.vdpStatus === 'not_found' ? 'opacity-50' : ''}`}
              >
                <td className="px-3 py-2">
                  <TagPill vin={v.vin} tag={tag} onCycle={onCycleTag} />
                </td>
                <td className="px-3 py-2 max-w-[180px] overflow-hidden">
                  <CommentCell vin={v.vin} comment={comment} onChange={onSetComment} />
                </td>
                <td className="px-3 py-2 font-medium">{v.year}</td>
                <td className="px-3 py-2 font-medium text-gray-700 whitespace-nowrap">{v.model ?? '—'}</td>
                <td className="px-3 py-2 whitespace-nowrap">
                  <span className={`text-xs px-1.5 py-0.5 rounded ${v.certified ? 'bg-indigo-100 text-indigo-700' : 'bg-gray-100 text-gray-600'}`}>
                    {v.trim ?? '—'}
                    {v.certified && ' ★'}
                  </span>
                </td>
                <td className="px-3 py-2 tabular-nums">
                  {v.odometer !== null ? v.odometer.toLocaleString() : 'new'}
                </td>
                <td className="px-3 py-2 tabular-nums">
                  {v.daysOnLot !== null ? (
                    <span className={v.daysOnLot > 180 ? 'text-orange-600 font-medium' : ''}>
                      {v.daysOnLot}d
                    </span>
                  ) : '—'}
                </td>
                <td className="px-3 py-2 tabular-nums">
                  {v.internetPrice ? `$${v.internetPrice.toLocaleString()}` : '—'}
                </td>
                <td className="px-3 py-2 text-gray-600 max-w-[140px] truncate">{v.extColor ?? '—'}</td>
                <td className="px-3 py-2">
                  {v.carfaxBadge ? (
                    <span
                      title={badgeLabel(v.carfaxBadge)}
                      className={`text-xs px-1.5 py-0.5 rounded whitespace-nowrap ${badgeClass(v.carfaxBadge)}`}
                    >
                      {badgeLabelShort(v.carfaxBadge)}
                    </span>
                  ) : <span className="text-gray-400">—</span>}
                </td>
                <td className="px-3 py-2 max-w-[160px] truncate text-gray-700">{v.dealerName ?? '—'}</td>
                <td className="px-3 py-2">{v.dealerState ?? '—'}</td>
                <td className="px-3 py-2 tabular-nums text-gray-600">
                  {distanceByVin[v.vin] != null
                    ? `${Math.round(distanceByVin[v.vin]!).toLocaleString()} mi`
                    : <span className="text-gray-300">—</span>}
                </td>
                <td className="px-3 py-2 whitespace-nowrap">
                  <div className="flex gap-2 items-center">
                    {(v.resolvedLink || v.link) && (
                      <a href={v.resolvedLink ?? v.link ?? ''} target="_blank" rel="noreferrer"
                        onClick={e => e.stopPropagation()}
                        className="text-blue-600 hover:underline text-xs">VDP</a>
                    )}
                    {v.carfaxUrl && (
                      <a href={v.carfaxUrl} target="_blank" rel="noreferrer"
                        onClick={e => e.stopPropagation()}
                        className="text-green-600 hover:underline text-xs">CarFax</a>
                    )}
                    {v.vdpStatus === 'not_found' && (
                      <span
                        title="Dealer page returned 404 — likely sold"
                        className="text-xs px-1.5 py-0.5 rounded bg-red-100 text-red-700 font-medium"
                      >404</span>
                    )}
                    {v.vdpStatus === 'blocked' && (
                      <span
                        title="Dealer page blocked our scraper — manual verification required"
                        className="text-xs px-1.5 py-0.5 rounded bg-yellow-100 text-yellow-700 font-medium"
                      >?</span>
                    )}
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {sorted.length === 0 && (
        <div className="p-8 text-center text-gray-400">No vehicles match the current filters.</div>
      )}
    </div>
  )
}
