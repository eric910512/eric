// Local stand-in for a Render Static Site (used by `node e2e/run.mjs --prod`), with the same rules
// as render.yaml: /api/* is rewritten (proxied) to the backend — the browser stays on this one
// origin, so the refresh-token cookie is first-party —; any other path serves the file if it
// exists, otherwise /index.html (SPA rewrite), plus the site's headers.
import fs from 'node:fs'
import http from 'node:http'
import path from 'node:path'

const [root, port, apiTarget] = [path.resolve(process.argv[2]), Number(process.argv[3] || 5174), process.argv[4] ?? 'http://127.0.0.1:5000']
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon', '.json': 'application/json', '.woff2': 'font/woff2' }
function proxy(req, res) {
  const target = new URL(req.url, apiTarget)
  const upstream = http.request(target, {
    method: req.method,
    headers: { ...req.headers, host: target.host, 'x-forwarded-for': req.socket.remoteAddress, 'x-forwarded-proto': 'http' },
  }, (up) => {
    res.writeHead(up.statusCode, up.headers) // Set-Cookie passes through unchanged (host-only → this origin)
    up.pipe(res)
  })
  upstream.on('error', () => { res.writeHead(502); res.end() })
  req.pipe(upstream)
}

http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) return proxy(req, res)
  const url = decodeURIComponent(new URL(req.url, 'http://x').pathname)
  let file = path.join(root, url)
  if (!file.startsWith(root)) { res.writeHead(400); return res.end() }
  let rewritten = false
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) { file = path.join(root, 'index.html'); rewritten = true }
  const headers = { 'Content-Type': TYPES[path.extname(file)] ?? 'application/octet-stream', 'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'strict-origin-when-cross-origin', 'X-Frame-Options': 'DENY', 'X-Rewritten': String(rewritten) }
  if (url.startsWith('/assets/') && !rewritten) headers['Cache-Control'] = 'public, max-age=31536000, immutable'
  res.writeHead(200, headers)
  fs.createReadStream(file).pipe(res)
}).listen(port, '127.0.0.1', () => console.log(`static ${root} on :${port}`))
