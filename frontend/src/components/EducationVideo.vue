<script setup>
import WidgetFrame from '@/components/WidgetFrame.vue'

/**
 * 衛教影片 on the patient home, right under the daily report entry. Static content: the MP4 is a
 * file in frontend/public/videos/ (served as-is by the static host, never bundled), so there is no
 * API, layout item or database row behind it. No autoplay; metadata only until the patient taps play.
 */
const SRC = '/videos/radiation-self-care.mp4'
</script>

<template>
  <WidgetFrame title="衛教影片" icon="play">
    <section aria-labelledby="education-video-title" data-education-video>
      <h3 id="education-video-title" class="mb-3 font-bold">放射線治療期間的自我照護</h3>
      <video
        class="block h-auto w-full max-w-full rounded-xl bg-ink/90"
        controls
        playsinline
        preload="metadata"
        aria-describedby="education-video-title"
      >
        <source :src="SRC" type="video/mp4" />
        您的瀏覽器無法播放這支影片。
      </video>
    </section>
  </WidgetFrame>
</template>
