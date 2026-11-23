<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download, FolderOpened, Refresh, Switch } from '@element-plus/icons-vue'

import {
  chooseManagedDataDirectory,
  chooseManagedVideoDirectory,
  getBackendStoragePaths,
  getManagedStorageStatus,
  openManagedDirectory,
  storageControlsAvailable,
} from '../api/storage'
import PageHeader from '../components/PageHeader.vue'
import {
  cancelUpdateDownload,
  checkForUpdates,
  getLocalVersionInfo,
  getUpdateState,
  onUpdateStateChanged,
  quitAndInstallUpdate,
  startUpdateDownload,
  updateBridgeAvailable,
} from '../api/updates'

const loading = ref(true)
const refreshing = ref(false)
const actionBusy = ref('')
const errorMessage = ref('')
const managed = ref(null)
const versionInfo = ref(null)
const updateState = ref(null)
const updateActionBusy = ref('')
const backendPaths = ref(null)

const production = computed(() => Boolean(managed.value?.production))
const controlsAvailable = computed(
  () => storageControlsAvailable && production.value && !actionBusy.value,
)
const appDataRoot = computed(
  () => managed.value?.appDataRoot || backendPaths.value?.app_user_data_dir || '未配置',
)
const videoRoot = computed(
  () => managed.value?.videoRoot || backendPaths.value?.save_dir || '未配置',
)
const installRoot = computed(() => managed.value?.installRoot || '未知')

const updateStatus = computed(() => updateState.value?.status || 'IDLE')
const tagText = computed(() => versionInfo.value?.tag || '未标记')
const commitShort = computed(() => versionInfo.value?.commit ? versionInfo.value.commit.slice(0, 12) : '未知')
const updateSourceText = computed(() => versionInfo.value?.update_source?.configured ? versionInfo.value.update_source.repository : '未配置')
const isDownloading = computed(() => updateStatus.value === 'DOWNLOADING')
const updateStatusType = computed(() => {
  if (['UPDATE_AVAILABLE', 'READY_TO_INSTALL'].includes(updateStatus.value)) return 'warning'
  if (['UP_TO_DATE', 'INSTALLING'].includes(updateStatus.value)) return 'success'
  if (['SOURCE_NOT_CONFIGURED', 'NETWORK_ERROR', 'DOWNLOAD_CANCELLED'].includes(updateStatus.value)) return 'info'
  if (['MANIFEST_INVALID', 'SIGNATURE_INVALID', 'VERSION_UNSUPPORTED', 'DOWNLOAD_FAILED', 'INSTALL_FAILED'].includes(updateStatus.value)) return 'danger'
  return 'info'
})
const updateStatusText = computed(() => ({
  IDLE: '尚未检查更新。',
  CHECKING: '正在检查更新…',
  UP_TO_DATE: '当前已是最新版本。',
  UPDATE_AVAILABLE: '发现可用更新。',
  SOURCE_NOT_CONFIGURED: '公共更新仓库尚未配置。',
  NETWORK_ERROR: '无法连接更新源，请检查网络后重试。',
  MANIFEST_INVALID: '更新信息验证失败。',
  SIGNATURE_INVALID: '更新签名验证失败。',
  VERSION_UNSUPPORTED: '当前版本不满足此次更新要求。',
  DOWNLOADING: '正在下载完整安装包…',
  DOWNLOAD_FAILED: '下载失败，可以重新尝试。',
  DOWNLOAD_CANCELLED: '下载已取消。',
  READY_TO_INSTALL: '安装包已完成签名、大小和 SHA256 校验。',
  INSTALLING: '正在安全关闭软件并启动安装程序…',
  INSTALL_FAILED: '安装启动失败。',
}[updateStatus.value] || '未知更新状态。'))

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / (1024 ** index)).toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}

function formatDate(value) {
  if (!value) return '未知'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

async function loadUpdates() {
  if (!updateBridgeAvailable) return
  const [versionResult, stateResult] = await Promise.allSettled([
    getLocalVersionInfo(),
    getUpdateState(),
  ])
  if (versionResult.status === 'fulfilled') versionInfo.value = versionResult.value
  if (stateResult.status === 'fulfilled') updateState.value = stateResult.value
}

async function checkUpdate() {
  if (!updateBridgeAvailable || updateActionBusy.value) return
  updateActionBusy.value = 'check'
  try {
    updateState.value = await checkForUpdates()
  } catch (error) {
    ElMessage.error(error.message || '检查更新失败。')
  } finally {
    updateActionBusy.value = ''
  }
}

async function downloadUpdate() {
  if (!updateBridgeAvailable || updateActionBusy.value) return
  updateActionBusy.value = 'download'
  try {
    updateState.value = await startUpdateDownload()
  } catch (error) {
    ElMessage.error(error.message || '下载更新失败。')
  } finally {
    updateActionBusy.value = ''
  }
}

async function cancelDownload() {
  try {
    await cancelUpdateDownload()
  } catch (error) {
    ElMessage.error(error.message || '取消下载失败。')
  }
}

async function installUpdate() {
  if (updateActionBusy.value) return
  try {
    await ElMessageBox.confirm(
      '软件将在确认没有运行中的抓取或下载任务后安全关闭，并启动已验证的 NSIS 安装程序。',
      '退出软件并安装',
      { type: 'warning', confirmButtonText: '退出并安装', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  updateActionBusy.value = 'install'
  try {
    await quitAndInstallUpdate()
  } catch (error) {
    ElMessage.error(error.message || '启动安装程序失败。')
    updateActionBusy.value = ''
  }
}

const backendRows = computed(() => {
  if (!backendPaths.value) return []
  return [
    { label: '视频数据库目录', value: backendPaths.value.video_data_dir },
    { label: '任务数据目录', value: backendPaths.value.task_data_dir },
    { label: '登录 Profile', value: backendPaths.value.browser_profile_dir },
    { label: '日志目录', value: backendPaths.value.log_dir },
    { label: 'FFmpeg', value: backendPaths.value.ffmpeg_path },
  ]
})

async function loadStorage() {
  errorMessage.value = ''
  const [managedResult, backendResult] = await Promise.allSettled([
    getManagedStorageStatus(),
    getBackendStoragePaths(),
  ])

  if (managedResult.status === 'fulfilled') {
    managed.value = managedResult.value
  }
  if (backendResult.status === 'fulfilled') {
    backendPaths.value = backendResult.value.data
  }

  const errors = []
  if (managedResult.status === 'rejected') errors.push(managedResult.reason.message)
  if (backendResult.status === 'rejected') errors.push(backendResult.reason.message)
  errorMessage.value = errors.filter(Boolean).join('；')
}

async function refreshStorage() {
  refreshing.value = true
  try {
    await loadStorage()
    await loadUpdates()
  } finally {
    refreshing.value = false
  }
}

async function changeVideoDirectory() {
  if (!controlsAvailable.value) return
  try {
    await ElMessageBox.confirm(
              '此操作只影响之后创建的任务；历史视频不会移动，已入队任务仍使用入队时的目标目录。',
      '修改视频下载目录',
      {
        type: 'warning',
        confirmButtonText: '选择新目录',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }

  actionBusy.value = 'video'
  try {
    const status = await chooseManagedVideoDirectory()
    managed.value = status
    await loadStorage()
    ElMessage.success('视频下载目录已更新。')
  } catch (error) {
    ElMessage.error(error.message || '修改视频下载目录失败。')
  } finally {
    actionBusy.value = ''
  }
}

async function changeDataDirectory() {
  if (!controlsAvailable.value) return
  try {
    await ElMessageBox.confirm(
      '修改前会先停止 Backend，复制并逐文件校验数据，然后切换配置。原目录不会被删除。抓取或下载任务运行时不能迁移。',
      '迁移应用数据根目录',
      {
        type: 'warning',
        confirmButtonText: '选择并迁移',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }

  actionBusy.value = 'data'
  try {
    const status = await chooseManagedDataDirectory()
    managed.value = status
    await loadStorage()
    const migration = status?.migration
    ElMessage.success(
      migration
        ? `应用数据迁移完成：${migration.files} 个文件，${migration.bytes} 字节。`
        : '应用数据目录已更新。',
    )
  } catch (error) {
    ElMessage.error(error.message || '迁移应用数据目录失败。')
  } finally {
    actionBusy.value = ''
  }
}

async function openPath(target) {
  try {
    await openManagedDirectory(target)
  } catch (error) {
    ElMessage.error(error.message || '无法打开目录。')
  }
}

let unsubscribeUpdateState = null

onMounted(async () => {
  if (updateBridgeAvailable) {
    unsubscribeUpdateState = onUpdateStateChanged((state) => {
      updateState.value = state
    })
  }
  loading.value = true
  try {
    await loadStorage()
  } finally {
    loading.value = false
  }
})

onBeforeUnmount(() => {
  unsubscribeUpdateState?.()
})
</script>

<template>
  <div class="page-view">
    <PageHeader title="设置" description="管理本地存储位置、版本更新和运行数据目录。">
      <template #actions>
        <ElButton :icon="Refresh" :loading="refreshing" @click="refreshStorage">
          刷新
        </ElButton>
      </template>
    </PageHeader>

    <ElAlert
      v-if="errorMessage"
      class="mb-4"
      title="存储状态读取失败"
      :description="errorMessage"
      type="error"
      show-icon
      :closable="false"
    />

    <ElAlert
      v-if="!production"
      class="mb-4"
      title="开发环境路径"
      description="开发版继续使用项目开发配置；目录迁移仅在正式安装版中启用。"
      type="info"
      show-icon
      :closable="false"
    />

    <section v-loading="loading" class="storage-panel">
      <div class="storage-panel__heading">
        <div>
          <h2>存储管理</h2>
          <p>所有业务数据仅保存在本机，不会上传云端。</p>
        </div>
        <ElTag :type="production ? 'success' : 'info'" effect="plain">
          {{ production ? '正式版配置' : '开发环境' }}
        </ElTag>
      </div>

      <div class="storage-list">
        <article class="storage-row">
          <div class="storage-row__copy">
            <span>软件安装位置</span>
            <strong>{{ installRoot }}</strong>
            <small>由安装程序维护，仅以只读方式展示。</small>
          </div>
          <ElButton :icon="FolderOpened" @click="openPath(installRoot)">打开</ElButton>
        </article>

        <article class="storage-row">
          <div class="storage-row__copy">
            <span>应用数据根目录</span>
            <strong>{{ appDataRoot }}</strong>
            <small>登录 Profile、任务、数据库、CSV 和日志都位于该目录。</small>
          </div>
          <div class="storage-row__actions">
            <ElButton :icon="FolderOpened" @click="openPath(appDataRoot)">打开</ElButton>
            <ElButton
              type="primary"
              :icon="Switch"
              :disabled="!controlsAvailable"
              :loading="actionBusy === 'data'"
              @click="changeDataDirectory"
            >
              迁移
            </ElButton>
          </div>
        </article>

        <article class="storage-row">
          <div class="storage-row__copy">
            <span>视频下载目录</span>
            <strong>{{ videoRoot }}</strong>
            <small>修改后只影响新任务，历史视频和已入队任务的目标目录保持不变。</small>
          </div>
          <div class="storage-row__actions">
            <ElButton :icon="FolderOpened" @click="openPath(videoRoot)">打开</ElButton>
            <ElButton
              type="primary"
              :icon="Switch"
              :disabled="!controlsAvailable"
              :loading="actionBusy === 'video'"
              @click="changeVideoDirectory"
            >
              更改
            </ElButton>
          </div>
        </article>
      </div>
    </section>

    <section class="storage-panel mt-4">
      <div class="storage-panel__heading">
        <div>
          <h2>Backend 实际生效路径</h2>
          <p>以下路径来自 FastAPI Runtime 配置。</p>
        </div>
      </div>

      <div class="path-table">
        <div v-for="row in backendRows" :key="row.label" class="path-table__row">
          <span>{{ row.label }}</span>
          <strong>{{ row.value }}</strong>
        </div>
        <div v-if="!backendRows.length" class="path-table__empty">
          暂时无法读取 Backend 路径配置。
        </div>
      </div>
    </section>

    <section class="storage-panel mt-4 update-panel">
      <div class="storage-panel__heading">
        <div>
          <h2>关于软件与版本更新</h2>
          <p>当前安装版本来自应用元数据；更新仅接受已验证的完整 NSIS 安装包。</p>
        </div>
        <ElButton
          type="primary"
          :icon="Refresh"
          :loading="updateActionBusy === 'check' || updateStatus === 'CHECKING'"
          :disabled="!updateBridgeAvailable || ['DOWNLOADING', 'INSTALLING'].includes(updateStatus)"
          @click="checkUpdate"
        >
          检查更新
        </ElButton>
      </div>

      <div class="update-details">
        <div><span>软件名称</span><strong>抖音视频工作台</strong></div>
        <div><span>当前版本</span><strong>{{ versionInfo?.current_version || '读取失败' }}</strong></div>
        <div><span>更新通道</span><strong>{{ versionInfo?.channel || 'stable' }}</strong></div>
        <div><span>Git Tag</span><strong>{{ tagText }}</strong></div>
        <div><span>构建 commit</span><strong>{{ commitShort }}</strong></div>
        <div><span>构建时间</span><strong>{{ formatDate(versionInfo?.build_time) }}</strong></div>
        <div><span>Updater 版本</span><strong>{{ versionInfo?.updater_version || '0.0.0' }}</strong></div>
        <div><span>公共更新源</span><strong>{{ updateSourceText }}</strong></div>
      </div>

      <div class="update-status">
        <ElTag :type="updateStatusType" effect="plain">{{ updateStatus }}</ElTag>
        <span>{{ updateStatusText }}</span>
        <small v-if="updateState?.updated_at">检查/更新于 {{ formatDate(updateState.updated_at) }}</small>
      </div>

      <ElAlert
        v-if="updateState?.error_message"
        :title="updateState.error_code || '更新错误'"
        :description="updateState.error_message"
        :type="updateStatusType === 'danger' ? 'error' : 'warning'"
        show-icon
        :closable="false"
        class="mt-3"
      />

      <div v-if="updateStatus === 'UPDATE_AVAILABLE'" class="update-release">
        <div class="update-release__header">
          <div>
            <span>最新版本</span>
            <strong>{{ updateState.latest_version }}</strong>
          </div>
          <ElTag type="warning" effect="plain">完整安装包</ElTag>
        </div>
        <div class="update-release__meta">
          <span>发布时间：{{ formatDate(updateState.published_at) }}</span>
          <span>安装包大小：{{ formatBytes(updateState.package_size) }}</span>
          <span>通道：{{ updateState.channel }}</span>
        </div>
        <p class="update-release__notes">{{ updateState.release_notes || '本次发布未提供更新说明。' }}</p>
        <div class="update-actions">
          <ElButton type="primary" :icon="Download" :loading="updateActionBusy === 'download'" @click="downloadUpdate">
            下载更新
          </ElButton>
        </div>
      </div>

      <div v-if="isDownloading" class="download-progress">
        <div class="download-progress__header">
          <span>{{ formatBytes(updateState.downloaded_bytes) }} / {{ formatBytes(updateState.total_bytes) }}</span>
          <strong>{{ updateState.bytes_per_second ? formatBytes(updateState.bytes_per_second) + '/s' : '准备中' }}</strong>
        </div>
        <ElProgress :percentage="updateState.percent || 0" :stroke-width="8" />
        <div class="update-actions">
          <ElButton @click="cancelDownload">取消下载</ElButton>
        </div>
      </div>

      <div v-if="updateStatus === 'READY_TO_INSTALL'" class="ready-install">
        <ElTag type="success" effect="plain">安装包已验证</ElTag>
        <span>退出软件后将启动完整 NSIS 安装程序，用户可确认原安装目录并取消安装。</span>
        <ElButton type="primary" :loading="updateActionBusy === 'install'" @click="installUpdate">
          退出软件并安装
        </ElButton>
      </div>

      <div v-if="['DOWNLOAD_FAILED', 'DOWNLOAD_CANCELLED', 'INSTALL_FAILED'].includes(updateStatus)" class="update-actions">
        <ElButton v-if="updateStatus !== 'INSTALL_FAILED'" :icon="Download" @click="downloadUpdate">重新下载</ElButton>
        <ElButton v-if="updateStatus === 'INSTALL_FAILED'" type="primary" @click="installUpdate">重新启动安装</ElButton>
        <ElButton v-if="updateStatus === 'DOWNLOAD_CANCELLED'" @click="checkUpdate">重新检查</ElButton>
      </div>
    </section>
  </div>
</template>

<style scoped>
.storage-panel {
  padding: 20px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.storage-panel__heading,
.storage-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}

.storage-panel__heading {
  padding-bottom: 16px;
  border-bottom: 1px solid var(--line);
}

.storage-panel__heading h2 {
  margin: 0;
  color: var(--ink);
  font-size: 17px;
}

.storage-panel__heading p,
.storage-row__copy small {
  display: block;
  margin: 5px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.storage-list {
  display: grid;
}

.storage-row {
  padding: 18px 0;
  border-bottom: 1px solid var(--line);
}

.storage-row:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}

.storage-row__copy {
  min-width: 0;
}

.storage-row__copy > span {
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
}

.storage-row__copy strong {
  display: block;
  margin-top: 5px;
  overflow-wrap: anywhere;
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 500;
}

.storage-row__actions {
  display: flex;
  flex: none;
  gap: 8px;
}

.path-table {
  padding-top: 8px;
}

.path-table__row {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr);
  gap: 16px;
  padding: 10px 0;
  border-bottom: 1px solid var(--border-subtle);
}

.path-table__row span {
  color: var(--muted);
  font-size: 12px;
}

.path-table__row strong {
  overflow-wrap: anywhere;
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 500;
}

.path-table__empty {
  padding: 18px 0;
  color: var(--muted);
  font-size: 12px;
}

.update-details {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 24px;
}

.update-details > div {
  display: grid;
  grid-template-columns: 100px minmax(0, 1fr);
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--border-subtle);
}

.update-details span,
.update-status small,
.update-release__meta,
.update-release__header span {
  color: var(--muted);
  font-size: 12px;
}

.update-details strong {
  min-width: 0;
  overflow-wrap: anywhere;
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 500;
}

.update-status {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  padding-top: 16px;
}

.update-status > span {
  color: var(--ink);
  font-size: 13px;
}

.update-release {
  margin-top: 16px;
  padding-left: 14px;
  border-left: 3px solid var(--brand);
}

.update-release__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.update-release__header > div {
  display: grid;
  gap: 3px;
}

.update-release__header strong {
  color: var(--ink);
  font-size: 18px;
}

.update-release__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  padding-top: 10px;
}

.update-release__notes {
  margin: 12px 0 0;
  color: var(--ink);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.download-progress {
  display: grid;
  gap: 10px;
  margin-top: 16px;
}

.download-progress__header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  color: var(--muted);
  font-size: 12px;
}

.ready-install {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px 14px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
}

.ready-install > span {
  flex: 1 1 360px;
  color: var(--muted);
  font-size: 12px;
}

.update-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

@media (max-width: 760px) {

  .update-details {
    grid-template-columns: 1fr;
  }

  .storage-row,
  .storage-panel__heading {
    flex-direction: column;
  }

  .storage-row__actions {
    width: 100%;
  }

  .storage-row__actions :deep(.el-button) {
    flex: 1;
  }

  .path-table__row {
    grid-template-columns: 1fr;
    gap: 4px;
  }
}
</style>
