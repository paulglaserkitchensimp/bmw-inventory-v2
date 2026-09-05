import { useState, useMemo } from 'react'
import { useVehicles, applyFilters } from './hooks/useVehicles'
import { useGeocoder } from './hooks/useGeocoder'
import { useAnnotations } from './hooks/useAnnotations'
import { usePersistedFilters } from './hooks/usePersistedFilters'
import { useDarkMode } from './hooks/useDarkMode'
import FilterPanel from './components/FilterPanel'
import VehicleTable from './components/VehicleTable'
import MapView from './components/MapView'
import VehicleDetail from './components/VehicleDetail'
import type { Vehicle } from './types'

type ViewMode = 'table' | 'map' | 'split'

export default function App() {
  const { vehicles, loading, error } = useVehicles()
  const geoMap = useGeocoder(vehicles)
  const { annotations, cycleTag, setTag, setComment, setLeasehackrUrl, setListingUrl } = useAnnotations()
  const [filters, setFilters] = usePersistedFilters()
  const [selected, setSelected] = useState<Vehicle | null>(null)
  const [view, setView] = useState<ViewMode>('split')
  const { dark, toggleDark } = useDarkMode()

  // geoMap participates in filtering because of the max-distance filter: it
  // grows as Nominatim resolves dealer cities, so the filtered set has to
  // recompute when it changes.
  const filtered = useMemo(
    () => applyFilters(vehicles, filters, annotations, geoMap),
    [vehicles, filters, annotations, geoMap],
  )

  if (loading) return (
    <div className="h-screen flex items-center justify-center bg-gray-50">
      <div className="text-gray-400 text-sm animate-pulse">Loading inventory…</div>
    </div>
  )

  if (error) return (
    <div className="h-screen flex items-center justify-center bg-gray-50">
      <div className="text-red-500 text-sm">Failed to load results.json: {error}</div>
    </div>
  )

  return (
    <div className="h-screen flex flex-col overflow-hidden bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-4 py-2.5 flex items-center gap-4 shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-lg font-bold text-blue-700">BMW</span>
          <span className="text-gray-400">/</span>
          <span className="text-sm text-gray-600 font-medium">Inventory Explorer</span>
        </div>

        <div className="ml-2 text-sm text-gray-400">
          Showing <span className="font-semibold text-gray-700">{filtered.length}</span>
          {filtered.length !== vehicles.length && (
            <> of <span className="font-semibold text-gray-700">{vehicles.length}</span></>
          )} vehicles
        </div>

        <div className="ml-auto flex items-center gap-2">
          <div className="flex items-center gap-1 bg-gray-100 rounded p-0.5">
            {(['table', 'split', 'map'] as ViewMode[]).map(m => (
              <button
                key={m}
                onClick={() => setView(m)}
                className={`px-3 py-1 rounded text-xs font-medium transition-colors capitalize ${
                  view === m ? 'bg-white shadow text-gray-900' : 'text-gray-500 hover:text-gray-800'
                }`}
              >
                {m}
              </button>
            ))}
          </div>
          <button
            onClick={toggleDark}
            title={dark ? 'Switch to light mode' : 'Switch to dark mode'}
            className="px-2 py-1 rounded text-sm bg-gray-100 text-gray-500 hover:text-gray-800 transition-colors"
          >
            {dark ? '☀️' : '🌙'}
          </button>
        </div>
      </header>

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        <FilterPanel filters={filters} onChange={setFilters} vehicles={vehicles} />

        <main className="flex-1 overflow-hidden flex">
          {/* Table pane */}
          {(view === 'table' || view === 'split') && (
            <div className={`flex flex-col overflow-hidden bg-white border-r border-gray-200 ${view === 'split' ? 'w-1/2' : 'w-full'}`}>
              <VehicleTable
                vehicles={filtered}
                onSelect={v => setSelected(prev => prev?.vin === v.vin ? null : v)}
                selected={selected}
                annotations={annotations}
                onCycleTag={cycleTag}
                onSetComment={setComment}
                geoMap={geoMap}
              />
            </div>
          )}

          {/* Map pane */}
          {(view === 'map' || view === 'split') && (
            <div className={`${view === 'split' ? 'w-1/2' : 'w-full'} min-w-0`}>
              <MapView
                vehicles={filtered}
                geoMap={geoMap}
                selected={selected}
                onSelect={v => setSelected(prev => prev?.vin === v.vin ? null : v)}
                annotations={annotations}
              />
            </div>
          )}

          {/* Inline detail pane — sits beside the table/map, which shrink to
              make room (flex siblings) instead of being covered by an overlay.
              `key` forces a remount when the user picks a different row so
              local draft state (notes, LH URL) re-initializes from the new
              annotation. */}
          {selected && (
            <VehicleDetail
              key={selected.vin}
              vehicle={selected}
              onClose={() => setSelected(null)}
              annotations={annotations}
              onSetTag={setTag}
              onSetComment={setComment}
              onSetLeasehackrUrl={setLeasehackrUrl}
              onSetListingUrl={setListingUrl}
            />
          )}
        </main>
      </div>
    </div>
  )
}
