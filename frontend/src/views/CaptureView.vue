<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import {
  Close,
  CopyDocument,
  Refresh,
  Search,
  VideoPlay,
  VideoPause,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  clearCapture,
  getCaptureResults,
  getCaptureStatus,
  startCapture,
  stopCapture,
} from '../api/capture'
import {
  getAuthStatus,
  isAuthRequiredError,
  isAuthStatusUnknownError,
} from '../api/auth'
import { addDownloadTasks, getDownloadStatus } from '../api/downloads'
import { getVideoSummary, getVideos } from '../api/videos'

const AUTO_REFRESH_INTERVAL = 1500
const AUTO_REFRESH_ERROR_INTERVAL = 6000

const MODE_OPTIONS = [
  {
    value: 'recommend',
    label: '推荐页',
    fixedUrl: 'https://www.douyin.com/?recommend=1',
  },
  {
    value: 'user',
    label: '博主主页',
    fixedUrl: '',
  },
  {
    value: 'jingxuan',
    label: '精选首页',
    fixedUrl: 'https://www.douyin.com/jingxuan',
  },
  {
    value: 'search',
    label: '搜索页',
    fixedUrl: '',
  },
]

const STATUS_META = {
  idle: { label: '空闲', type: 'info' },
  running: { label: '抓取中', type: 'success' },
  success: { label: '抓取完成', type: 'success' },
  failed: { label: '抓取失败', type: 'danger' },
}

const mode = ref('recommend')
const userHomeUrl = ref('')
const searchKeyword = ref('')
const actionBusy = ref('')
const pageVisible = ref(true)
const processingCompletion = ref(false)

const capture = reactive({
  task_id: null,
  status: 'idle',
  message: '',
  progress: {},
  error: null,
  stopping: false,
  stop_requested_at: null,
})

const downloadRunning = ref(false)
const authState = ref('checking')
const authError = ref('')
const captureResults = ref([])
const resultSource = ref('capture')
const selectedRows = ref([])
const recentVideos = ref([])
const recentLoading = ref(false)
const requestError = ref('')
const lastUpdatedAt = ref(null)
const submittedModeLabel = ref('')
const submittedTaskId = ref(null)

const librarySummary = reactive({
  total: 0,
  downloaded_total: 0,
  pending_total: 0,
  waiting_tasks: 0,
  running_tasks: 0,
  failed_tasks: 0,
})

const tableRef = ref(null)
let refreshTimer = null
let statusController = null
let requestSequence = 0
let nextRefreshDelay = AUTO_REFRESH_INTERVAL
let disposed = false
const processedTaskIds = new Set()

const activeMode = computed(
  () => MODE_OPTIONS.find((option) => option.value === mode.value) || MODE_OPTIONS[0],
)
const targetUrl = computed(() => {
  if (mode.value === 'user') return userHomeUrl.value.trim()
  if (mode.value === 'search') {
    const keyword = searchKeyword.value.trim()
    return keyword ? `https://www.douyin.com/search/${keyword}?type=video` : ''
  }
  return activeMode.value.fixedUrl
})
const captureRunning = computed(() => capture.status === 'running')
const captureStopping = computed(() => Boolean(capture.stopping))
const captureBusy = computed(() => captureRunning.value || captureStopping.value)
const canStart = computed(
  () =>
    Boolean(targetUrl.value) &&
    !captureBusy.value &&
    !downloadRunning.value &&
    authState.value === 'ready' &&
    !actionBusy.value,
)
const canStop = computed(
  () => captureRunning.value && !captureStopping.value && !actionBusy.value,
)
const captureStatusMeta = computed(
  () => STATUS_META[capture.status] || { label: capture.status || '未知', type: 'info' },
)
const authStatusMeta = computed(() => {
  if (authState.value === 'ready') return { label: '已登录', type: 'success' }
  if (authState.value === 'missing') return { label: '尚未登录', type: 'warning' }
  if (authState.value === 'failed') return { label: '状态未知', type: 'danger' }
  return { label: '登录检查中', type: 'info' }
})
const authNotice = computed(() => {
  if (authState.value === 'missing') {
    return '当前未登录。抖音登录是使用抓取功能的必要前提，请先前往首页完成登录。'
  }
  if (authState.value === 'failed') {
    return `无法获取登录状态：${authError.value || 'Auth API 请求失败'}`
  }
  return ''
})
const lastUpdatedLabel = computed(() => {
  if (!lastUpdatedAt.value) return '尚未更新'
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(lastUpdatedAt.value)
})
const submittedMode = computed(() => {
  if (!submittedTaskId.value) return '未提交'
  if (capture.task_id && capture.task_id !== submittedTaskId.value) return '外部任务'
  return submittedModeLabel.value || '未记录'
})
const progressPercentage = computed(() => {
  const value = Number(capture.progress?.progress || 0)
  if (!Number.isFinite(value)) return 0
  return Math.min(100, Math.max(0, Math.round(value * 100)))
})
const resultCount = computed(() => captureResults.value.length)
const hasResults = computed(() => resultCount.value > 0)
const dataMayBeStale = computed(() => Boolean(requestError.value && lastUpdatedAt.value))
const captureErrorDescription = computed(() => {
  const message = requestError.value || capture.error || ''
  return dataMayBeStale.value ? `${message} 当前数据可能已过期。` : message
})

function clearRefreshTimer() {
  if (refreshTimer !== null) {
    clearTimeout(refreshTimer)
    refreshTimer = null
  }
}

function scheduleRefresh(delay = nextRefreshDelay) {
  clearRefreshTimer()
  if (disposed || document.hidden) return
  refreshTimer = setTimeout(() => {
    refreshTimer = null
    refreshCapture()
  }, delay)
}

async function loadLibrarySnapshot() {
  try {
    const response = await getVideoSummary()
    Object.assign(librarySummary, {
      total: response.data?.total || 0,
      downloaded_total: response.data?.downloaded_total || 0,
      pending_total: response.data?.pending_total || 0,
      waiting_tasks: response.data?.waiting_tasks || 0,
      running_tasks: response.data?.running_tasks || 0,
      failed_tasks: response.data?.failed_tasks || 0,
    })
  } catch {
    // The capture page keeps running when only the optional library summary is unavailable.
  }
}

async function finalizeCapture(statusData) {
  const taskId = statusData.task_id
  if (!taskId || processedTaskIds.has(taskId) || processingCompletion.value) return

  processedTaskIds.add(taskId)
  processingCompletion.value = true
  try {
    await loadLibrarySnapshot()
    if (!statusData.error) {
      ElMessage.success('抓取完成，结果已按现有后端流程合并到视频库。')
    }
    await clearCapture()
  } catch (error) {
    processedTaskIds.delete(taskId)
    ElMessage.error(error.message || '完成抓取后刷新视频库或清理抓取状态失败。')
  } finally {
    processingCompletion.value = false
  }
}

async function refreshCapture({ manual = false } = {}) {
  if (disposed) return
  if (!manual && statusController) return

  clearRefreshTimer()
  if (manual && statusController) statusController.abort()

  const controller = new AbortController()
  statusController = controller
  const requestId = ++requestSequence
  if (manual) requestError.value = ''

  try {
    const [statusResult, resultsResult, downloadResult, authResult] = await Promise.allSettled([
      getCaptureStatus({ signal: controller.signal }),
      getCaptureResults({ signal: controller.signal }),
      getDownloadStatus({ signal: controller.signal }),
      getAuthStatus({ signal: controller.signal }),
    ])
    if (requestId !== requestSequence) return
    if (statusResult.status === 'rejected' && statusResult.reason.code === 'REQUEST_CANCELED') return

    if (statusResult.status === 'fulfilled') {
      Object.assign(capture, statusResult.value.data || {})
      requestError.value = ''
      lastUpdatedAt.value = new Date()
      nextRefreshDelay = AUTO_REFRESH_INTERVAL
    } else {
      requestError.value = statusResult.reason.message || '抓取状态加载失败。'
      nextRefreshDelay = AUTO_REFRESH_ERROR_INTERVAL
    }

    if (downloadResult.status === 'fulfilled') {
      downloadRunning.value = Boolean(downloadResult.value.data?.running)
    }

    if (authResult.status === 'fulfilled') {
      authState.value = authResult.value.data?.login_ready ? 'ready' : 'missing'
      authError.value = ''
    } else if (authResult.reason.code !== 'REQUEST_CANCELED') {
      authState.value = 'failed'
      authError.value = authResult.reason.message || '登录状态加载失败。'
    }

    if (resultsResult.status === 'fulfilled' && capture.status !== 'idle') {
      captureResults.value = (resultsResult.value.data?.items || []).map((item) => ({
        video_id: item.video_id,
        title: item.title || '无标题',
      }))
      resultSource.value = 'capture'
      selectedRows.value = []
    }

    if (['success', 'failed'].includes(capture.status)) {
      await finalizeCapture(capture)
    }

  } catch (error) {
    if (error.code === 'REQUEST_CANCELED') return
    if (requestId !== requestSequence) return
    requestError.value = error.message || '抓取状态加载失败。'
    nextRefreshDelay = AUTO_REFRESH_ERROR_INTERVAL
  } finally {
    if (requestId === requestSequence) {
      statusController = null
      scheduleRefresh()
    }
  }
}

function handleVisibilityChange() {
  pageVisible.value = !document.hidden
  if (document.hidden) {
    clearRefreshTimer()
    statusController?.abort()
    return
  }
  refreshCapture({ manual: true })
}

async function submitCapture() {
  if (!canStart.value) return
  const target = targetUrl.value
  actionBusy.value = 'start'
  try {
    const response = await startCapture({ targetUrl: target, mode: mode.value })
    submittedTaskId.value = response.data?.task_id || null
    submittedModeLabel.value = activeMode.value.label
    capture.task_id = submittedTaskId.value
    capture.status = 'running'
    capture.stopping = false
    capture.error = null
    captureResults.value = []
    resultSource.value = 'capture'
    selectedRows.value = []
    ElMessage.success(response.message || '抓取任务已启动。')
    await refreshCapture({ manual: true })
  } catch (error) {
    if (isAuthRequiredError(error)) {
      authState.value = 'missing'
      authError.value = ''
      ElMessage.error('登录状态无效，请先前往首页完成抖音登录。')
      return
    }
    if (isAuthStatusUnknownError(error)) {
      authState.value = 'failed'
      authError.value = error.message
      ElMessage.error(error.message || '无法确认抖音登录状态。')
      return
    }
    ElMessage.error(error.message || '无法启动抓取。')
  } finally {
    actionBusy.value = ''
  }
}

async function requestStopCapture() {
  if (!canStop.value) return
  actionBusy.value = 'stop'
  try {
    const response = await stopCapture()
    capture.stopping = true
    ElMessage.warning(response.message || '正在停止抓取并提取已加载数据。')
    await refreshCapture({ manual: true })
  } catch (error) {
    ElMessage.error(error.message || '停止抓取失败。')
  } finally {
    actionBusy.value = ''
  }
}

async function copyVideoId(videoId) {
  try {
    await navigator.clipboard.writeText(videoId)
    ElMessage.success('视频 ID 已复制。')
  } catch {
    ElMessage.error('复制失败，请检查浏览器剪贴板权限。')
  }
}

function handleSelectionChange(rows) {
  selectedRows.value = rows
}

function clearSelection() {
  tableRef.value?.clearSelection()
  selectedRows.value = []
}

async function addSelectedToQueue() {
  if (!selectedRows.value.length || actionBusy.value) return
  actionBusy.value = 'queue'
  try {
    const response = await addDownloadTasks(
      selectedRows.value.map((row) => ({
        video_id: row.video_id,
        title: row.title || '无标题',
      })),
    )
    const created = response.data?.created_count || 0
    const existing = response.data?.existing?.length || 0
    if (created) {
      ElMessage.success(`已加入 ${created} 个下载任务。`)
    } else if (existing) {
      ElMessage.info('所选视频已经在下载队列中。')
    } else {
      ElMessage.warning('所选视频未加入下载队列。')
    }
    clearSelection()
  } catch (error) {
    ElMessage.error(error.message || '加入下载队列失败。')
  } finally {
    actionBusy.value = ''
  }
}

async function clearResults() {
  if (!hasResults.value || actionBusy.value) return
  try {
    await ElMessageBox.confirm(
      '只清理当前抓取状态和页面结果，不会删除视频库、CSV、下载任务或视频文件。是否继续？',
      '确认清空抓取结果',
      {
        type: 'warning',
        confirmButtonText: '清空结果',
        cancelButtonText: '取消',
      },
    )
  } catch (action) {
    if (action === 'cancel' || action === 'close') return
    throw action
  }

  actionBusy.value = 'clear'
  try {
    if (resultSource.value === 'capture' || capture.status !== 'idle') {
      await clearCapture()
    }
    captureResults.value = []
    resultSource.value = 'capture'
    clearSelection()
    ElMessage.success('抓取结果已清空，视频库和下载任务未受影响。')
  } catch (error) {
    ElMessage.error(error.message || '清空抓取结果失败。')
  } finally {
    actionBusy.value = ''
  }
}

async function loadRecentVideosToWorkspace() {
  if (recentLoading.value) return
  recentLoading.value = true
  try {
    const response = await getVideos({
      page: 1,
      pageSize: 50,
      sortBy: 'library',
      sortOrder: 'desc',
    })
    captureResults.value = (response.data?.items || []).map((item) => ({
      video_id: item.video_id,
      title: item.title || '无标题',
    }))
    resultSource.value = 'recent'
    clearSelection()
    ElMessage.info(`已载入最近 ${captureResults.value.length} 条视频。`)
  } catch (error) {
    ElMessage.error(error.message || '读取最近视频失败。')
  } finally {
    recentLoading.value = false
  }
}

async function manualRefresh() {
  await Promise.allSettled([
    refreshCapture({ manual: true }),
    loadLibrarySnapshot(),
  ])
}

onMounted(async () => {
  pageVisible.value = !document.hidden
  document.addEventListener('visibilitychange', handleVisibilityChange)
  await Promise.allSettled([refreshCapture({ manual: true }), loadLibrarySnapshot()])
})

onUnmounted(() => {
  disposed = true
  clearRefreshTimer()
  statusController?.abort()
  document.removeEventListener('visibilitychange', handleVisibilityChange)
})
</script>

<template>
  <div class="page-view">

    <div class="page-body">
      <section class="capture-heading">
        <div>
          <p class="section-kicker">视频来源</p>
          <h2>抓取管理</h2>
          <p class="capture-subtitle">通过推荐、博主主页、精选或搜索关键词提取视频 ID。</p>
        </div>
        <div class="heading-actions">
          <ElTag :type="pageVisible ? 'success' : 'info'" effect="plain">
            {{ pageVisible ? '自动刷新 1.5s' : '后台暂停刷新' }}
          </ElTag>
          <span class="last-updated">最近更新 {{ lastUpdatedLabel }}</span>
          <ElButton :icon="Refresh" @click="manualRefresh">刷新</ElButton>
        </div>
      </section>

      <ElAlert
        v-if="requestError || capture.error"
        class="capture-alert"
        title="抓取状态异常"
        :description="captureErrorDescription"
        type="error"
        show-icon
        :closable="false"
      />
      <ElAlert
        v-else-if="dataMayBeStale"
        title="当前抓取数据可能已过期"
        description="FastAPI 暂时不可用，恢复后页面会继续刷新。"
        type="warning"
        :closable="false"
        show-icon
      />

      <section class="stat-grid">
        <article class="stat-card">
          <span>视频库总数</span>
          <strong>{{ librarySummary.total }}</strong>
        </article>
        <article class="stat-card">
          <span>未下载</span>
          <strong>{{ librarySummary.pending_total }}</strong>
        </article>
        <article class="stat-card">
          <span>队列待处理</span>
          <strong>{{ librarySummary.waiting_tasks }}</strong>
        </article>
        <article class="stat-card" data-tone="accent">
          <span>当前结果</span>
          <strong>{{ resultCount }}</strong>
        </article>
      </section>

      <section class="capture-panel">
        <div class="capture-panel__heading">
          <div>
            <h3>抓取操作</h3>
            <p>切换模式后，目标输入规则会同步变化。</p>
          </div>
          <div class="capture-status-tags">
            <ElTag :type="authStatusMeta.type" effect="light">{{ authStatusMeta.label }}</ElTag>
            <ElTag :type="captureStatusMeta.type" effect="light">{{ captureStatusMeta.label }}</ElTag>
          </div>
        </div>

        <div v-if="authNotice" class="capture-auth-banner" :data-state="authState">
          <span>{{ authNotice }}</span>
          <RouterLink v-if="authState === 'missing'" to="/">
            <ElButton size="small" type="primary">前往登录</ElButton>
          </RouterLink>
          <ElButton
            v-else-if="authState === 'failed'"
            size="small"
            @click="refreshCapture({ manual: true })"
          >
            重新检查
          </ElButton>
        </div>

        <div class="capture-form">
          <div class="form-field mode-field">
            <label>抓取模式</label>
            <ElSelect v-model="mode" :disabled="captureBusy">
              <ElOption
                v-for="option in MODE_OPTIONS"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </ElSelect>
          </div>

          <div class="form-field target-field">
            <template v-if="mode === 'user'">
              <label>博主主页链接</label>
              <ElInput
                v-model="userHomeUrl"
                clearable
                placeholder="粘贴抖音博主主页地址"
                :disabled="captureBusy"
              />
            </template>
            <template v-else-if="mode === 'search'">
              <label>搜索关键词</label>
              <ElInput
                v-model="searchKeyword"
                clearable
                placeholder="输入关键词或话题"
                :disabled="captureBusy"
                @keyup.enter="submitCapture"
              />
            </template>
            <template v-else>
              <label>目标地址</label>
              <ElInput :model-value="targetUrl" readonly>
                <template #append>固定</template>
              </ElInput>
            </template>
          </div>

          <div class="capture-actions">
            <ElButton
              type="primary"
              :icon="VideoPlay"
              :disabled="!canStart"
              :loading="actionBusy === 'start'"
              @click="submitCapture"
            >
              开始抓取
            </ElButton>
            <ElButton
              :icon="VideoPause"
              :disabled="!canStop"
              :loading="actionBusy === 'stop'"
              @click="requestStopCapture"
            >
              停止抓取
            </ElButton>
          </div>
        </div>

        <div class="capture-status">
          <div class="status-main">
            <span class="status-dot" :class="{ running: captureRunning, stopping: captureStopping }" />
            <div>
              <strong>{{ capture.message || '等待开始抓取' }}</strong>
              <p>
                任务 {{ capture.task_id || '—' }} · 提交模式 {{ submittedMode }}
              </p>
            </div>
          </div>
          <div class="status-progress">
            <ElProgress :percentage="progressPercentage" :stroke-width="7" />
            <span>{{ capture.progress?.stage || capture.progress?.message || '无阶段信息' }}</span>
          </div>
        </div>

        <ElAlert
          v-if="downloadRunning"
          title="下载队列正在运行"
          description="为避免浏览器 Profile 冲突，下载运行期间不能启动抓取。"
          type="warning"
          :closable="false"
          show-icon
        />
      </section>

      <section class="results-panel">
        <div class="results-heading">
          <div>
            <h3>{{ resultSource === 'recent' ? '最近视频工作区' : '抓取结果' }}</h3>
            <p>
              {{ resultSource === 'recent' ? '已从视频库载入最近记录。' : '抓取完成后由现有后端 Scrapling 流程自动尝试合并到视频库。' }}
            </p>
          </div>
          <div class="results-actions">
            <span class="selection-count">已选择 {{ selectedRows.length }} 个视频</span>
            <ElButton
              type="primary"
              :disabled="selectedRows.length === 0 || Boolean(actionBusy)"
              :loading="actionBusy === 'queue'"
              @click="addSelectedToQueue"
            >
              加入下载队列（{{ selectedRows.length }}）
            </ElButton>
            <ElButton
              :icon="Close"
              :disabled="selectedRows.length === 0 || Boolean(actionBusy)"
              @click="clearSelection"
            >
              清空选择
            </ElButton>
            <ElButton
              :disabled="!hasResults || Boolean(actionBusy)"
              :loading="actionBusy === 'clear'"
              @click="clearResults"
            >
              清空结果
            </ElButton>
          </div>
        </div>

        <ElAlert
          class="capability-note"
          title="入库统计能力说明"
          description="现有抓取 API 只返回结果总数和视频 ID/标题，不提供本次新增、重复、入库失败数量拆分。"
          type="info"
          :closable="false"
          show-icon
        />

        <div v-if="hasResults" class="result-table-shell">
          <ElTable
            ref="tableRef"
            :data="captureResults"
            row-key="video_id"
            max-height="560"
            class="result-table"
            @selection-change="handleSelectionChange"
          >
            <ElTableColumn type="selection" width="46" />
            <ElTableColumn prop="video_id" label="视频 ID" min-width="220">
              <template #default="{ row }">
                <span class="video-id">{{ row.video_id }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="title" label="视频标题" min-width="480" show-overflow-tooltip>
              <template #default="{ row }">{{ row.title || '无标题' }}</template>
            </ElTableColumn>
            <ElTableColumn label="操作" width="96" fixed="right">
              <template #default="{ row }">
                <ElTooltip content="复制视频 ID" placement="top">
                  <ElButton
                    circle
                    size="small"
                    :icon="CopyDocument"
                    aria-label="复制视频 ID"
                    @click="copyVideoId(row.video_id)"
                  />
                </ElTooltip>
              </template>
            </ElTableColumn>
          </ElTable>
        </div>

        <div v-else class="empty-results">
          <div class="empty-copy">
            <strong>暂无当前抓取结果</strong>
            <p>最近保存的视频仍可在下方重新载入工作区。</p>
          </div>
          <ElButton
            :loading="recentLoading"
            @click="loadRecentVideosToWorkspace"
          >
            载入最近 50 条到工作区
          </ElButton>
        </div>

        <div v-if="!hasResults && recentVideos.length" class="recent-list">
          <div class="recent-heading">
            <h4>最近保存的视频</h4>
            <span>显示 {{ recentVideos.length }} 条</span>
          </div>
          <div class="recent-grid">
            <article v-for="video in recentVideos" :key="video.video_id" class="recent-item">
              <span class="video-id">{{ video.video_id }}</span>
              <strong>{{ video.title || '无标题' }}</strong>
            </article>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.workspace--capture {
  width: min(1320px, calc(100% - 40px));
}

.capture-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 20px;
}

.capture-heading .section-kicker {
  color: #748187;
}

.capture-heading h2 {
  margin: 0;
  color: var(--ink);
  font-size: 28px;
  letter-spacing: 0;
}

.capture-subtitle {
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

.capture-alert {
  margin-bottom: 16px;
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.stat-card {
  padding: 17px 18px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 8px;
}

.stat-card span {
  color: var(--muted);
  font-size: 12px;
}

.stat-card strong {
  display: block;
  margin-top: 8px;
  color: var(--ink);
  font-size: 29px;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.stat-card[data-tone="accent"] strong {
  color: #c75550;
}

.capture-panel,
.results-panel {
  margin-top: 14px;
  padding: 20px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 8px;
}

.capture-panel__heading,
.results-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}

.capture-panel h3,
.results-heading h3,
.recent-heading h4 {
  margin: 0;
  color: var(--ink);
  font-size: 17px;
  letter-spacing: 0;
}

.capture-status-tags {
  display: flex;
  align-items: center;
  gap: 7px;
}

.capture-auth-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 16px;
  padding: 11px 13px;
  color: #76520c;
  font-size: 12px;
  background: #fff6e7;
  border: 1px solid #ecd3a0;
  border-radius: 6px;
}

.capture-auth-banner[data-state="failed"] {
  color: #9a3f3b;
  background: #fff0ef;
  border-color: #efc3bf;
}

.capture-panel p,
.results-heading p {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.capture-form {
  display: grid;
  grid-template-columns: 170px minmax(280px, 1fr) auto;
  gap: 16px;
  align-items: end;
  margin-top: 20px;
}

.form-field label {
  display: block;
  margin-bottom: 7px;
  color: #59676e;
  font-size: 12px;
  font-weight: 600;
}

.mode-field .el-select,
.target-field .el-input {
  width: 100%;
}

.capture-actions {
  display: flex;
  gap: 10px;
}

.capture-status {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  margin-top: 20px;
  padding-top: 18px;
  border-top: 1px solid var(--line);
}

.status-main {
  display: flex;
  align-items: flex-start;
  gap: 11px;
  min-width: 0;
}

.status-dot {
  flex: none;
  width: 8px;
  height: 8px;
  margin-top: 6px;
  background: #9ba6aa;
  border-radius: 50%;
}

.status-dot.running {
  background: var(--teal);
  box-shadow: 0 0 0 4px rgba(42, 148, 136, 0.1);
}

.status-dot.stopping {
  background: #d79b2e;
  box-shadow: 0 0 0 4px rgba(215, 155, 46, 0.1);
}

.status-main strong {
  color: var(--ink);
  font-size: 13px;
}

.status-main p {
  margin: 5px 0 0;
  color: var(--muted);
  font-family: "Cascadia Mono", "Consolas", monospace;
  font-size: 11px;
  overflow-wrap: anywhere;
}

.status-progress {
  flex: none;
  width: min(310px, 38%);
}

.status-progress span {
  display: block;
  margin-top: 3px;
  color: var(--muted);
  font-size: 11px;
  text-align: right;
}

.results-heading {
  align-items: flex-end;
}

.results-actions {
  display: flex;
  align-items: center;
  gap: 9px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.selection-count {
  color: var(--muted);
  font-size: 12px;
}

.capability-note {
  margin-top: 16px;
}

.result-table-shell {
  margin-top: 14px;
  overflow-x: auto;
}

.result-table {
  min-width: 900px;
}

.video-id {
  color: #526168;
  font-family: "Cascadia Mono", "Consolas", monospace;
  font-size: 12px;
}

.empty-results {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-top: 16px;
  padding: 28px 20px;
  background: #f8faf9;
  border: 1px dashed #d4dcda;
  border-radius: 7px;
}

.empty-copy strong {
  color: #334249;
  font-size: 14px;
}

.empty-copy p {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.recent-list {
  margin-top: 18px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
}

.recent-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.recent-heading h4 {
  font-size: 14px;
}

.recent-heading span {
  color: var(--muted);
  font-size: 12px;
}

.recent-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.recent-item {
  display: flex;
  gap: 12px;
  min-width: 0;
  padding: 10px 12px;
  background: #f7f9f8;
  border: 1px solid #e2e8e6;
  border-radius: 5px;
}

.recent-item .video-id {
  flex: none;
}

.recent-item strong {
  overflow: hidden;
  color: #3a484f;
  font-size: 12px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 980px) {
  .capture-form {
    grid-template-columns: 150px minmax(0, 1fr);
  }

  .capture-actions {
    grid-column: 1 / -1;
  }
}

@media (max-width: 720px) {
  .workspace--capture {
    width: min(100% - 24px, 1320px);
  }

  .capture-heading,
  .capture-panel__heading,
  .results-heading,
  .capture-status,
  .empty-results {
    align-items: flex-start;
    flex-direction: column;
  }

  .heading-actions,
  .results-actions,
  .capture-actions {
    width: 100%;
    flex-wrap: wrap;
  }

  .capture-auth-banner {
    align-items: flex-start;
    flex-direction: column;
  }

  .stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .capture-form {
    grid-template-columns: 1fr;
  }

  .capture-actions {
    grid-column: auto;
  }

  .capture-actions .el-button {
    flex: 1;
  }

  .status-progress {
    width: 100%;
  }

  .status-progress span {
    text-align: left;
  }

  .recent-grid {
    grid-template-columns: 1fr;
  }
}
</style>
