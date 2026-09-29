/**
 * Timestamps for mock mode — same contract as the API: ISO 8601 UTC with milliseconds
 * ("2026-09-25T06:10:00.123Z", i.e. Date#toISOString), never truncated to seconds.
 */
export const nowIso = () => new Date().toISOString()

/** Newest-first comparator for ISO timestamps (by instant, not by string). */
export const byTimeDesc = (a, b) => Date.parse(b) - Date.parse(a)

/**
 * Time for a new handling step: strictly later (≥ 1 ms) than every step already taken, even
 * when the clock has not advanced — the same rule as the backend. Steps sharing a timestamp
 * therefore always mean "done in one operation" (quick resolve).
 */
export function afterSteps(previous, now = nowIso()) {
  const latest = Math.max(...previous.filter(Boolean).map((t) => Date.parse(t)), -Infinity)
  return Date.parse(now) > latest ? now : new Date(latest + 1).toISOString()
}
