/**
 * Shared E2E settings. `node e2e/run.mjs` starts its own servers and sets these; the defaults
 * match the usual dev ports, so a single suite can also be run by hand against running servers:
 *   E2E_MOCK_URL  mock-mode frontend       (default http://localhost:5173)
 *   E2E_WEB_URL   API-mode frontend        (default http://localhost:5174)
 *   E2E_API_URL   backend API base         (default http://127.0.0.1:5000/api/v1)
 *   CHROME_PATH   Chrome / Chromium binary (auto-detected when unset)
 *   E2E_ARTIFACTS screenshots / logs       (default e2e/.artifacts)
 *
 * CHROME_ARGS keeps the tests independent of the internet: every host except the local test
 * servers fails DNS immediately, so external resources (e.g. the Google Fonts stylesheet in
 * index.html) fall back at once instead of hanging page loads while the network or DNS is down.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export const E2E_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
export const FRONTEND_DIR = path.resolve(E2E_DIR, '..')
export const MOCK_URL = process.env.E2E_MOCK_URL ?? 'http://localhost:5173'
export const WEB_URL = process.env.E2E_WEB_URL ?? 'http://localhost:5174'
export const API_URL = process.env.E2E_API_URL ?? 'http://127.0.0.1:5000/api/v1'
export const OUT = process.env.E2E_ARTIFACTS ?? path.join(E2E_DIR, '.artifacts')
fs.mkdirSync(OUT, { recursive: true })

const CANDIDATES = [
  process.env.CHROME_PATH,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
]
export const CHROME = CANDIDATES.find((p) => p && fs.existsSync(p))
if (!CHROME) throw new Error('Chrome / Chromium not found — set CHROME_PATH')

export const CHROME_ARGS = [
  '--no-first-run',
  '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1',
]
