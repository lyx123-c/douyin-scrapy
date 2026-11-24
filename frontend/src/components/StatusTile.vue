<script setup>
import { computed } from 'vue'

const props = defineProps({
  label: {
    type: String,
    required: true,
  },
  tag: {
    type: String,
    required: true,
  },
  title: {
    type: String,
    required: true,
  },
  description: {
    type: String,
    default: '',
  },
  tone: {
    type: String,
    default: 'info',
    validator: (value) => ['success', 'warning', 'danger', 'info'].includes(value),
  },
  loading: {
    type: Boolean,
    default: false,
  },
  icon: {
    type: Object,
    default: null,
  },
})

const toneStyles = {
  success: {
    icon: 'border-success/15 bg-success-soft text-success',
    badge: 'border-success/20 bg-success-soft text-success',
  },
  warning: {
    icon: 'border-warning/15 bg-warning-soft text-warning',
    badge: 'border-warning/20 bg-warning-soft text-warning',
  },
  danger: {
    icon: 'border-error/15 bg-error-soft text-error',
    badge: 'border-error/20 bg-error-soft text-error',
  },
  info: {
    icon: 'border-info/15 bg-info-soft text-info',
    badge: 'border-info/20 bg-info-soft text-info',
  },
}

const toneStyle = computed(() => toneStyles[props.tone] || toneStyles.info)
</script>

<template>
  <article class="flex min-h-[172px] min-w-0 flex-col p-5">
    <div class="flex items-start justify-between gap-3">
      <div class="flex min-w-0 items-center gap-2.5">
        <span
          v-if="icon"
          class="grid h-9 w-9 shrink-0 place-items-center rounded-control border text-[17px]"
          :class="toneStyle.icon"
          aria-hidden="true"
        >
          <ElIcon><component :is="icon" /></ElIcon>
        </span>
        <span class="text-[12px] font-semibold text-muted">{{ label }}</span>
      </div>
      <span
        class="inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold"
        :class="toneStyle.badge"
      >
        <span class="h-1.5 w-1.5 rounded-full bg-current" :class="{ 'animate-pulse': loading }" />
        {{ tag }}
      </span>
    </div>

    <div class="mt-5 flex flex-1 flex-col">
      <h3 class="text-[17px] font-semibold leading-snug text-ink">{{ title }}</h3>
      <p class="mt-1.5 max-w-[58ch] text-[12px] leading-5 text-muted">{{ description }}</p>
      <div v-if="$slots.actions" class="mt-4">
        <slot name="actions" />
      </div>
    </div>
  </article>
</template>
