<script setup>
defineProps({
  title: {
    type: String,
    required: true,
  },
  description: {
    type: String,
    default: '',
  },
})
</script>

<template>
  <header
    class="mb-6 flex flex-col gap-4 border-b border-line pb-5 min-[900px]:flex-row min-[900px]:items-start min-[900px]:justify-between"
  >
    <div class="min-w-0">
      <h1 class="text-[28px] font-semibold leading-tight tracking-[-0.025em] text-ink max-[760px]:text-[24px]">
        {{ title }}
      </h1>
      <p v-if="description" class="mt-1.5 max-w-[68ch] text-[13px] leading-6 text-muted">
        {{ description }}
      </p>
    </div>
    <div v-if="$slots.actions" class="flex flex-wrap items-center gap-3 min-[900px]:justify-end">
      <slot name="actions" />
    </div>
  </header>
</template>
