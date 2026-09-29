<script setup>
defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 22 },
})

// 24×24 stroke icons. Always paired with visible text (ui-architecture.md §1.2).
const PATHS = {
  home: 'M3 11.5 12 4l9 7.5M5.5 9.5V20h13V9.5',
  calendar: 'M4 6.5h16v13H4zM4 10.5h16M8.5 4v4M15.5 4v4',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7.5V12l3 2',
  phone: 'M6.6 3.5h2.8l1.4 4.2-2 1.5a11 11 0 0 0 6 6l1.5-2 4.2 1.4v2.8a2 2 0 0 1-2.2 2A17 17 0 0 1 4.6 5.7a2 2 0 0 1 2-2.2z',
  alert: 'M12 4 2.8 19.5h18.4zM12 10v4.5M12 17.3v.2',
  bell: 'M6 16.5V11a6 6 0 1 1 12 0v5.5l1.5 2h-15zM10 20.5a2 2 0 0 0 4 0',
  thermometer: 'M10 14.2V5a2 2 0 1 1 4 0v9.2a4 4 0 1 1-4 0zM12 9v7',
  heart: 'M12 20s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 12 7.3 4.3 4.3 0 0 1 19.5 10c0 5.4-7.5 10-7.5 10z',
  pressure: 'M12 21a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM12 13l3.5-3.5M12 5V3',
  drop: 'M12 3.5s-6 6.6-6 11a6 6 0 0 0 12 0c0-4.4-6-11-6-11z',
  flask: 'M9 3.5h6M10 3.5v6.2L5.2 18a1.8 1.8 0 0 0 1.6 2.5h10.4a1.8 1.8 0 0 0 1.6-2.5L14 9.7V3.5M7.4 14.5h9.2',
  lungs: 'M12 4v8M12 12c-1.5 0-3-1.3-3-3M12 12c1.5 0 3-1.3 3-3M8.5 8C6 8 4 12 4 16.5 4 19 5.5 20 7 20s2.5-1 2.5-3V9.5M15.5 8C18 8 20 12 20 16.5c0 2.5-1.5 3.5-3 3.5s-2.5-1-2.5-3V9.5',
  scale: 'M5 5h14l-1.5 15h-11zM9.5 10a3.5 3.5 0 0 1 5 0M12 10v2',
  check: 'M4.5 12.5 9.5 17 19.5 7',
  clipboard: 'M8.5 4.5h7v3h-7zM8.5 6H5.5v14.5h13V6h-3M8.5 12h7M8.5 16h5',
  chart: 'M4 4v16h16M7.5 15l3.5-4 3 2.5 5-6.5',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4.5 20.5a7.5 7.5 0 0 1 15 0',
  pill: 'M9.2 20.3a4.5 4.5 0 0 1-6.4-6.4l7-7a4.5 4.5 0 0 1 6.4 6.4zM6.3 10.3l6.4 6.4',
  hand: 'M7 11V6.5a1.5 1.5 0 0 1 3 0V11V4.5a1.5 1.5 0 0 1 3 0V11V6a1.5 1.5 0 0 1 3 0v8a6.5 6.5 0 0 1-12.2 3.1L2.9 13a1.5 1.5 0 0 1 2.5-1.6L7 13.5',
  shield: 'M12 3.5 5 6v5.5c0 4.3 3 7.6 7 9 4-1.4 7-4.7 7-9V6z',
  chevron: 'M9.5 5.5 16 12l-6.5 6.5',
  users: 'M9 11.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2.5 20a6.5 6.5 0 0 1 13 0M16 4.8a3.5 3.5 0 0 1 0 6.4M18.5 14.5A6.5 6.5 0 0 1 21.5 20',
}
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <path :d="PATHS[name]" />
  </svg>
</template>
