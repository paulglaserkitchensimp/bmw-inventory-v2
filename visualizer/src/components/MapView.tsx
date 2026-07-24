import { useEffect } from 'react'
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { Vehicle } from '../types'
import type { GeoMap } from '../hooks/useGeocoder'
import type { AnnotationMap } from '../hooks/useAnnotations'

// Fix default leaflet marker icons (broken in Vite)
import iconUrl from 'leaflet/dist/images/marker-icon.png'
import iconRetinaUrl from 'leaflet/dist/images/marker-icon-2x.png'
import shadowUrl from 'leaflet/dist/images/marker-shadow.png'
L.Icon.Default.mergeOptions({ iconUrl, iconRetinaUrl, shadowUrl })

// Leaflet doesn't watch its container size — when the inline detail pane opens
// or closes the map pane resizes and tiles would go stale/gray without this.
function ResizeEffect() {
  const map = useMap()
  useEffect(() => {
    const container = map.getContainer()
    const observer = new ResizeObserver(() => map.invalidateSize())
    observer.observe(container)
    return () => observer.disconnect()
  }, [map])
  return null
}

// Fly to selected vehicle's dealer + close any open popup when selection changes
function SelectionEffect({ coords, selectedVin }: { coords: [number, number] | null; selectedVin: string | null }) {
  const map = useMap()
  if (coords) {
    map.flyTo(coords, Math.max(map.getZoom(), 10), { duration: 0.8 })
  }
  if (selectedVin) {
    map.closePopup()
  }
  return null
}

interface DealerGroup {
  key: string
  city: string
  state: string
  coords: [number, number]
  vehicles: Vehicle[]
}

interface Props {
  vehicles: Vehicle[]
  geoMap: GeoMap
  selected: Vehicle | null
  onSelect: (v: Vehicle) => void
  annotations: AnnotationMap
}

export default function MapView({ vehicles, geoMap, selected, onSelect, annotations }: Props) {
  // Hide pass-tagged vehicles entirely from the map.
  const visible = vehicles.filter(v => annotations[v.vin]?.tag !== 'pass')

  // Group vehicles by dealer city+state
  const groups = new Map<string, DealerGroup>()
  for (const v of visible) {
    if (!v.dealerCity || !v.dealerState) continue
    const key = `${v.dealerCity}, ${v.dealerState}`
    const coords = geoMap[key]
    if (!coords) continue
    if (!groups.has(key)) {
      groups.set(key, { key, city: v.dealerCity, state: v.dealerState, coords, vehicles: [] })
    }
    groups.get(key)!.vehicles.push(v)
  }

  const selectedKey = selected && selected.dealerCity && selected.dealerState
    ? `${selected.dealerCity}, ${selected.dealerState}`
    : null
  const selectedCoords = selectedKey ? geoMap[selectedKey] ?? null : null

  const geocoded = groups.size
  const total    = new Set(visible.filter(v => v.dealerCity && v.dealerState).map(v => `${v.dealerCity}, ${v.dealerState}`)).size

  return (
    <div className="relative w-full h-full">
      {geocoded < total && (
        <div className="absolute top-2 right-2 z-[1000] bg-white/90 text-xs text-gray-500 px-2 py-1 rounded shadow">
          Geocoding {geocoded}/{total} dealers…
        </div>
      )}
      <MapContainer
        center={[39.5, -98.35]}
        zoom={4}
        className="w-full h-full"
        scrollWheelZoom
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <ResizeEffect />
        <SelectionEffect coords={selectedCoords} selectedVin={selected?.vin ?? null} />

        {[...groups.values()].map(g => {
          const isActive = g.key === selectedKey
          const icon = L.divIcon({
            className: '',
            html: `<div style="
              background:${isActive ? '#2563eb' : '#1e40af'};
              color:white;
              border-radius:50%;
              width:${isActive ? 32 : 26}px;
              height:${isActive ? 32 : 26}px;
              display:flex;
              align-items:center;
              justify-content:center;
              font-size:${isActive ? 13 : 11}px;
              font-weight:700;
              border:2px solid white;
              box-shadow:0 2px 4px rgba(0,0,0,.3);
            ">${g.vehicles.length}</div>`,
            iconSize: [isActive ? 32 : 26, isActive ? 32 : 26],
            iconAnchor: [isActive ? 16 : 13, isActive ? 16 : 13],
          })

          return (
            <Marker key={g.key} position={g.coords} icon={icon}>
              <Popup maxWidth={280} minWidth={200}>
                <div className="text-sm">
                  <div className="font-semibold text-gray-800 mb-2">
                    {g.city}, {g.state} — {g.vehicles.length} vehicle{g.vehicles.length !== 1 ? 's' : ''}
                  </div>
                  {g.vehicles.map(v => (
                    <div
                      key={v.vin}
                      onClick={() => onSelect(v)}
                      className="cursor-pointer hover:bg-blue-50 rounded px-1 py-0.5 -mx-1 flex justify-between items-center"
                    >
                      <span className="text-gray-700">
                        {v.year} {v.trim ?? ''}&nbsp;
                        <span className="text-gray-400 text-xs">
                          {v.odometer != null ? `${v.odometer.toLocaleString()} mi` : 'new'}
                        </span>
                      </span>
                      {v.certified && <span className="text-xs text-indigo-600 ml-1">CPO</span>}
                    </div>
                  ))}
                </div>
              </Popup>
            </Marker>
          )
        })}
      </MapContainer>
    </div>
  )
}
