<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import {
  Close,
  Plus,
  Refresh,
  RefreshLeft,
  Search,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

import { addDownloadTasks } from '../api/downloads'
import { getVideoSummary, getVideos } from '../api/videos'
import PageHeader from '../components/PageHeader.vue'

const STATUS_OPTIONS = [
  { value: 'all', label: '全部状态' },
  { value: 'pending', label: '待下载' },
  { value: 'downloaded', label: '已下载' },
  { value: 'queued', label: '队列中' },
  { value: 'failed', label: '下载失败' },
]

const SORT_OPTIONS = [
  { value: 'library', label: '视频库顺序' },
  { value: 'video_id', label: '视频 ID' },
  { value: 'title', label: '标题' },
]

const ORDER_OPTIONS = [
  { value: 'asc', label: '升序' },
  { value: 'desc', label: '降序' },
]

const PAGE_SIZE_OPTIONS = [20, 50, 100, 200]

const keywordInput = ref('')
const statusFilter = ref('all')
const sortBy = ref('library')
const sortOrder = ref('asc')
const pageSize = ref(50)
const videoTableRef = ref(null)
const selectedRows = ref([])
const queueSubmitting = ref(false)

const pageState = reactive({
  items: [],
  total: 0,
  page: 1,
  page_size: 50,
  total_pages: 0,
})

const summary = reactive({
  total: 0,
  downloaded_total: 0,
  pending_total: 0,
  waiting_tasks: 0,
  running_tasks: 0,
  failed_tasks: 0,
})

const loading = ref(false)
const summaryLoading = ref(false)
const summaryLoaded = ref(false)
const videoError = ref('')
const summaryError = ref('')
const lastLoadedAt = ref(null)

let videoRequestId = 0
let summaryRequestId = 0

const errorMessage = computed(() =>
  [...new Set([summaryError.value, videoError.value].filter(Boolean))].join('；'),
)

const hasFilters = computed(
  () =>
    keywordInput.value.trim() !== '' ||
    statusFilter.value !== 'all' ||
    sortBy.value !== 'library' ||
    sortOrder.value !== 'asc' ||
    pageSize.value !== 50,
)

const currentRangeLabel = computed(() => {
  if (!pageState.total) return '暂无记录'
  const start = (pageState.page - 1) * pageState.page_size + 1
  const end = Math.min(pageState.page * pageState.page_size, pageState.total)
  return `显示 ${start}-${end} / ${pageState.total} 条`
})

const lastLoadedLabel = computed(() => {
  if (!lastLoadedAt.value) return '尚未加载'
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(lastLoadedAt.value)
})

const summaryCards = computed(() => [
  {
    key: 'total',
    label: '视频总数',
    value: summary.total,
    note: '视频库记录总量',
    tone: 'ink',
  },
  {
    key: 'downloaded',
    label: '已下载',
    value: summary.downloaded_total,
    note: '存在下载记录',
    tone: 'teal',
  },
  {
    key: 'pending',
    label: '待下载',
    value: summary.pending_total,
    note: '尚未形成下载记录',
    tone: 'amber',
  },
])

const selectedCount = computed(() => selectedRows.value.length)

function statusTagType(status) {
  const typeMap = {
    downloaded: 'success',
    success: 'success',
    skipped: 'info',
    pending: 'info',
    queued: 'warning',
    waiting: 'warning',
    interrupted: 'warning',
    running: 'primary',
    failed: 'danger',
  }
  return typeMap[status] || 'info'
}

async function loadVideos() {
  const requestId = ++videoRequestId
  loading.value = true
  videoError.value = ''
  resetSelection()

  try {
    const response = await getVideos({
      keyword: keywordInput.value.trim(),
      status: statusFilter.value,
      page: pageState.page,
      pageSize: pageSize.value,
      sortBy: sortBy.value,
      sortOrder: sortOrder.value,
    })
    if (requestId !== videoRequestId) return

    pageState.items = response.data?.items || []
    pageState.total = response.data?.total || 0
    pageState.page = response.data?.page || 1
    pageState.page_size = response.data?.page_size || pageSize.value
    pageState.total_pages = response.data?.total_pages || 0
    lastLoadedAt.value = new Date()
    await nextTick()
    videoTableRef.value?.clearSelection()
  } catch (error) {
    if (requestId !== videoRequestId) return
    pageState.items = []
    pageState.total = 0
    pageState.total_pages = 0
    videoError.value = error.message || '视频列表加载失败。'
  } finally {
    if (requestId === videoRequestId) loading.value = false
  }
}

function resetSelection() {
  selectedRows.value = []
  videoTableRef.value?.clearSelection()
}

function handleSelectionChange(rows) {
  selectedRows.value = rows
}

async function submitSelectedToQueue() {
  if (!selectedRows.value.length || queueSubmitting.value) return

  const selectedSnapshot = [...selectedRows.value]
  const items = selectedSnapshot.map((row) => ({
    video_id: String(row.video_id || '').trim(),
    title: String(row.title || '').trim() || '无标题',
  }))
  if (items.some((item) => !item.video_id)) {
    ElMessage.error('部分选中视频缺少视频 ID，未提交下载队列。')
    return
  }

  queueSubmitting.value = true
  try {
    const response = await addDownloadTasks(items)
    const result = response.data || {}
    const created = result.created || []
    const existing = result.existing || []
    const skipped = result.skipped || []
    const createdCount = Number(result.created_count || created.length || 0)

    if (skipped.length) {
      ElMessage.warning(
        `已创建 ${createdCount} 个，已存在 ${existing.length} 个，跳过 ${skipped.length} 个。`,
      )
    } else if (createdCount > 0) {
      ElMessage.success(
        existing.length
          ? `已创建 ${createdCount} 个下载任务，${existing.length} 个已存在。`
          : `已创建 ${createdCount} 个下载任务。`,
      )
    } else if (existing.length) {
      ElMessage.info(`所选 ${existing.length} 个视频已经在下载队列中。`)
    } else {
      ElMessage.warning('所选视频没有产生新的下载任务。')
    }

    const processedIds = new Set(
      [...created, ...existing]
        .map((task) => String(task.video_id || '').trim())
        .filter(Boolean),
    )
    const remainingRows = selectedSnapshot.filter(
      (row) => !processedIds.has(String(row.video_id || '').trim()),
    )

    resetSelection()
    selectedRows.value = remainingRows
    await nextTick()
    if (remainingRows.length) {
      const remainingIds = new Set(
        remainingRows.map((row) => String(row.video_id || '').trim()),
      )
      for (const row of pageState.items) {
        if (remainingIds.has(String(row.video_id || '').trim())) {
          videoTableRef.value?.toggleRowSelection(row, true)
        }
      }
    }
    await loadSummary()
  } catch (error) {
    ElMessage.error(error.message || '批量加入下载队列失败。')
  } finally {
    queueSubmitting.value = false
  }
}

async function loadSummary() {
  const requestId = ++summaryRequestId
  summaryLoading.value = true
  summaryError.value = ''

  try {
    const response = await getVideoSummary()
    if (requestId !== summaryRequestId) return

    Object.assign(summary, {
      total: response.data?.total || 0,
      downloaded_total: response.data?.downloaded_total || 0,
      pending_total: response.data?.pending_total || 0,
      waiting_tasks: response.data?.waiting_tasks || 0,
      running_tasks: response.data?.running_tasks || 0,
      failed_tasks: response.data?.failed_tasks || 0,
    })
    summaryLoaded.value = true
  } catch (error) {
    if (requestId !== summaryRequestId) return
    summaryError.value = error.message || '视频摘要加载失败。'
  } finally {
    if (requestId === summaryRequestId) summaryLoading.value = false
  }
}

async function refreshAll() {
  await loadSummary()
  if (pageState.total_pages > 0 && pageState.page > pageState.total_pages) {
    pageState.page = pageState.total_pages
  }
  await loadVideos()
}

function applyFilters() {
  pageState.page = 1
  loadVideos()
}

function resetFilters() {
  keywordInput.value = ''
  statusFilter.value = 'all'
  sortBy.value = 'library'
  sortOrder.value = 'asc'
  pageSize.value = 50
  pageState.page = 1
  loadVideos()
}

function handlePageChange(page) {
  pageState.page = page
  loadVideos()
}

function handlePageSizeChange(size) {
  pageSize.value = size
  pageState.page = 1
  loadVideos()
}

function applySorting() {
  pageState.page = 1
  loadVideos()
}

function handleTableSortChange({ prop, order }) {
  if (!order) {
    sortBy.value = 'library'
    sortOrder.value = 'asc'
  } else {
    sortBy.value = prop
    sortOrder.value = order === 'descending' ? 'desc' : 'asc'
  }
  applySorting()
}

onMounted(refreshAll)
</script>

<template>
  <div class="page-view">
    <PageHeader
      title="视频库"
      description="查询、筛选和浏览本地视频记录。"
    >
      <template #actions>
          <span class="last-loaded">最近加载 {{ lastLoadedLabel }}</span>
          <ElButton :icon="Refresh" :loading="loading || summaryLoading" @click="refreshAll">
            刷新
          </ElButton>
      </template>
    </PageHeader>

      <ElAlert
        v-if="errorMessage"
        class="library-alert"
        title="视频库请求失败"
        :description="errorMessage"
        type="error"
        show-icon
        :closable="false"
      />

      <section class="stat-grid" aria-label="视频统计">
        <article
          v-for="card in summaryCards"
          :key="card.key"
          class="stat-card"
          :data-tone="card.tone"
        >
          <div class="stat-card__label">{{ card.label }}</div>
          <div class="stat-card__value">
            <ElSkeleton v-if="summaryLoading && !summaryLoaded" animated :rows="0" />
            <template v-else>{{ card.value }}</template>
          </div>
          <div class="stat-card__note">{{ card.note }}</div>
        </article>
      </section>

      <div class="queue-summary">
        <span>队列待处理 <strong>{{ summary.waiting_tasks }}</strong></span>
        <span>下载中 <strong>{{ summary.running_tasks }}</strong></span>
        <span>下载失败 <strong>{{ summary.failed_tasks }}</strong></span>
      </div>

      <section class="filter-panel" aria-label="视频筛选">
        <div class="filter-main">
          <ElInput
            aria-label="搜索视频标题或视频 ID"
            v-model="keywordInput"
            class="keyword-input"
            clearable
            placeholder="搜索视频标题或视频 ID"
            :prefix-icon="Search"
            @keyup.enter="applyFilters"
            @clear="applyFilters"
          />
          <ElSelect v-model="statusFilter" class="status-select" aria-label="按下载状态筛选" @change="applyFilters">
            <ElOption
              v-for="option in STATUS_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </ElSelect>
          <ElButton type="primary" :icon="Search" @click="applyFilters">搜索</ElButton>
          <ElButton :icon="RefreshLeft" :disabled="!hasFilters" @click="resetFilters">
            重置
          </ElButton>
        </div>

        <div class="sort-controls">
          <span class="sort-label">排序</span>
          <ElSelect v-model="sortBy" class="sort-select" aria-label="排序字段" @change="applySorting">
            <ElOption
              v-for="option in SORT_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </ElSelect>
          <ElSelect v-model="sortOrder" class="order-select" aria-label="排序方向" @change="applySorting">
            <ElOption
              v-for="option in ORDER_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </ElSelect>
        </div>
      </section>

      <section class="queue-action-bar" aria-label="批量下载操作">
        <div class="selection-summary">
          <span class="selection-label">已选择</span>
          <strong>{{ selectedCount }}</strong>
          <span>个视频</span>
        </div>
        <div class="selection-actions">
          <ElButton
            type="primary"
            :icon="Plus"
            :disabled="selectedCount === 0 || queueSubmitting"
            :loading="queueSubmitting"
            @click="submitSelectedToQueue"
          >
            加入下载队列（{{ selectedCount }}）
          </ElButton>
          <ElButton
            :icon="Close"
            :disabled="selectedCount === 0 || queueSubmitting"
            @click="resetSelection"
          >
            清空选择
          </ElButton>
        </div>
      </section>

      <section class="table-panel">
        <div class="table-heading">
          <div>
            <h2>视频记录</h2>
            <span>{{ currentRangeLabel }}</span>
          </div>
          <span class="table-page-label">
            第 {{ pageState.page }} / {{ pageState.total_pages || 0 }} 页
          </span>
        </div>

        <div class="video-table-shell">
          <ElTable
            ref="videoTableRef"
            v-loading="loading"
            :data="pageState.items"
            row-key="video_id"
            class="video-table"
            :empty-text="hasFilters ? '没有符合筛选条件的视频' : '视频库暂无数据'"
            @sort-change="handleTableSortChange"
            @selection-change="handleSelectionChange"
          >
            <ElTableColumn type="selection" width="48" />
            <ElTableColumn
              prop="title"
              label="视频标题"
              min-width="380"
              show-overflow-tooltip
              sortable="custom"
            >
              <template #default="{ row }">
                <span class="video-title">{{ row.title || '无标题' }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="video_id" label="视频 ID" min-width="210" sortable="custom">
              <template #default="{ row }">
                <span class="video-id">{{ row.video_id }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn label="下载状态" width="120">
              <template #default="{ row }">
                <ElTag :type="statusTagType(row.status)" effect="light">
                  {{ row.status_label || row.status }}
                </ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="action_label" label="操作提示" min-width="140">
              <template #default="{ row }">
                {{ row.action_label || '—' }}
              </template>
            </ElTableColumn>
            <ElTableColumn prop="task_id" label="任务 ID" min-width="190">
              <template #default="{ row }">
                <span class="task-id">{{ row.task_id || '—' }}</span>
              </template>
            </ElTableColumn>
          </ElTable>
        </div>

        <div class="pagination-row">
          <ElPagination
            v-model:current-page="pageState.page"
            v-model:page-size="pageSize"
            background
            :total="pageState.total"
            :page-sizes="PAGE_SIZE_OPTIONS"
            layout="total, sizes, prev, pager, next, jumper"
            @current-change="handlePageChange"
            @size-change="handlePageSizeChange"
          />
        </div>
      </section>
  </div>
</template>

<style scoped>

.library-alert {
  margin-bottom: 16px;
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

.stat-card {
  position: relative;
  min-height: 132px;
  padding: 20px;
  overflow: hidden;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.stat-card::after {
  position: absolute;
  top: 18px;
  right: 18px;
  width: 34px;
  height: 4px;
  content: "";
  background: var(--border-strong);
  border-radius: 2px;
}

.stat-card[data-tone="teal"]::after {
  background: var(--teal);
}

.stat-card[data-tone="amber"]::after {
  background: var(--warning);
}

.stat-card__label {
  color: var(--muted);
  font-size: 13px;
  font-weight: 600;
}

.stat-card__value {
  min-height: 42px;
  margin-top: 12px;
  color: var(--ink);
  font-size: 34px;
  font-weight: 700;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
}

.stat-card__note {
  margin-top: 5px;
  color: var(--text-tertiary);
  font-size: 12px;
}

.queue-summary {
  display: flex;
  gap: 22px;
  padding: 12px 2px 0;
  color: var(--muted);
  font-size: 12px;
}

.queue-summary strong {
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}

.filter-panel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-top: 18px;
  padding: 16px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.filter-main,
.sort-controls {
  display: flex;
  align-items: center;
  gap: 10px;
}

.filter-main {
  flex: 1;
}

.keyword-input {
  width: min(390px, 100%);
}

.status-select {
  width: 138px;
}

.sort-controls {
  flex: none;
}

.sort-label {
  color: var(--muted);
  font-size: 12px;
}

.sort-select {
  width: 136px;
}

.order-select {
  width: 92px;
}

.queue-action-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-top: 14px;
  padding: 13px 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.selection-summary {
  display: flex;
  align-items: baseline;
  gap: 5px;
  color: var(--muted);
  font-size: 12px;
}

.selection-summary strong {
  color: var(--ink);
  font-size: 19px;
  font-variant-numeric: tabular-nums;
}

.selection-actions {
  display: flex;
  align-items: center;
  gap: 9px;
}

.table-panel {
  margin-top: 16px;
  padding: 18px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.table-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.table-heading h2 {
  margin: 0;
  color: var(--ink);
  font-size: 17px;
  letter-spacing: 0;
}

.table-heading span {
  color: var(--muted);
  font-size: 12px;
}

.table-page-label {
  font-variant-numeric: tabular-nums;
}

.video-table-shell {
  overflow-x: auto;
}

.video-table {
  min-width: 1040px;
}

.video-title {
  color: var(--text-primary);
  font-weight: 550;
}

.video-id,
.task-id {
  color: var(--text-secondary);
  font-family: "Cascadia Mono", "Consolas", monospace;
  font-size: 12px;
}

.pagination-row {
  display: flex;
  justify-content: flex-end;
  padding-top: 16px;
  overflow-x: auto;
}

@media (max-width: 980px) {
  .filter-panel {
    align-items: flex-start;
    flex-direction: column;
  }

  .filter-main {
    width: 100%;
  }

  .sort-controls {
    width: 100%;
  }
}

@media (max-width: 720px) {
  .stat-grid {
    grid-template-columns: 1fr;
  }

  .stat-card {
    min-height: 112px;
  }

  .queue-summary {
    flex-wrap: wrap;
    gap: 8px 18px;
  }

  .filter-main {
    align-items: stretch;
    flex-direction: column;
  }

  .keyword-input,
  .status-select,
  .sort-select,
  .order-select {
    width: 100%;
  }

  .sort-controls {
    align-items: stretch;
    flex-direction: column;
  }

  .queue-action-bar,
  .selection-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .table-panel {
    padding: 14px;
  }

  .pagination-row {
    justify-content: flex-start;
  }
}
</style>
