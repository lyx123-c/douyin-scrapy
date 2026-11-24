<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import {
  Delete,
  Download,
  Plus,
  Refresh,
  RefreshRight,
  VideoPause,
  VideoPlay,
  View,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  addDownloadTasks,
  clearCompletedDownloads,
  deleteDownloadTask,
  getDownloadTask,
  getDownloads,
  immediateDownload,
  retryDownloadTask,
  startDownloads,
  stopDownloads,
} from '../api/downloads'

const AUTO_REFRESH_INTERVAL = 3000
const AUTO_REFRESH_ERROR_INTERVAL = 10000

const STATUS_META = {
  waiting: { label: '待下载', type: 'warning' },
  running: { label: '下载中', type: 'primary' },
  success: { label: '已完成', type: 'success' },
  failed: { label: '失败', type: 'danger' },
  skipped: { label: '已存在', type: 'info' },
  interrupted: { label: '已中断', type: 'warning' },
}

const snapshot = reactive({
  items: [],
  total: 0,
  counts: {
    waiting: 0,
    running: 0,
    success: 0,
    failed: 0,
    skipped: 0,
    interrupted: 0,
  },
  queue: {
    running: false,
    thread_alive: false,
    stopping: false,
    forced_task_count: 0,
    counts: {},
  },
})

const addForm = reactive({
  videoId: '',
  title: '',
})

const immediateForm = reactive({
  videoId: '',
  title: '',
  force: false,
})

const loading = ref(true)
const refreshing = ref(false)
const errorMessage = ref('')
const lastUpdatedAt = ref(null)
const actionBusy = ref('')
const pageVisible = ref(true)
const detailsVisible = ref(false)
const detailsLoading = ref(false)
const selectedTask = ref(null)

let autoRefreshTimer = null
let snapshotController = null
let requestSequence = 0
let nextAutoRefreshDelay = AUTO_REFRESH_INTERVAL
let disposed = false

const queueRunning = computed(() => Boolean(snapshot.queue?.running))
const queueStopping = computed(() => Boolean(snapshot.queue?.stopping))
const completedCount = computed(
  () => Number(snapshot.counts.success || 0) + Number(snapshot.counts.skipped || 0),
)
const canStartQueue = computed(
  () =>
    Number(snapshot.counts.waiting || 0) > 0 &&
    !queueRunning.value &&
    !actionBusy.value,
)
const canStopQueue = computed(
  () => queueRunning.value && !queueStopping.value && !actionBusy.value,
)
const queueStatusLabel = computed(() => {
  if (queueStopping.value) return '正在停止'
  if (queueRunning.value) return '运行中'
  return '空闲'
})
const queueStatusType = computed(() => {
  if (queueStopping.value) return 'warning'
  if (queueRunning.value) return 'success'
  return 'info'
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
const hasCompletedTasks = computed(() => completedCount.value > 0)
const dataMayBeStale = computed(() => Boolean(errorMessage.value && lastUpdatedAt.value))

function clearAutoRefreshTimer() {
  if (autoRefreshTimer !== null) {
    clearTimeout(autoRefreshTimer)
    autoRefreshTimer = null
  }
}

function scheduleAutoRefresh(delay = nextAutoRefreshDelay) {
  clearAutoRefreshTimer()
  if (disposed || document.hidden) return
  autoRefreshTimer = setTimeout(() => {
    autoRefreshTimer = null
    refreshSnapshot()
  }, delay)
}

async function refreshSnapshot({ manual = false } = {}) {
  if (disposed) return
  if (!manual && snapshotController) return

  clearAutoRefreshTimer()
  if (manual && snapshotController) {
    snapshotController.abort()
  }

  const controller = new AbortController()
  snapshotController = controller
  const requestId = ++requestSequence
  refreshing.value = true
  loading.value = snapshot.items.length === 0
  if (manual) errorMessage.value = ''

  try {
    const response = await getDownloads({ signal: controller.signal })
    if (requestId !== requestSequence) return

    snapshot.items = response.data?.items || []
    snapshot.total = response.data?.total || 0
    snapshot.counts = {
      ...snapshot.counts,
      ...(response.data?.counts || {}),
    }
    snapshot.queue = response.data?.queue || snapshot.queue
    errorMessage.value = ''
    lastUpdatedAt.value = new Date()
    nextAutoRefreshDelay = AUTO_REFRESH_INTERVAL
  } catch (error) {
    if (error.code === 'REQUEST_CANCELED') return
    if (requestId !== requestSequence) return

    errorMessage.value = error.message || '下载队列加载失败。'
    nextAutoRefreshDelay = AUTO_REFRESH_ERROR_INTERVAL
  } finally {
    if (requestId === requestSequence) {
      snapshotController = null
      loading.value = false
      refreshing.value = false
      scheduleAutoRefresh()
    }
  }
}

function handleVisibilityChange() {
  pageVisible.value = !document.hidden
  if (document.hidden) {
    clearAutoRefreshTimer()
    snapshotController?.abort()
    return
  }
  refreshSnapshot({ manual: true })
}

function statusMeta(status) {
  return STATUS_META[status] || { label: status || '未知', type: 'info' }
}

function normalizeProgress(value) {
  const number = Number(value || 0)
  if (!Number.isFinite(number)) return 0
  return Math.min(100, Math.max(0, Math.round(number * 100)))
}

function progressMessage(task) {
  return (
    task.progress_details?.message ||
    task.message ||
    (task.status === 'waiting' ? '等待执行' : '—')
  )
}

function taskIssue(task) {
  return task.error || (task.status === 'failed' ? task.message : '')
}

function formatDateTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}

function canRetry(task) {
  return ['failed', 'interrupted'].includes(task.status)
}

function canDelete(task) {
  return task.status !== 'running'
}

async function startQueue() {
  if (!canStartQueue.value) return
  actionBusy.value = 'start'
  try {
    const response = await startDownloads()
    ElMessage.success(response.message || '下载队列已启动。')
    await refreshSnapshot({ manual: true })
  } catch (error) {
    ElMessage.error(error.message || '下载队列启动失败。')
  } finally {
    actionBusy.value = ''
  }
}

async function stopQueue() {
  if (!canStopQueue.value) return
  actionBusy.value = 'stop'
  try {
    const response = await stopDownloads()
    ElMessage.warning(response.message || '将在当前任务完成后停止下载队列。')
    await refreshSnapshot({ manual: true })
  } catch (error) {
    ElMessage.error(error.message || '停止下载队列失败。')
  } finally {
    actionBusy.value = ''
  }
}

async function submitAddTask() {
  if (actionBusy.value) return
  const videoId = addForm.videoId.trim()
  if (!videoId) {
    ElMessage.warning('请输入视频 ID。')
    return
  }

  actionBusy.value = 'add'
  try {
    const response = await addDownloadTasks([
      {
        video_id: videoId,
        title: addForm.title.trim() || '无标题',
      },
    ])
    const result = response.data || {}
    if (result.created_count > 0) {
      ElMessage.success(`已加入 ${result.created_count} 个下载任务。`)
      addForm.videoId = ''
      addForm.title = ''
    } else if (result.existing?.length) {
      ElMessage.info('该任务已在下载队列中。')
    } else if (result.skipped?.length) {
      ElMessage.warning('视频 ID 格式不正确，任务未加入。')
    } else {
      ElMessage.info('没有创建新的下载任务。')
    }
    await refreshSnapshot({ manual: true })
  } catch (error) {
    ElMessage.error(error.message || '添加下载任务失败。')
  } finally {
    actionBusy.value = ''
  }
}

async function submitImmediateDownload() {
  if (actionBusy.value) return
  const videoId = immediateForm.videoId.trim()
  if (!videoId) {
    ElMessage.warning('请输入视频 ID。')
    return
  }

  actionBusy.value = 'immediate'
  try {
    const response = await immediateDownload({
      videoId,
      title: immediateForm.title.trim() || '无标题',
      force: immediateForm.force,
    })
    const result = response.data || {}
    if (result.queued) {
      ElMessage.success('任务已加入队列并请求启动下载。')
      immediateForm.videoId = ''
      immediateForm.title = ''
      immediateForm.force = false
    } else if (result.reason === 'already_downloaded') {
      ElMessage.info('该视频已有下载记录，可勾选强制重新下载。')
    } else if (result.reason === 'running') {
      ElMessage.info('该视频正在下载中。')
    } else {
      ElMessage.warning('任务未加入下载队列。')
    }
    await refreshSnapshot({ manual: true })
  } catch (error) {
    ElMessage.error(error.message || '立即下载请求失败。')
  } finally {
    actionBusy.value = ''
  }
}

async function openTaskDetails(task) {
  detailsVisible.value = true
  detailsLoading.value = true
  selectedTask.value = task
  try {
    const response = await getDownloadTask(task.task_id)
    selectedTask.value = response.data
  } catch (error) {
    ElMessage.error(error.message || '读取任务详情失败。')
  } finally {
    detailsLoading.value = false
  }
}

async function retryTask(task) {
  if (!canRetry(task) || actionBusy.value) return
  actionBusy.value = `retry:${task.task_id}`
  try {
    const response = await retryDownloadTask(task.task_id)
    ElMessage.success(response.message || '任务已重新加入待下载。')
    await refreshSnapshot({ manual: true })
  } catch (error) {
    ElMessage.error(error.message || '重试任务失败。')
  } finally {
    actionBusy.value = ''
  }
}

async function removeTask(task) {
  if (!canDelete(task) || actionBusy.value) return
  try {
    await ElMessageBox.confirm(
      '只移除下载任务记录，不会删除视频文件或 CSV 记录。是否继续？',
      '确认移除任务',
      {
        type: 'warning',
        confirmButtonText: '移除任务',
        cancelButtonText: '取消',
      },
    )
  } catch (action) {
    if (action === 'cancel' || action === 'close') return
    throw action
  }

  actionBusy.value = `delete:${task.task_id}`
  try {
    const response = await deleteDownloadTask(task.task_id)
    ElMessage.success(response.message || '任务已移除。')
    if (selectedTask.value?.task_id === task.task_id) {
      detailsVisible.value = false
    }
    await refreshSnapshot({ manual: true })
  } catch (error) {
    ElMessage.error(error.message || '移除任务失败。')
  } finally {
    actionBusy.value = ''
  }
}

async function clearCompleted() {
  if (!hasCompletedTasks.value || actionBusy.value) return
  try {
    await ElMessageBox.confirm(
      '将移除所有已完成和已跳过的任务记录，不会删除视频文件或 CSV 记录。是否继续？',
      '确认清理已完成任务',
      {
        type: 'warning',
        confirmButtonText: '清理任务',
        cancelButtonText: '取消',
      },
    )
  } catch (action) {
    if (action === 'cancel' || action === 'close') return
    throw action
  }

  actionBusy.value = 'clear'
  try {
    const response = await clearCompletedDownloads()
    ElMessage.success(`已清理 ${response.data?.removed || 0} 个任务。`)
    await refreshSnapshot({ manual: true })
  } catch (error) {
    ElMessage.error(error.message || '清理任务失败。')
  } finally {
    actionBusy.value = ''
  }
}

onMounted(() => {
  pageVisible.value = !document.hidden
  document.addEventListener('visibilitychange', handleVisibilityChange)
  refreshSnapshot({ manual: true })
})

onUnmounted(() => {
  disposed = true
  clearAutoRefreshTimer()
  snapshotController?.abort()
  document.removeEventListener('visibilitychange', handleVisibilityChange)
})
</script>

<template>
  <div class="page-view">

    <div class="page-body">
      <section class="downloads-heading">
        <div>
          <p class="section-kicker">任务队列</p>
          <h2>下载管理</h2>
          <p class="downloads-subtitle">管理下载队列、任务状态和失败重试。</p>
        </div>
        <div class="heading-actions">
          <ElTag :type="pageVisible ? 'success' : 'info'" effect="plain">
            {{ pageVisible ? '自动刷新 3s' : '后台暂停刷新' }}
          </ElTag>
          <span class="last-updated">最近更新 {{ lastUpdatedLabel }}</span>
          <ElButton :icon="Refresh" :loading="refreshing" @click="refreshSnapshot({ manual: true })">
            刷新
          </ElButton>
        </div>
      </section>

      <ElAlert
        v-if="errorMessage"
        class="downloads-alert"
        title="下载队列请求失败"
        :description="dataMayBeStale ? `${errorMessage} 当前数据可能已过期。` : errorMessage"
        type="error"
        show-icon
        :closable="false"
      />

      <section class="queue-panel">
        <div class="queue-panel__heading">
          <div>
            <div class="queue-title-row">
              <span class="queue-dot" :class="{ active: queueRunning, stopping: queueStopping }" />
              <h3>下载队列</h3>
              <ElTag :type="queueStatusType" effect="light">{{ queueStatusLabel }}</ElTag>
            </div>
            <p class="queue-note">
              线程 {{ snapshot.queue.thread_alive ? '已连接' : '未运行' }}
              <span v-if="snapshot.queue.forced_task_count"> · 强制任务 {{ snapshot.queue.forced_task_count }}</span>
            </p>
          </div>
          <div class="queue-actions">
            <ElButton
              type="primary"
              :icon="VideoPlay"
              :disabled="!canStartQueue"
              :loading="actionBusy === 'start'"
              @click="startQueue"
            >
              开始队列
            </ElButton>
            <ElButton
              :icon="VideoPause"
              :disabled="!canStopQueue"
              :loading="actionBusy === 'stop'"
              @click="stopQueue"
            >
              停止队列
            </ElButton>
          </div>
        </div>

        <div class="queue-stat-grid">
          <article class="queue-stat">
            <span>等待任务</span>
            <strong>{{ snapshot.counts.waiting || 0 }}</strong>
          </article>
          <article class="queue-stat">
            <span>正在执行</span>
            <strong>{{ snapshot.counts.running || 0 }}</strong>
          </article>
          <article class="queue-stat">
            <span>已完成</span>
            <strong>{{ completedCount }}</strong>
          </article>
          <article class="queue-stat" data-tone="danger">
            <span>失败任务</span>
            <strong>{{ snapshot.counts.failed || 0 }}</strong>
          </article>
        </div>

        <ElAlert
          v-if="snapshot.counts.interrupted"
          :title="`检测到 ${snapshot.counts.interrupted} 个中断任务`"
          description="请在任务列表中查看并重试需要继续的任务。"
          type="warning"
          :closable="false"
          show-icon
        />
        <ElAlert
          v-else-if="queueStopping"
          title="停止信号已发送"
          description="当前任务会在安全边界结束，队列不会强制中断正在写入的视频文件。"
          type="warning"
          :closable="false"
          show-icon
        />
      </section>

      <section class="task-entry-grid">
        <article class="entry-panel">
          <div class="entry-panel__heading">
            <div>
              <h3>加入下载队列</h3>
              <p>任务添加后保持等待，直到手动启动队列。</p>
            </div>
            <span class="entry-icon"><ElIcon><Plus /></ElIcon></span>
          </div>
          <div class="entry-form">
            <ElInput v-model="addForm.videoId" clearable placeholder="视频 ID" />
            <ElInput v-model="addForm.title" clearable placeholder="视频标题（可选）" />
            <ElButton
              type="primary"
              :icon="Plus"
              :disabled="Boolean(actionBusy)"
              :loading="actionBusy === 'add'"
              @click="submitAddTask"
            >
              加入队列
            </ElButton>
          </div>
        </article>

        <article class="entry-panel">
          <div class="entry-panel__heading">
            <div>
              <h3>立即下载</h3>
              <p>任务加入后立即请求启动下载队列。</p>
            </div>
            <span class="entry-icon accent"><ElIcon><Download /></ElIcon></span>
          </div>
          <div class="entry-form">
            <ElInput v-model="immediateForm.videoId" clearable placeholder="视频 ID" />
            <ElInput v-model="immediateForm.title" clearable placeholder="视频标题（可选）" />
            <div class="immediate-actions">
              <ElCheckbox v-model="immediateForm.force">强制重新下载</ElCheckbox>
              <ElButton
                type="primary"
                :icon="Download"
                :disabled="Boolean(actionBusy)"
                :loading="actionBusy === 'immediate'"
                @click="submitImmediateDownload"
              >
                立即下载
              </ElButton>
            </div>
          </div>
        </article>
      </section>

      <section class="tasks-panel">
        <div class="tasks-heading">
          <div>
            <h3>任务列表</h3>
            <p>共 {{ snapshot.total }} 条任务记录</p>
          </div>
          <ElButton
            :icon="Delete"
            :disabled="!hasCompletedTasks || Boolean(actionBusy)"
            :loading="actionBusy === 'clear'"
            @click="clearCompleted"
          >
            清理已完成
          </ElButton>
        </div>

        <div class="task-table-shell">
          <ElTable
            v-loading="loading"
            :data="snapshot.items"
            row-key="task_id"
            max-height="560"
            empty-text="下载队列暂无任务"
            class="task-table"
          >
            <ElTableColumn label="任务标题" min-width="300" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="task-title">{{ row.title || '无标题' }}</div>
                <div class="task-subtitle">{{ row.retry_count ? `重试 ${row.retry_count} 次` : '首次执行' }}</div>
              </template>
            </ElTableColumn>
            <ElTableColumn label="任务 / 视频 ID" min-width="235">
              <template #default="{ row }">
                <div class="task-id">{{ row.task_id }}</div>
                <div class="video-id">{{ row.video_id || '—' }}</div>
              </template>
            </ElTableColumn>
            <ElTableColumn label="状态" width="112">
              <template #default="{ row }">
                <ElTag :type="statusMeta(row.status).type" effect="light">
                  {{ statusMeta(row.status).label }}
                </ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn label="进度" min-width="210">
              <template #default="{ row }">
                <ElProgress
                  :percentage="normalizeProgress(row.progress)"
                  :stroke-width="7"
                  :status="row.status === 'failed' ? 'exception' : row.status === 'success' ? 'success' : undefined"
                />
                <div class="progress-message">{{ progressMessage(row) }}</div>
              </template>
            </ElTableColumn>
            <ElTableColumn label="状态信息" min-width="230" show-overflow-tooltip>
              <template #default="{ row }">
                <span :class="{ 'task-error': taskIssue(row) }">{{ taskIssue(row) || row.message || '—' }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn label="更新时间" width="172">
              <template #default="{ row }">
                <span class="task-time">{{ formatDateTime(row.updated_at || row.created_at) }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn label="操作" width="164" fixed="right">
              <template #default="{ row }">
                <div class="row-actions">
                  <ElTooltip content="查看详情" placement="top">
                    <ElButton
                      circle
                      size="small"
                      :icon="View"
                      aria-label="查看任务详情"
                      @click="openTaskDetails(row)"
                    />
                  </ElTooltip>
                  <ElTooltip content="重试任务" placement="top">
                    <ElButton
                      circle
                      size="small"
                      :icon="RefreshRight"
                      aria-label="重试任务"
                      :disabled="!canRetry(row) || Boolean(actionBusy)"
                      :loading="actionBusy === `retry:${row.task_id}`"
                      @click="retryTask(row)"
                    />
                  </ElTooltip>
                  <ElTooltip content="移除任务" placement="top">
                    <ElButton
                      circle
                      size="small"
                      type="danger"
                      plain
                      :icon="Delete"
                      aria-label="移除任务"
                      :disabled="!canDelete(row) || Boolean(actionBusy)"
                      :loading="actionBusy === `delete:${row.task_id}`"
                      @click="removeTask(row)"
                    />
                  </ElTooltip>
                </div>
              </template>
            </ElTableColumn>
          </ElTable>
        </div>
      </section>
    </div>

    <ElDialog v-model="detailsVisible" title="下载任务详情" width="min(620px, calc(100vw - 32px))">
      <div v-loading="detailsLoading">
        <template v-if="selectedTask">
          <ElDescriptions :column="1" border>
            <ElDescriptionsItem label="任务 ID">{{ selectedTask.task_id }}</ElDescriptionsItem>
            <ElDescriptionsItem label="视频 ID">{{ selectedTask.video_id || '—' }}</ElDescriptionsItem>
            <ElDescriptionsItem label="标题">{{ selectedTask.title || '无标题' }}</ElDescriptionsItem>
            <ElDescriptionsItem label="状态">
              <ElTag :type="statusMeta(selectedTask.status).type" effect="light">
                {{ statusMeta(selectedTask.status).label }}
              </ElTag>
            </ElDescriptionsItem>
            <ElDescriptionsItem label="重试次数">{{ selectedTask.retry_count || 0 }}</ElDescriptionsItem>
            <ElDescriptionsItem label="创建时间">{{ formatDateTime(selectedTask.created_at) }}</ElDescriptionsItem>
            <ElDescriptionsItem label="更新时间">{{ formatDateTime(selectedTask.updated_at) }}</ElDescriptionsItem>
            <ElDescriptionsItem label="错误信息">{{ selectedTask.error || '无' }}</ElDescriptionsItem>
          </ElDescriptions>

          <div class="details-progress">
            <div class="details-progress__heading">
              <strong>下载进度</strong>
              <span>{{ normalizeProgress(selectedTask.progress) }}%</span>
            </div>
            <ElProgress
              :percentage="normalizeProgress(selectedTask.progress)"
              :stroke-width="10"
              :status="selectedTask.status === 'failed' ? 'exception' : selectedTask.status === 'success' ? 'success' : undefined"
            />
          </div>

          <div class="progress-details">
            <div class="details-progress__heading">
              <strong>进度详情</strong>
              <span>{{ selectedTask.progress_details?.stage || '无阶段信息' }}</span>
            </div>
            <div class="progress-detail-grid">
              <span>已下载</span>
              <strong>{{ formatBytes(selectedTask.progress_details?.downloaded) }}</strong>
              <span>总量</span>
              <strong>{{ formatBytes(selectedTask.progress_details?.total) }}</strong>
              <span>消息</span>
              <strong>{{ selectedTask.progress_details?.message || selectedTask.message || '—' }}</strong>
            </div>
          </div>
        </template>
      </div>
    </ElDialog>
  </div>
</template>

<style scoped>
.workspace--downloads {
  width: min(1320px, calc(100% - 40px));
}

.downloads-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 20px;
}

.downloads-heading .section-kicker {
  color: #748187;
}

.downloads-heading h2 {
  margin: 0;
  color: var(--ink);
  font-size: 28px;
  letter-spacing: 0;
}

.downloads-subtitle {
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

.downloads-alert {
  margin-bottom: 16px;
}

.queue-panel,
.entry-panel,
.tasks-panel {
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 8px;
}

.queue-panel {
  padding: 20px;
}

.queue-panel__heading,
.tasks-heading,
.entry-panel__heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}

.queue-title-row {
  display: flex;
  align-items: center;
  gap: 9px;
}

.queue-title-row h3,
.entry-panel h3,
.tasks-heading h3 {
  margin: 0;
  color: var(--ink);
  font-size: 17px;
  letter-spacing: 0;
}

.queue-dot {
  width: 8px;
  height: 8px;
  background: #9ba6aa;
  border-radius: 50%;
}

.queue-dot.active {
  background: var(--teal);
  box-shadow: 0 0 0 4px rgba(42, 148, 136, 0.1);
}

.queue-dot.stopping {
  background: #d79b2e;
  box-shadow: 0 0 0 4px rgba(215, 155, 46, 0.1);
}

.queue-note,
.entry-panel p,
.tasks-heading p {
  margin: 7px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.queue-actions {
  display: flex;
  gap: 10px;
}

.queue-stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin: 20px 0 16px;
}

.queue-stat {
  padding: 14px 16px;
  background: #f7f9f8;
  border: 1px solid #e2e8e6;
  border-radius: 6px;
}

.queue-stat span {
  display: block;
  color: var(--muted);
  font-size: 12px;
}

.queue-stat strong {
  display: block;
  margin-top: 7px;
  color: var(--ink);
  font-size: 26px;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.queue-stat[data-tone="danger"] strong {
  color: #c94f4a;
}

.task-entry-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-top: 14px;
}

.entry-panel {
  padding: 18px;
}

.entry-icon {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  color: #526168;
  background: #f0f3f2;
  border-radius: 7px;
}

.entry-icon.accent {
  color: #b9504b;
  background: #fff0ef;
}

.entry-form {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 1.4fr) auto;
  gap: 10px;
  margin-top: 16px;
}

.entry-form .el-input {
  width: 100%;
}

.immediate-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  grid-column: 1 / -1;
  justify-content: space-between;
}

.tasks-panel {
  margin-top: 14px;
  padding: 18px;
}

.tasks-heading {
  align-items: flex-end;
  margin-bottom: 14px;
}

.task-table-shell {
  overflow-x: auto;
}

.task-table {
  min-width: 1260px;
}

.task-title {
  color: #25333a;
  font-weight: 600;
}

.task-subtitle {
  margin-top: 4px;
  color: #899399;
  font-size: 11px;
}

.task-id,
.video-id,
.task-time {
  color: #58666d;
  font-family: "Cascadia Mono", "Consolas", monospace;
  font-size: 11px;
}

.video-id {
  margin-top: 4px;
  color: #879196;
}

.progress-message {
  margin-top: 4px;
  overflow: hidden;
  color: #77838a;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-error {
  color: #c94f4a;
}

.row-actions {
  display: flex;
  gap: 6px;
}

.details-progress,
.progress-details {
  margin-top: 18px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
}

.details-progress__heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 10px;
  color: #536168;
  font-size: 12px;
}

.details-progress__heading strong {
  color: var(--ink);
  font-size: 13px;
}

.progress-detail-grid {
  display: grid;
  grid-template-columns: 90px minmax(0, 1fr);
  gap: 8px 12px;
  font-size: 12px;
}

.progress-detail-grid span {
  color: var(--muted);
}

.progress-detail-grid strong {
  color: #304047;
  font-weight: 500;
  overflow-wrap: anywhere;
}

@media (max-width: 980px) {
  .task-entry-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .workspace--downloads {
    width: min(100% - 24px, 1320px);
  }

  .downloads-heading,
  .queue-panel__heading,
  .tasks-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .heading-actions,
  .queue-actions {
    width: 100%;
    flex-wrap: wrap;
  }

  .queue-actions .el-button {
    flex: 1;
  }

  .queue-stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .entry-form {
    grid-template-columns: 1fr;
  }

  .immediate-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .tasks-panel {
    padding: 14px;
  }
}
</style>
