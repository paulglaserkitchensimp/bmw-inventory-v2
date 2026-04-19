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

function annotationsPlugin(): Plugin {
  return {
    name: 'annotations-api',
    configureServer(server) {
      server.middlewares.use('/api/annotations', (req, res) => {
        res.setHeader('Access-Control-Allow-Origin', '*')
        res.setHeader('Content-Type', 'application/json')

        if (req.method === 'GET') {
          try {
            const data = fs.existsSync(ANNOTATIONS_FILE)
              ? fs.readFileSync(ANNOTATIONS_FILE, 'utf-8')
              : '{}'
            res.end(data)
          } catch {
            res.end('{}')
          }

        } else if (req.method === 'POST') {
          let body = ''
          req.on('data', chunk => { body += chunk })
          req.on('end', () => {
            try {
              // Validate JSON before writing
              JSON.parse(body)
              fs.mkdirSync(path.dirname(ANNOTATIONS_FILE), { recursive: true })
              fs.writeFileSync(ANNOTATIONS_FILE, body, 'utf-8')
              res.statusCode = 200
              res.end('{"ok":true}')
            } catch {
              res.statusCode = 400
              res.end('{"ok":false}')
            }
          })

        } else {
          res.statusCode = 405
          res.end('{}')
        }
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), annotationsPlugin(), geocodePlugin()],
})
