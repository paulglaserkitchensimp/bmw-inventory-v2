import { useState } from 'react'
import type { Vehicle } from '../types'
import type { AnnotationMap, Tag } from '../hooks/useAnnotations'
import { TAG_META, nextTag } from '../hooks/useAnnotations'

interface Props {
  vehicle: Vehicle
  onClose: () => void
  annotations: AnnotationMap
  onSetTag: (vin: string, tag: Tag | null) => void
  onSetComment: (vin: string, comment: string) => void
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  if (!value && value !== 0) return null
  return (
    <div className="flex gap-2 py-1 border-b border-gray-50">
      <span className="text-xs text-gray-400 w-28 shrink-0">{label}</span>
      <span className="text-sm text-gray-800 break-all">{value}</span>
    </div>
  )
}

const TAG_ORDER: (Tag | null)[] = [null, 'shortlisted', 'contacted', 'purchased', 'pass']

export default function VehicleDetail({ vehicle: v, onClose, annotations, onSetTag, onSetComment }: Props) {
  const ann = annotations[v.vin]
  const currentTag = ann?.tag ?? null
  const currentComment = ann?.comment ?? ''
  const [commentDraft, setCommentDraft] = useState(currentComment)
  const [commentSaved, setCommentSaved] = useState(false)

  function handleCommentSave() {
    onSetComment(v.vin, commentDraft.trim())
    setCommentSaved(true)
    setTimeout(() => setCommentSaved(false), 1500)
  }

  return (
    <div className="fixed inset-y-0 right-0 w-96 bg-white shadow-xl z-50 flex flex-col border-l border-gray-200">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 bg-gray-50">
        <div>
          <div className="font-semibold text-gray-900">
            {v.year} BMW {v.model} {v.trim}
          </div>
          <div className="text-xs text-gray-500 font-mono mt-0.5">{v.vin}</div>
        </div>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-xl leading-none px-1">×</button>
      </div>

      <div className="overflow-y-auto flex-1 px-4 py-3">

        {/* ── Annotation section ── */}
        <div className="mb-4 p-3 rounded-lg border border-gray-200 bg-gray-50">
          <div className="text-xs font-semibold text-gray-400 uppercase mb-2">Your Notes</div>

          {/* Tag selector */}
          <div className="flex flex-wrap gap-1.5 mb-3">
            {TAG_ORDER.map(tag => {
              const isActive = tag === currentTag
              if (!tag) {
                return (
                  <button
                    key="none"
                    onClick={() => onSetTag(v.vin, null)}
                    className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                      isActive
                        ? 'bg-gray-200 border-gray-400 text-gray-700 font-medium'
                        : 'border-gray-300 text-gray-400 hover:border-gray-400 hover:text-gray-600'
                    }`}
                  >
                    None
                  </button>
                )
              }
              const meta = TAG_META[tag]
              return (
                <button
                  key={tag}
                  onClick={() => onSetTag(v.vin, isActive ? null : tag)}
                  className={`text-xs px-2.5 py-1 rounded-full border transition-colors font-medium ${
                    isActive
                      ? `${meta.bg} ${meta.color} ring-2 ring-offset-1 ring-current`
                      : 'border-gray-300 text-gray-400 hover:border-gray-400 hover:text-gray-600'
                  }`}
                >
                  {meta.label}
                </button>
              )
            })}
          </div>

          {/* Comment box */}
          <textarea
            value={commentDraft}
            onChange={e => {
              setCommentDraft(e.target.value)
              setCommentSaved(false)
            }}
            onBlur={handleCommentSave}
            placeholder="Add a note about this vehicle…"
            rows={3}
            className="w-full text-sm border border-gray-300 rounded px-2 py-1.5 resize-none outline-none focus:border-blue-400 transition-colors"
          />
          <div className="flex justify-end mt-1">
            <span className={`text-xs transition-opacity ${commentSaved ? 'text-green-600 opacity-100' : 'opacity-0'}`}>
              Saved ✓
            </span>
          </div>
        </div>

        {/* ── Vehicle data ── */}
        <Row label="Odometer" value={v.odometer != null ? `${v.odometer.toLocaleString()} mi` : 'new'} />
        <Row label="Days on lot" value={v.daysOnLot != null ? `${v.daysOnLot} days` : undefined} />
        <Row label="Price" value={v.internetPrice ? `$${v.internetPrice.toLocaleString()}` : undefined} />
        <Row label="Ext. Color" value={v.extColor} />
        <Row label="Int. Color" value={v.interiorColor} />
        <Row label="Type" value={v.type} />
        <Row label="Certified" value={v.certified ? 'Yes (BMW CPO)' : 'No'} />
        <Row label="Platform" value={v.platform === 'dealercom' ? 'Dealer.com' : 'DealerInspire'} />

        <div className="mt-3 mb-1 text-xs font-semibold text-gray-400 uppercase">Dealer</div>
        <Row label="Name" value={v.dealerName} />
        <Row label="Location" value={[v.dealerCity, v.dealerState].filter(Boolean).join(', ')} />
        {v.dealerUrl && (
          <Row label="Website" value={
            <a href={v.dealerUrl} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">{v.dealerUrl}</a>
          } />
        )}

        <div className="mt-3 mb-1 text-xs font-semibold text-gray-400 uppercase">CarFax</div>
        <Row label="Owner count" value={v.ownerCount != null ? `${v.ownerCount}` : 'Unknown'} />
        <Row label="Badge" value={v.carfaxBadge} />
        {v.carfaxUrl && (
          <div className="py-1 border-b border-gray-50">
            <a href={v.carfaxUrl} target="_blank" rel="noreferrer"
              className="text-sm text-green-600 hover:underline">
              View Free CarFax Report ↗
            </a>
          </div>
        )}
        {!v.carfaxUrl && v.carfaxPaywallUrl && (
          <div className="py-1 border-b border-gray-50">
            <a href={v.carfaxPaywallUrl} target="_blank" rel="noreferrer"
              className="text-sm text-gray-500 hover:underline">
              CarFax (paywall) ↗
            </a>
          </div>
        )}

        {v.ownershipAssessment && (
          <>
            <div className="mt-3 mb-1 text-xs font-semibold text-gray-400 uppercase">Ownership Analysis</div>
            <div className={`text-sm px-2 py-1.5 rounded mb-1 ${
              v.ownershipAssessment === 'dealer_only' ? 'bg-green-50 text-green-800' :
              v.ownershipAssessment === 'likely_private' ? 'bg-yellow-50 text-yellow-800' :
              'bg-gray-50 text-gray-600'
            }`}>
              <span className="font-medium capitalize">{v.ownershipAssessment.replace('_', ' ')}</span>
              {v.ownershipConfidence && <span className="text-xs ml-2 opacity-70">({v.ownershipConfidence} confidence)</span>}
            </div>
            {v.ownershipReasoning && (
              <p className="text-xs text-gray-500 mt-1">{v.ownershipReasoning}</p>
            )}
          </>
        )}

        <div className="mt-4 flex flex-col gap-2">
          {(v.resolvedLink || v.link) && (
            <a href={v.resolvedLink ?? v.link ?? ''} target="_blank" rel="noreferrer"
              className="block text-center text-sm bg-blue-600 text-white py-2 rounded hover:bg-blue-700 transition-colors">
              View Listing ↗
            </a>
          )}
          {v.nhtsaUrl && (
            <a href={v.nhtsaUrl} target="_blank" rel="noreferrer"
              className="block text-center text-sm border border-gray-300 text-gray-700 py-2 rounded hover:bg-gray-50 transition-colors">
              NHTSA VIN Decode ↗
            </a>
          )}
        </div>
      </div>
    </div>
  )
}
