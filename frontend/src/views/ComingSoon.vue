<script setup>
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <main class="mx-auto grid min-h-[70dvh] max-w-md place-items-center px-6 text-center">
    <div>
      <h1 class="text-2xl font-bold">這個功能還在開發中</h1>
      <p class="mt-2 text-ink-soft">目前的原型只包含首頁 Dashboard。</p>
      <button
        type="button"
        class="mt-6 min-h-12 rounded-full bg-care px-6 font-bold text-white hover:bg-care/90"
        @click="router.back()"
      >
        回上一頁
      </button>
    </div>
  </main>
</template>
