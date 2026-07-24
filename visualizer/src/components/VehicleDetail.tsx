import { useMemo, useState } from 'react'
import type { Vehicle } from '../types'
import type { AnnotationMap, Tag } from '../hooks/useAnnotations'
import { TAG_META } from '../hooks/useAnnotations'
import type { AutoSaveStatus } from '../hooks/useDebouncedAutoSave'
import { useDebouncedAutoSave } from '../hooks/useDebouncedAutoSave'
import { parseLeasehackrUrl, discountPercent } from '../utils/leasehackr'
import { badgeClass, badgeLabel } from '../utils/carfaxBadge'

interface Props {
  vehicle: Vehicle
  onClose: () => void
  annotations: AnnotationMap
  onSetTag: (vin: string, tag: Tag | null) => void
  onSetComment: (vin: string, comment: string) => void
  onSetLeasehackrUrl: (vin: string, url: string) => void
  onSetListingUrl: (vin: string, url: string) => void
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

function DealField({
  label, value, emphasise = false,
}: { label: string; value: React.ReactNode; emphasise?: boolean }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wide text-gray-400">{label}</span>
      <span className={`tabular-nums ${emphasise ? 'text-base font-semibold text-blue-700' : 'text-gray-800'}`}>
        {value}
      </span>
    </div>
  )
}

/**
 * Tiny inline status line shown under auto-saving inputs: "Saving…" while
 * the debounce is running and "Saved ✓" flash after a successful commit.
 * Fixed height avoids layout shift between states.
 */
function SaveIndicator({ status }: { status: AutoSaveStatus }) {
  return (
    <div className="flex justify-end mt-1 h-3">
      {status === 'pending' && (
        <span className="text-xs text-gray-400">Saving…</span>
      )}
      {status === 'saved' && (
        <span className="text-xs text-green-600">Saved ✓</span>
      )}
    </div>
  )
}

const TAG_ORDER: (Tag | null)[] = [
  null,
  'interesting',
  'shortlisted',
  'contacted',
  'negotiating',
  'purchased',
  'negotiated_pass',
  'nonleasable_pass',
  'pass',
]

export default function VehicleDetail({
  vehicle: v, onClose, annotations, onSetTag, onSetComment, onSetLeasehackrUrl, onSetListingUrl,
}: Props) {
  const ann = annotations[v.vin]
  const currentTag = ann?.tag ?? null
  const currentComment = ann?.comment ?? ''
  const currentLeaseUrl = ann?.leasehackrUrl ?? ''
  // Crawler-derived listing URL, used as the placeholder/fallback when the user
  // hasn't supplied an override.
  const scrapedListingUrl = v.resolvedLink ?? v.link ?? ''
  const currentListingUrl = ann?.listingUrl ?? ''

  // Local drafts so typing doesn't thrash the global annotation store on every
  // keystroke. The hook debounces saves and flushes on unmount so switching
  // rows (VehicleDetail gets remounted via `key={vin}` in App.tsx) can't drop
  // an in-flight edit.
  const [commentDraft, setCommentDraft] = useState(currentComment)
  const [leaseDraft, setLeaseDraft] = useState(currentLeaseUrl)
  const [listingDraft, setListingDraft] = useState(currentListingUrl)

  const commentStatus = useDebouncedAutoSave(
    commentDraft.trim(),
    currentComment,
    value => onSetComment(v.vin, value),
  )

  const leaseStatus = useDebouncedAutoSave(
    leaseDraft.trim(),
    currentLeaseUrl,
    value => onSetLeasehackrUrl(v.vin, value),
  )

  const listingStatus = useDebouncedAutoSave(
    listingDraft.trim(),
    currentListingUrl,
    value => onSetListingUrl(v.vin, value),
  )

  // Prefer the user's override, falling back to the scraped link.
  const effectiveListingUrl = currentListingUrl || scrapedListingUrl

  // Parse whatever's persisted (not the draft) so the deal summary only
  // reflects values the user has actually committed, not mid-paste typing.
  const deal = useMemo(() => parseLeasehackrUrl(currentLeaseUrl), [currentLeaseUrl])
  const pct = deal ? discountPercent(deal) : null

  const fmtMoney = (n: number | null) =>
    n === null ? '—' : `$${Math.round(n).toLocaleString()}`

  return (
    <div className="w-96 shrink-0 bg-white flex flex-col border-l border-gray-200 overflow-hidden">
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

        {/* ── VDP status banner ── */}
        {v.vdpStatus === 'not_found' && (
          <div className="mb-3 px-3 py-2 rounded border border-red-200 bg-red-50 text-sm text-red-800">
            <span className="font-semibold">Listing not found (404).</span>{' '}
            Dealer page returned 404 on every attempt — the vehicle is likely sold or relisted. Verify manually before contacting.
          </div>
        )}
        {v.vdpStatus === 'blocked' && (
          <div className="mb-3 px-3 py-2 rounded border border-yellow-200 bg-yellow-50 text-sm text-yellow-800">
            <span className="font-semibold">Couldn't verify listing.</span>{' '}
            Dealer page blocked our scraper (anti-bot / timeout). Availability and CarFax data are unconfirmed — manual research required.
          </div>
        )}

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

          {/* Comment box — auto-saves after a short pause in typing. */}
          <textarea
            value={commentDraft}
            onChange={e => setCommentDraft(e.target.value)}
            placeholder="Add a note about this vehicle…"
            rows={3}
            className="w-full text-sm border border-gray-300 rounded px-2 py-1.5 resize-none outline-none focus:border-blue-400 transition-colors"
          />
          <SaveIndicator status={commentStatus} />
        </div>

        {/* ── Leasehackr deal ── */}
        <div className="mb-4 p-3 rounded-lg border border-gray-200 bg-gray-50">
          <div className="flex items-baseline justify-between mb-2">
            <div className="text-xs font-semibold text-gray-400 uppercase">Leasehackr</div>
            {deal && currentLeaseUrl && (
              <a
                href={currentLeaseUrl}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-blue-600 hover:underline"
              >
                Open calculator ↗
              </a>
            )}
          </div>

          <input
            type="url"
            value={leaseDraft}
            onChange={e => setLeaseDraft(e.target.value)}
            placeholder="Paste calculator.leasehackr.com URL…"
            className="w-full text-xs font-mono border border-gray-300 rounded px-2 py-1.5 outline-none focus:border-blue-400 transition-colors"
          />
          <SaveIndicator status={leaseStatus} />

          {currentLeaseUrl && !deal && (
            <div className="mt-1 text-xs text-red-600">
              URL doesn't look like a Leasehackr calculator link.
            </div>
          )}

          {deal && (
            <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
              <DealField label="MSRP"          value={fmtMoney(deal.msrp)} />
              <DealField label="Selling Price" value={fmtMoney(deal.salesPrice)} />
              <DealField
                label="% off MSRP"
                value={pct !== null ? `${pct.toFixed(1)}%` : '—'}
                emphasise
              />
              <DealField
                label="MF"
                value={deal.mf !== null ? deal.mf.toFixed(5) : '—'}
              />
              <DealField
                label="Terms"
                value={
                  deal.months !== null || deal.miles !== null
                    ? `${deal.months ?? '—'} / ${deal.miles !== null ? deal.miles.toLocaleString() : '—'}`
                    : '—'
                }
              />
            </div>
          )}
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
        {v.dealerPhone && (
          <Row label="Phone" value={
            <a href={`tel:${v.dealerPhone.replace(/\D/g, '')}`} className="text-blue-600 hover:underline">{v.dealerPhone}</a>
          } />
        )}
        {v.dealerUrl && (
          <Row label="Website" value={
            <a href={v.dealerUrl} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">{v.dealerUrl}</a>
          } />
        )}

        <div className="mt-3 mb-1 text-xs font-semibold text-gray-400 uppercase">CarFax</div>
        <Row label="Owner count" value={v.ownerCount != null ? `${v.ownerCount}` : 'Unknown'} />
        <div className="flex gap-2 py-1 border-b border-gray-50">
          <span className="text-xs text-gray-400 w-28 shrink-0">Badge</span>
          {v.carfaxBadge ? (
            <span className={`text-xs px-2 py-0.5 rounded ${badgeClass(v.carfaxBadge)}`}>
              {badgeLabel(v.carfaxBadge)}
            </span>
          ) : (
            <span className="text-sm text-gray-400">Not reported</span>
          )}
        </div>
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
          {/* Editable listing URL — overrides the scraped link when set. */}
          <div>
            <label className="text-[10px] uppercase tracking-wide text-gray-400">Listing URL</label>
            <input
              type="url"
              value={listingDraft}
              onChange={e => setListingDraft(e.target.value)}
              placeholder={scrapedListingUrl || 'Paste the listing URL…'}
              className="w-full text-xs font-mono border border-gray-300 rounded px-2 py-1.5 outline-none focus:border-blue-400 transition-colors"
            />
            <SaveIndicator status={listingStatus} />
          </div>
          {effectiveListingUrl && (
            <a href={effectiveListingUrl} target="_blank" rel="noreferrer"
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
