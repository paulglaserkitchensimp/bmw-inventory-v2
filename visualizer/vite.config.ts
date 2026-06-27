import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import path from 'node:path'
import type { Plugin } from 'vite'

const ANNOTATIONS_FILE = path.resolve(__dirname, 'public/data/annotations.json')
const GEOCACHE_FILE    = path.resolve(__dirname, 'public/data/geocache.json')

// Nominatim requires a descriptive User-Agent per its usage policy.
const NOMINATIM_UA = 'bmw-visualizer/0.1 (local dev tool)'

function readJsonSafe<T>(file: string, fallback: T): T {
  try {
    return fs.existsSync(file)
      ? JSON.parse(fs.readFileSync(file, 'utf-8'))
      : fallback
  } catch {
    return fallback
  }
}

function writeJson(file: string, data: unknown) {
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, JSON.stringify(data, null, 2), 'utf-8')
}

// Proxies Nominatim geocoding through the Vite dev server to avoid CORS and
// respect the usage policy (User-Agent header, ≤1 rps). Results are cached on
// disk in public/data/geocache.json keyed by "City, ST" so the full dealer
// list is geocoded exactly once across all browsers/sessions.
function geocodePlugin(): Plugin {
  let lastFetch = 0
  return {
    name: 'geocode-api',
    configureServer(server) {
      server.middlewares.use('/api/geocode', async (req, res) => {
        res.setHeader('Access-Control-Allow-Origin', '*')
        res.setHeader('Content-Type', 'application/json')

        const url   = new URL(req.url ?? '', 'http://localhost')
        const city  = url.searchParams.get('city')?.trim()
        const state = url.searchParams.get('state')?.trim()
        if (!city || !state) {
          res.statusCode = 400
          res.end(JSON.stringify({ error: 'city and state required' }))
          return
        }

        const key   = `${city}, ${state}`
        const cache = readJsonSafe<Record<string, [number, number] | null>>(GEOCACHE_FILE, {})
        if (key in cache) {
          res.end(JSON.stringify({ coords: cache[key], cached: true }))
          return
        }

        // Respect Nominatim's ~1 rps limit across all callers.
        const wait = Math.max(0, 1100 - (Date.now() - lastFetch))
        if (wait > 0) await new Promise(r => setTimeout(r, wait))
        lastFetch = Date.now()

        try {
          const q = encodeURIComponent(`${city}, ${state}, USA`)
          const r = await fetch(
            `https://nominatim.openstreetmap.org/search?q=${q}&format=json&limit=1`,
            { headers: { 'User-Agent': NOMINATIM_UA, 'Accept-Language': 'en' } }
          )
          if (!r.ok) {
            res.statusCode = r.status
            res.end(JSON.stringify({ error: `nominatim ${r.status}` }))
            return
          }
          const data = await r.json() as Array<{ lat: string; lon: string }>
          const coords: [number, number] | null = data[0]
            ? [parseFloat(data[0].lat), parseFloat(data[0].lon)]
            : null

          cache[key] = coords
          writeJson(GEOCACHE_FILE, cache)
          res.end(JSON.stringify({ coords, cached: false }))
        } catch (e) {
          res.statusCode = 502
          res.end(JSON.stringify({ error: String(e) }))
        }
      })
    },
  }
}

type AnnotationEntry = {
  tag?: string | null
  comment?: string
  leasehackrUrl?: string
}

function isEmptyEntry(e: unknown): boolean {
  if (!e || typeof e !== 'object') return true
  const { tag, comment, leasehackrUrl } = e as AnnotationEntry
  return !tag && !comment && !leasehackrUrl
}

/**
 * Annotations API.
 *
 *   GET  /api/annotations        → whole map (for initial load + poll-based sync)
 *   POST /api/annotations/:vin   → merge a single VIN into the on-disk map
 *                                   (body = the entry; empty/missing fields
 *                                   cause the VIN to be removed entirely)
 *
 * Per-VIN writes avoid the lost-update race that full-map POSTs caused across
 * multiple open browsers: browser A saving VIN X can no longer clobber browser
 * B's unrelated edit on VIN Y, since the server reads the latest file, merges
 * just the one VIN, and writes it back.
 *
 * Writes are funnelled through a Promise queue so concurrent POSTs on the same
 * process don't interleave their read → merge → write cycles.
 */
function annotationsPlugin(): Plugin {
  let writeQueue: Promise<void> = Promise.resolve()
  const enqueueWrite = (fn: () => void): Promise<void> => {
    const next = writeQueue.then(fn, fn)
    writeQueue = next.catch(() => {})
    return next
  }

  return {
    name: 'annotations-api',
    configureServer(server) {
      server.middlewares.use('/api/annotations', (req, res) => {
        res.setHeader('Access-Control-Allow-Origin', '*')
        res.setHeader('Content-Type', 'application/json')

        // Vite/Connect strips the '/api/annotations' mount prefix before handing
        // us the request, so req.url is '/' for the whole-map endpoint and
        // '/<vin>' for per-VIN writes.
        const subPath = (req.url ?? '/').split('?')[0]
        const vin = subPath.replace(/^\/+/, '').trim()

        if (req.method === 'GET' && !vin) {
          try {
            const data = fs.existsSync(ANNOTATIONS_FILE)
              ? fs.readFileSync(ANNOTATIONS_FILE, 'utf-8')
              : '{}'
            res.end(data)
          } catch {
            res.end('{}')
          }
          return
        }

        if (req.method === 'POST' && vin) {
          let body = ''
          req.on('data', chunk => { body += chunk })
          req.on('end', async () => {
            let parsed: unknown
            try {
              parsed = body ? JSON.parse(body) : null
            } catch {
              res.statusCode = 400
              res.end('{"ok":false,"error":"invalid json"}')
              return
            }
            try {
              await enqueueWrite(() => {
                const current = readJsonSafe<Record<string, AnnotationEntry>>(ANNOTATIONS_FILE, {})
                if (isEmptyEntry(parsed)) {
                  delete current[vin]
                } else {
                  current[vin] = parsed as AnnotationEntry
                }
                writeJson(ANNOTATIONS_FILE, current)
              })
              res.statusCode = 200
              res.end('{"ok":true}')
            } catch (e) {
              res.statusCode = 500
              res.end(JSON.stringify({ ok: false, error: String(e) }))
            }
          })
          return
        }

        res.statusCode = 405
        res.end('{}')
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), annotationsPlugin(), geocodePlugin()],
})
