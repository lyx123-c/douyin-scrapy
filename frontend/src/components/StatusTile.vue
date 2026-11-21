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
})

const tagType = computed(() => props.tone)
</script>

<template>
  <article class="status-tile" :data-tone="tone">
    <div class="status-tile__header">
      <span class="status-tile__label">{{ label }}</span>
      <ElTag :type="tagType" effect="plain" size="small">{{ tag }}</ElTag>
    </div>
    <div class="status-tile__body">
      <div class="status-tile__title">
        <span class="status-dot" :class="{ 'is-loading': loading }" />
        {{ title }}
      </div>
      <p class="status-tile__description">{{ description }}</p>
    </div>
  </article>
</template>
