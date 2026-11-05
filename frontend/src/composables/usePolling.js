import { onBeforeUnmount, onMounted } from 'vue'

/**
 * Run `task` now and then every `intervalMs` while the page is in the foreground.
 * - never overlapping: the next run is scheduled only after the previous one settled (a slow first
 *   request, e.g. a sleeping API waking up, does not pile up more requests behind it)
 * - paused while the tab is hidden; runs at once when it becomes visible again
 * - stopped when the component unmounts (no timers or listeners left behind)
 * Returns `{ refresh }` to run the task immediately (it joins a run that is already in flight).
 */
export function usePolling(task, intervalMs) {
  let timer = null
  let inflight = null
  let stopped = false

  const visible = () => typeof document === 'undefined' || document.visibilityState === 'visible'

  function schedule() {
    clearTimeout(timer)
    timer = null
    if (!stopped && visible()) timer = setTimeout(run, intervalMs)
  }

  function run() {
    if (stopped) return Promise.resolve()
    if (!inflight) {
      inflight = Promise.resolve()
        .then(task)
        .catch(() => {}) // the task reports its own errors; polling keeps going
        .finally(() => {
          inflight = null
          schedule()
        })
    }
    return inflight
  }

  function onVisibility() {
    if (visible()) run()
    else {
      clearTimeout(timer)
      timer = null
    }
  }

  onMounted(() => {
    document.addEventListener('visibilitychange', onVisibility)
    run()
  })
  onBeforeUnmount(() => {
    stopped = true
    clearTimeout(timer)
    document.removeEventListener('visibilitychange', onVisibility)
  })

  return { refresh: run }
}
