import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import path from 'node:path'
import type { Plugin } from 'vite'

const ANNOTATIONS_FILE = path.resolve(__dirname, 'public/data/annotations.json')

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
  plugins: [react(), annotationsPlugin()],
})
