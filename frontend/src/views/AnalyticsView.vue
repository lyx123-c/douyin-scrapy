<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'

import { getAnalytics } from '../api/analytics'
import { getVideoSummary } from '../api/videos'
import AppHeader from '../components/AppHeader.vue'

const summary = reactive({
  total: 0,
  downloaded_total: 0,
  pending_total: 0,
  waiting_tasks: 0,
  running_tasks: 0,
  failed_tasks: 0,
})

const analyticsItems = ref([])
const analyticsTotal = ref(0)
const loading = ref(true)
const summaryLoaded = ref(false)
const summaryError = ref('')
const analyticsError = ref('')
const lastUpdatedAt = ref(null)

let requestController = null
let requestSequence = 0
let disposed = false

const errorMessage = computed(() =>
  [...new Set([summaryError.value, analyticsError.value].filter(Boolean))].join('；'),
)
const dataMayBeStale = computed(() => Boolean(errorMessage.value && lastUpdatedAt.value))
const lastUpdatedLabel = computed(() => {
  if (!lastUpdatedAt.value) return '尚未加载'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit',
    minute: '2-digit', second: '2-digit', hour12: false,
  }).format(lastUpdatedAt.value)
})

const summaryCards = computed(() => [
  { key: 'total', label: '视频库总数', value: summary.total, tone: 'ink' },
  { key: 'downloaded', label: '已下载', value: summary.downloaded_total, tone: 'teal' },
  { key: 'pending', label: '待下载', value: summary.pending_total, tone: 'amber' },
  { key: 'failed', label: '下载失败', value: summary.failed_tasks, tone: 'coral' },
])

const metricItems = computed(() =>
  analyticsItems.value.map((item) => ({
    ...item,
    digg: parseMetric(item.digg_count),
    comment: parseMetric(item.comment_count),
    collect: parseMetric(item.collect_count),
    share: parseMetric(item.share_count),
  })),
)

const topDiggItems = computed(() =>
  [...metricItems.value]
    .filter((item) => Number.isFinite(item.digg))
    .sort((left, right) => right.digg - left.digg)
    .slice(0, 10),
)
const maxTopDigg = computed(() => Math.max(1, ...topDiggItems.value.map((item) => item.digg)))

const scatterScale = computed(() => {
  const valid = metricItems.value.filter(
    (item) => Number.isFinite(item.digg) && Number.isFinite(item.comment),
  )
  return {
    maxX: Math.max(1, ...valid.map((item) => item.digg)),
    maxY: Math.max(1, ...valid.map((item) => item.comment)),
    width: 760,
    height: 340,
    margin: { top: 20, right: 22, bottom: 46, left: 58 },
  }
})

const scatterPoints = computed(() => {
  const valid = metricItems.value.filter(
    (item) => Number.isFinite(item.digg) && Number.isFinite(item.comment),
  )
  if (!valid.length) return []
  const scale = scatterScale.value
  const plotWidth = scale.width - scale.margin.left - scale.margin.right
  const plotHeight = scale.height - scale.margin.top - scale.margin.bottom
  return valid.map((item) => ({
    ...item,
    cx: scale.margin.left + (item.digg / scale.maxX) * plotWidth,
    cy: scale.margin.top + plotHeight - (item.comment / scale.maxY) * plotHeight,
    tooltip: `${item.video_title || '无标题'} · 点赞 ${formatMetric(item.digg)} · 评论 ${formatMetric(item.comment)}`,
  }))
})

const scatterTicks = computed(() => {
  const { maxX, maxY, width, height, margin } = scatterScale.value
  const plotWidth = width - margin.left - margin.right
  const plotHeight = height - margin.top - margin.bottom
  return {
    x: Array.from({ length: 5 }, (_, index) => ({
      x: margin.left + plotWidth * (index / 4), value: Math.round(maxX * (index / 4)),
    })),
    y: Array.from({ length: 5 }, (_, index) => ({
      y: margin.top + plotHeight * (1 - index / 4), value: Math.round(maxY * (index / 4)),
    })),
  }
})

const authorStats = computed(() => {
  const grouped = new Map()
  for (const item of metricItems.value) {
    if (!item.author_name || !Number.isFinite(item.digg) || !Number.isFinite(item.comment)) continue
    const current = grouped.get(item.author_name) || {
      author: item.author_name, diggTotal: 0, commentTotal: 0, count: 0,
    }
    current.diggTotal += item.digg
    current.commentTotal += item.comment
    current.count += 1
    grouped.set(item.author_name, current)
  }
  return [...grouped.values()]
    .map((item) => ({
      author: item.author,
      avgDigg: item.diggTotal / item.count,
      avgComment: item.commentTotal / item.count,
    }))
    .sort((left, right) => right.avgDigg - left.avgDigg)
})
const maxAuthorAverage = computed(() =>
  Math.max(1, ...authorStats.value.flatMap((item) => [item.avgDigg, item.avgComment])),
)
const hasMultipleAuthors = computed(() => authorStats.value.length > 1)

function parseMetric(value) {
  const normalized = String(value ?? '').replace(/,/g, '').trim()
  if (!normalized) return Number.NaN
  const number = Number(normalized)
  return Number.isFinite(number) ? number : Number.NaN
}

function formatMetric(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 }).format(number)
}

function barWidth(value, maximum) {
  return `${Math.max(0, Math.min(100, (Number(value) / maximum) * 100))}%`
}

async function refreshData({ manual = false } = {}) {
  if (disposed) return
  if (requestController && !manual) return
  if (manual && requestController) requestController.abort()
  const controller = new AbortController()
  requestController = controller
  const requestId = ++requestSequence
  loading.value = true
  if (manual) {
    summaryError.value = ''
    analyticsError.value = ''
  }
  try {
    const [summaryResult, analyticsResult] = await Promise.allSettled([
      getVideoSummary({ signal: controller.signal }),
      getAnalytics({ signal: controller.signal }),
    ])
    if (requestId !== requestSequence) return
    if (summaryResult.status === 'fulfilled') {
      Object.assign(summary, {
        total: summaryResult.value.data?.total || 0,
        downloaded_total: summaryResult.value.data?.downloaded_total || 0,
        pending_total: summaryResult.value.data?.pending_total || 0,
        waiting_tasks: summaryResult.value.data?.waiting_tasks || 0,
        running_tasks: summaryResult.value.data?.running_tasks || 0,
        failed_tasks: summaryResult.value.data?.failed_tasks || 0,
      })
      summaryError.value = ''
      summaryLoaded.value = true
    } else if (summaryResult.reason.code !== 'REQUEST_CANCELED') {
      summaryError.value = summaryResult.reason.message || '视频汇总加载失败。'
    }
    if (analyticsResult.status === 'fulfilled') {
      analyticsItems.value = analyticsResult.value.data?.items || []
      analyticsTotal.value = analyticsResult.value.data?.total || 0
      analyticsError.value = ''
    } else if (analyticsResult.reason.code !== 'REQUEST_CANCELED') {
      analyticsError.value = analyticsResult.reason.message || '统计数据加载失败。'
      if (!lastUpdatedAt.value) {
        analyticsItems.value = []
        analyticsTotal.value = 0
      }
    }
    if (summaryResult.status === 'fulfilled' || analyticsResult.status === 'fulfilled') {
      lastUpdatedAt.value = new Date()
    }
  } finally {
    if (requestId === requestSequence) {
      requestController = null
      loading.value = false
    }
  }
}

onMounted(() => refreshData({ manual: true }))
onUnmounted(() => {
  disposed = true
  requestController?.abort()
})
</script>

<template>
  <div class="app-shell">
    <AppHeader />

    <main class="workspace workspace--analytics">
      <section class="analytics-heading">
        <div>
          <p class="section-kicker">下载数据</p>
          <h2>统计分析</h2>
          <p class="analytics-subtitle">基于已下载视频的点赞、评论和作者数据进行统计。</p>
        </div>
        <div class="heading-actions">
          <ElTag type="info" effect="plain">统计样本 {{ analyticsTotal }} 条</ElTag>
          <span class="last-updated">更新于 {{ lastUpdatedLabel }}</span>
          <ElButton :icon="Refresh" :loading="loading" @click="refreshData({ manual: true })">
            刷新
          </ElButton>
        </div>
      </section>

      <ElAlert
        v-if="errorMessage"
        class="analytics-alert"
        title="统计数据请求失败"
        :description="dataMayBeStale ? `${errorMessage} 当前数据可能已过期。` : errorMessage"
        type="error"
        show-icon
        :closable="false"
      />

      <section class="summary-grid" aria-label="统计概览">
        <article
          v-for="card in summaryCards"
          :key="card.key"
          class="summary-card"
          :data-tone="card.tone"
        >
          <span>{{ card.label }}</span>
          <ElSkeleton v-if="loading && !summaryLoaded" animated :rows="0" />
          <strong v-else>{{ card.value }}</strong>
        </article>
      </section>

      <div class="queue-meta">
        <span>队列待处理 <strong>{{ summary.waiting_tasks }}</strong></span>
        <span>正在执行 <strong>{{ summary.running_tasks }}</strong></span>
        <span>下载失败 <strong>{{ summary.failed_tasks }}</strong></span>
      </div>

      <section v-if="analyticsTotal > 0" class="charts-layout" v-loading="loading">
        <article class="chart-card chart-card--top">
          <header class="chart-heading">
            <div>
              <h3>点赞量 Top 10 视频</h3>
              <p>按点赞量降序排列。</p>
            </div>
            <span>{{ topDiggItems.length }} 条</span>
          </header>
          <div v-if="topDiggItems.length" class="top-list">
            <div v-for="(item, index) in topDiggItems" :key="item.video_id" class="top-row">
              <span class="top-rank">{{ index + 1 }}</span>
              <span class="top-title" :title="item.video_title">{{ item.video_title || '无标题' }}</span>
              <div class="top-bar-track">
                <div class="top-bar" :style="{ width: barWidth(item.digg, maxTopDigg) }" />
              </div>
              <strong>{{ formatMetric(item.digg) }}</strong>
            </div>
          </div>
          <div v-else class="chart-empty">暂无可用于点赞量排行的数据。</div>
        </article>

        <article class="chart-card chart-card--scatter">
          <header class="chart-heading">
            <div>
              <h3>点赞量与评论量相关性</h3>
              <p>每个点代表一条已下载视频。</p>
            </div>
            <span>{{ scatterPoints.length }} 个点</span>
          </header>
          <div v-if="scatterPoints.length" class="scatter-wrap">
            <svg
              class="scatter-chart"
              :viewBox="`0 0 ${scatterScale.width} ${scatterScale.height}`"
              role="img"
              aria-label="点赞量与评论量散点图"
            >
              <line
                v-for="tick in scatterTicks.y"
                :key="`grid-y-${tick.value}`"
                :x1="scatterScale.margin.left"
                :x2="scatterScale.width - scatterScale.margin.right"
                :y1="tick.y"
                :y2="tick.y"
                class="grid-line"
              />
              <line
                :x1="scatterScale.margin.left"
                :x2="scatterScale.margin.left"
                :y1="scatterScale.margin.top"
                :y2="scatterScale.height - scatterScale.margin.bottom"
                class="axis-line"
              />
              <line
                :x1="scatterScale.margin.left"
                :x2="scatterScale.width - scatterScale.margin.right"
                :y1="scatterScale.height - scatterScale.margin.bottom"
                :y2="scatterScale.height - scatterScale.margin.bottom"
                class="axis-line"
              />
              <text
                v-for="tick in scatterTicks.x"
                :key="`x-${tick.value}`"
                :x="tick.x"
                :y="scatterScale.height - 20"
                text-anchor="middle"
                class="tick-label"
              >{{ formatMetric(tick.value) }}</text>
              <text
                v-for="tick in scatterTicks.y"
                :key="`y-${tick.value}`"
                :x="scatterScale.margin.left - 10"
                :y="tick.y + 4"
                text-anchor="end"
                class="tick-label"
              >{{ formatMetric(tick.value) }}</text>
              <circle
                v-for="point in scatterPoints"
                :key="point.video_id"
                :cx="point.cx"
                :cy="point.cy"
                r="5"
                class="scatter-point"
              >
                <title>{{ point.tooltip }}</title>
              </circle>
              <text :x="scatterScale.width / 2" :y="scatterScale.height - 3" text-anchor="middle" class="axis-title">点赞量</text>
              <text x="14" y="170" text-anchor="middle" transform="rotate(-90 14 170)" class="axis-title">评论量</text>
            </svg>
          </div>
          <div v-else class="chart-empty">暂无有效的点赞与评论数据。</div>
        </article>

        <article class="chart-card chart-card--authors">
          <header class="chart-heading">
            <div>
              <h3>各作者平均点赞量与评论量对比</h3>
              <p>按作者平均点赞量降序排列；仅作者数量大于 1 时显示。</p>
            </div>
            <span>{{ authorStats.length }} 位作者</span>
          </header>
          <div v-if="hasMultipleAuthors" class="author-chart">
            <div v-for="author in authorStats" :key="author.author" class="author-row">
              <span class="author-name" :title="author.author">{{ author.author }}</span>
              <div class="author-bars">
                <div class="author-bar-line">
                  <div class="author-bar author-bar--likes" :style="{ width: barWidth(author.avgDigg, maxAuthorAverage) }" />
                  <span>{{ formatMetric(author.avgDigg) }}</span>
                </div>
                <div class="author-bar-line">
                  <div class="author-bar author-bar--comments" :style="{ width: barWidth(author.avgComment, maxAuthorAverage) }" />
                  <span>{{ formatMetric(author.avgComment) }}</span>
                </div>
              </div>
            </div>
            <div class="author-legend">
              <span><i class="legend-dot legend-dot--likes" />平均点赞量</span>
              <span><i class="legend-dot legend-dot--comments" />平均评论量</span>
            </div>
          </div>
          <div v-else class="chart-empty">作者数量不足 2，原统计图不生成。</div>
        </article>
      </section>

      <section v-else-if="!loading" class="empty-state">
        <h3>暂无下载统计数据</h3>
        <p>下载完成并写入统计记录后，这里会显示点赞、评论和作者统计图。</p>
      </section>
    </main>
  </div>
</template>

<style scoped>
.workspace--analytics {
  width: min(1320px, calc(100% - 40px));
}

.analytics-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 20px;
}

.analytics-heading .section-kicker {
  color: #748187;
}

.analytics-heading h2 {
  margin: 0;
  color: var(--ink);
  font-size: 28px;
  letter-spacing: 0;
}

.analytics-subtitle {
  margin: 7px 0 0;
  color: var(--muted);
  font-size: 13px;
}

.heading-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.last-updated {
  color: var(--muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.analytics-alert {
  margin-bottom: 16px;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.summary-card {
  min-height: 112px;
  padding: 17px 18px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 8px;
}

.summary-card span {
  color: var(--muted);
  font-size: 12px;
}

.summary-card strong {
  display: block;
  margin-top: 9px;
  color: var(--ink);
  font-size: 30px;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.summary-card[data-tone="teal"] strong { color: var(--teal); }
.summary-card[data-tone="amber"] strong { color: #b87915; }
.summary-card[data-tone="coral"] strong { color: #c94f4a; }

.queue-meta {
  display: flex;
  gap: 22px;
  padding: 11px 2px 0;
  color: var(--muted);
  font-size: 12px;
}

.queue-meta strong {
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}

.charts-layout {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-top: 16px;
}

.chart-card {
  padding: 18px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 8px;
}

.chart-card--authors {
  grid-column: 1 / -1;
}

.chart-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 16px;
}

.chart-heading h3,
.empty-state h3 {
  margin: 0;
  color: var(--ink);
  font-size: 17px;
  letter-spacing: 0;
}

.chart-heading p,
.empty-state p {
  margin: 5px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.chart-heading > span {
  color: #7c878c;
  font-size: 11px;
  white-space: nowrap;
}

.top-list {
  display: grid;
  gap: 10px;
}

.top-row {
  display: grid;
  grid-template-columns: 22px minmax(130px, 1.1fr) minmax(100px, 1fr) 76px;
  align-items: center;
  gap: 8px;
}

.top-rank {
  color: #8a959a;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.top-title {
  overflow: hidden;
  color: #39484f;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.top-bar-track {
  height: 8px;
  overflow: hidden;
  background: #edf1f0;
  border-radius: 4px;
}

.top-bar {
  height: 100%;
  background: #2a9488;
  border-radius: 4px;
}

.top-row strong {
  color: #506067;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.scatter-wrap {
  overflow: hidden;
}

.scatter-chart {
  display: block;
  width: 100%;
  min-height: 280px;
}

.grid-line {
  stroke: #e4e9e7;
  stroke-dasharray: 4 4;
}

.axis-line { stroke: #aeb9b6; }
.tick-label { fill: #7d898e; font-size: 10px; }
.axis-title { fill: #5f6d73; font-size: 11px; }

.scatter-point {
  fill: #e85d57;
  fill-opacity: 0.62;
  stroke: #ffffff;
  stroke-width: 1.5;
}

.author-chart {
  max-height: 520px;
  padding-right: 4px;
  overflow-y: auto;
}

.author-row {
  display: grid;
  grid-template-columns: minmax(120px, 180px) minmax(260px, 1fr);
  align-items: center;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid #eef1f0;
}

.author-name {
  overflow: hidden;
  color: #44535a;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.author-bars { display: grid; gap: 5px; }

.author-bar-line {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 64px;
  align-items: center;
  gap: 8px;
}

.author-bar-line span {
  color: #6c797f;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.author-bar {
  height: 7px;
  min-width: 2px;
  border-radius: 4px;
}

.author-bar--likes,
.legend-dot--likes { background: #2a9488; }
.author-bar--comments,
.legend-dot--comments { background: #e69937; }

.author-legend {
  display: flex;
  gap: 18px;
  padding-top: 12px;
  color: #6b777c;
  font-size: 11px;
}

.author-legend span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.legend-dot {
  width: 8px;
  height: 8px;
  border-radius: 2px;
}

.chart-empty {
  display: grid;
  min-height: 180px;
  place-items: center;
  color: var(--muted);
  font-size: 12px;
  text-align: center;
  background: #f8faf9;
  border: 1px dashed #d6dfdc;
  border-radius: 6px;
}

.empty-state {
  margin-top: 16px;
  padding: 48px 24px;
  text-align: center;
  background: var(--paper);
  border: 1px dashed #d4ddda;
  border-radius: 8px;
}

@media (max-width: 980px) {
  .charts-layout {
    grid-template-columns: 1fr;
  }

  .chart-card--authors {
    grid-column: auto;
  }
}

@media (max-width: 720px) {
  .workspace--analytics {
    width: min(100% - 24px, 1320px);
  }

  .analytics-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .heading-actions {
    width: 100%;
    flex-wrap: wrap;
  }

  .summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .summary-card {
    min-height: 100px;
  }

  .queue-meta {
    flex-wrap: wrap;
    gap: 8px 18px;
  }

  .chart-card {
    padding: 14px;
  }

  .top-row {
    grid-template-columns: 20px minmax(88px, 1fr) 78px 62px;
  }

  .scatter-chart {
    min-height: 230px;
  }

  .author-row {
    grid-template-columns: 92px minmax(180px, 1fr);
  }
}
</style>
