<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { Monitor } from '@element-plus/icons-vue'

const route = useRoute()
const sectionTitle = computed(() => route.meta.title || '工作台')
</script>

<template>
  <header
    class="z-10 flex min-h-14 shrink-0 items-center justify-between border-b border-line bg-panel px-6 shadow-[0_1px_2px_rgba(17,25,31,0.035)] max-[760px]:sticky max-[760px]:top-0 max-[760px]:z-20 max-[760px]:min-h-12 max-[760px]:px-4"
  >
    <div class="flex min-w-0 items-center gap-2.5 text-[12px] text-muted-strong">
      <ElIcon class="shrink-0 text-[15px] text-brand"><Monitor /></ElIcon>
      <span class="whitespace-nowrap">本地工作台</span>
      <span class="mx-1 h-3.5 w-px bg-line" aria-hidden="true" />
      <strong class="truncate font-semibold text-ink">{{ sectionTitle }}</strong>
    </div>
    <div class="flex items-center gap-2 whitespace-nowrap text-[11px] text-muted-strong max-[760px]:hidden" aria-label="运行环境">
      <span>Windows Desktop</span>
      <span class="h-1 w-1 rounded-full bg-muted-strong/60" aria-hidden="true" />
      <span>Vue 3 + FastAPI</span>
    </div>
  </header>
</template>
