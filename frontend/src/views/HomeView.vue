<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import {
  ArrowDown,
  ArrowRight,
  Key,
  Lock,
  Monitor,
  Refresh,
  UserFilled,
  VideoPlay,
} from '@element-plus/icons-vue'

import {
  getAuthStatus,
  isAuthRequiredError,
  isLoginBrowserClosedError,
  login,
  resetAuth,
} from '../api/auth'
import { configuredApiBaseUrl, usingDesktopApiBridge } from '../api'
import { getHealth } from '../api/system'
import { getVideoSummary } from '../api/videos'
import PageHeader from '../components/PageHeader.vue'
import StatusTile from '../components/StatusTile.vue'

const checking = ref(true)
const healthState = ref('checking')
const authState = ref('checking')
const healthResponse = ref(null)
const authResponse = ref(null)
const lastCheckedAt = ref(null)
const lastCheckDurationMs = ref(null)
const errorMessage = ref('')
const loginPending = ref(false)
const loginError = ref('')
const loginNotice = ref('')
const loginNoticeType = ref('info')

const summary = reactive({
  total: 0,
  pending_total: 0,
  waiting_tasks: 0,
  failed_tasks: 0,
})
const summaryLoading = ref(true)
const summaryError = ref('')

const healthTone = computed(() => {
  if (healthState.value === 'running') return 'success'
  if (healthState.value === 'failed') return 'danger'
  return 'info'
})

const healthTag = computed(() => {
  if (healthState.value === 'running') return '运行正常'
  if (healthState.value === 'failed') return '连接失败'
  return '正在检测'
})

const healthTitle = computed(() => {
  if (healthState.value === 'running') return 'FastAPI 服务在线'
  if (healthState.value === 'failed') return '无法连接 FastAPI'
  return '正在检查服务状态'
})

const healthDescription = computed(() => {
  if (healthState.value === 'running') {
    return healthResponse.value?.message || '健康检查请求成功。'
  }
  if (healthState.value === 'failed') return `确认 Electron 管理的 Backend 服务可用：${configuredApiBaseUrl}`
  return '正在请求健康检查接口。'
})

const authTone = computed(() => {
  if (authState.value === 'ready') return 'success'
  if (authState.value === 'failed') return 'danger'
  if (authState.value === 'missing') return 'warning'
  return 'info'
})

const authTag = computed(() => {
  if (authState.value === 'ready') return '会话已保存'
  if (authState.value === 'missing') return '尚未登录'
  if (authState.value === 'failed') return '状态未知'
  return '正在读取'
})

const authTitle = computed(() => {
  if (authState.value === 'ready') return '本地登录会话已就绪'
  if (authState.value === 'missing') return '尚未检测到登录会话'
  if (authState.value === 'failed') return '无法读取登录状态'
  return '正在读取登录状态'
})

const authDescription = computed(() => {
  if (authState.value === 'ready') return '后端已检测到持久化抖音 Profile，可进入抓取页面。'
  if (authState.value === 'missing') return '点击下方登录按钮，在弹出的浏览器中完成抖音扫码登录。'
  if (authState.value === 'failed') return '登录状态接口请求失败，当前状态未知，不能按未登录处理。'
  return '正在请求 Auth API。'
})

const loginButtonLabel = computed(() => {
  if (loginPending.value) return '等待登录完成'
  if (authState.value === 'ready') return '重新登录'
  return '登录抖音'
})

const authActionLabel = computed(() => {
  if (authState.value === 'checking') return '正在检查'
  if (authState.value === 'failed') return '重新检查'
  return loginButtonLabel.value
})

const canStartLogin = computed(
  () =>
    !loginPending.value &&
    !checking.value &&
    healthState.value === 'running' &&
    ['ready', 'missing'].includes(authState.value),
)

const lastCheckedLabel = computed(() => {
  if (!lastCheckedAt.value) return '尚未检测'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(lastCheckedAt.value)
})

const healthResult = computed(() => {
  if (checking.value) return '检测中'
  if (healthState.value === 'failed') return '连接失败'
  return healthResponse.value?.data?.status || '未知'
})

const authResult = computed(() => {
  if (checking.value) return '读取中'
  if (authState.value === 'failed') return '读取失败'
  return authResponse.value?.data?.login_ready ? 'login_ready=true' : 'login_ready=false'
})

const requestMode = computed(() =>
  usingDesktopApiBridge ? 'Electron Bridge API' : import.meta.env.DEV ? 'Vite 开发代理 /backend' : configuredApiBaseUrl,
)

const diagnosticSummary = computed(() => {
  if (checking.value) return '正在检查本地服务与登录会话'
  if (errorMessage.value) return '连接异常 · 展开查看错误信息'
  if (!lastCheckedAt.value) return '尚未执行检测'
  const latency = lastCheckDurationMs.value
  return latency ? `API 响应正常 · ${latency} ms` : 'API 响应正常'
})

async function checkServices() {
  const startedAt = performance.now()
  checking.value = true
  healthState.value = 'checking'
  authState.value = 'checking'
  errorMessage.value = ''

  const [healthRequest, authRequest] = await Promise.allSettled([
    getHealth(),
    getAuthStatus(),
  ])

  if (healthRequest.status === 'fulfilled') {
    healthResponse.value = healthRequest.value
    healthState.value = 'running'
  } else {
    healthResponse.value = null
    healthState.value = 'failed'
    errorMessage.value = healthRequest.reason.message
  }

  if (authRequest.status === 'fulfilled') {
    authResponse.value = authRequest.value
    authState.value = authRequest.value?.data?.login_ready ? 'ready' : 'missing'
  } else {
    authResponse.value = null
    authState.value = 'failed'
    errorMessage.value = [errorMessage.value, authRequest.reason.message]
      .filter(Boolean)
      .join('；')
  }

  lastCheckDurationMs.value = Math.max(1, Math.round(performance.now() - startedAt))
  lastCheckedAt.value = new Date()
  checking.value = false
}

function applyAuthResponse(response) {
  authResponse.value = response
  authState.value = response?.data?.login_ready ? 'ready' : 'missing'
}

async function loadSummary() {
  summaryLoading.value = true
  summaryError.value = ''

  try {
    const response = await getVideoSummary()
    Object.assign(summary, {
      total: response.data?.total || 0,
      pending_total: response.data?.pending_total || 0,
      waiting_tasks: response.data?.waiting_tasks || 0,
      failed_tasks: response.data?.failed_tasks || 0,
    })
  } catch (error) {
    summaryError.value = error.message || '工作区概况加载失败。'
  } finally {
    summaryLoading.value = false
  }
}

async function performLogin() {
  if (!canStartLogin.value || loginPending.value) return
  loginPending.value = true
  loginError.value = ''
  loginNoticeType.value = 'info'
  loginNotice.value = '请在弹出的浏览器中完成抖音扫码登录，扫码成功后直接关闭浏览器窗口即可保存缓存。'

  try {
    const response = await login()
    let statusPayload = null
    try {
      statusPayload = await getAuthStatus()
      applyAuthResponse(statusPayload)
    } catch (statusError) {
      authResponse.value = null
      authState.value = 'failed'
      loginNoticeType.value = 'warning'
      loginNotice.value = `登录流程已结束，但无法重新读取登录状态：${statusError.message}`
      return
    }

    if (statusPayload?.data?.login_ready) {
      loginNoticeType.value = 'success'
      loginNotice.value = '登录流程已结束，后端检测到本地登录会话。'
    } else {
      loginNoticeType.value = 'warning'
      loginNotice.value = response?.data?.profile_saved
        ? '登录流程已结束，浏览器 Profile 已保存，但登录状态接口仍显示未登录。请重新登录。'
        : '登录流程已结束，但后端未确认登录状态。请重新登录。'
    }
    lastCheckedAt.value = new Date()
  } catch (error) {
    loginNotice.value = ''
    if (isLoginBrowserClosedError(error)) {
      loginError.value = ''
      loginNoticeType.value = 'info'
      loginNotice.value = '登录浏览器已关闭，登录流程已取消。当前登录状态未改变。'
      await checkServices()
      return
    }
    const message = isAuthRequiredError(error)
      ? '登录状态无效，请重新完成抖音登录。'
      : error.message || '登录请求失败。'
    loginError.value = /Profile|占用|浏览器已关闭/.test(message)
      ? `${message} 浏览器可能仍在关闭中，请稍后重试；后端会在重试前检查本项目残留浏览器资源。`
      : message
  } finally {
    loginPending.value = false
  }
}

async function performResetLogin() {
  if (!canStartLogin.value || loginPending.value) return
  loginPending.value = true
  loginError.value = ''
  loginNotice.value = ''

  try {
    const response = await resetAuth()
    applyAuthResponse(response)
    loginNoticeType.value = 'info'
    loginNotice.value = '已清空本地登录缓存，请点击重新扫码登录。'
    lastCheckedAt.value = new Date()
  } catch (error) {
    loginError.value = error.message || '清空登录缓存失败。'
  } finally {
    loginPending.value = false
  }
}

onMounted(() => {
  checkServices()
  loadSummary()
})
</script>

<template>
  <div class="page-view">
    <PageHeader
      title="工作台状态"
      description="检查本地 Runtime、FastAPI 服务和抖音登录会话。"
    >
      <template #actions>
        <span
          class="inline-flex items-center gap-2 rounded-full border border-line bg-panel px-3 py-1.5 text-[11px] font-medium text-muted-strong shadow-panel"
        >
          <span class="h-1.5 w-1.5 rounded-full bg-brand" aria-hidden="true" />
          上次检测 {{ lastCheckedLabel }}
        </span>
        <ElTooltip content="刷新状态" placement="bottom">
          <ElButton
            circle
            :icon="Refresh"
            :loading="checking"
            aria-label="刷新状态"
            @click="checkServices"
          />
        </ElTooltip>
      </template>
    </PageHeader>

    <ElAlert
      v-if="errorMessage"
      class="mb-4"
      title="连接异常"
      :description="errorMessage"
      type="error"
      show-icon
      :closable="false"
    />

    <section
      class="overflow-hidden rounded-panel bg-panel shadow-panel ring-1 ring-line/40"
      aria-label="系统运行态势"
    >
      <div class="flex flex-wrap items-start justify-between gap-4 border-b border-line px-5 py-4">
        <div>
          <h2 class="text-[16px] font-semibold text-ink">系统运行态势</h2>
          <p class="mt-1 text-[12px] text-muted">本地 Runtime 与抖音账号均就绪后，即可开始抓取和下载。</p>
        </div>
        <span
          class="inline-flex items-center gap-1.5 rounded-full border border-brand/20 bg-brand-soft px-2.5 py-1 text-[11px] font-semibold text-brand"
        >
          <ElIcon><Lock /></ElIcon>
          本地模式
        </span>
      </div>

      <div class="grid grid-cols-1 divide-y divide-line md:grid-cols-2 md:divide-x md:divide-y-0">
        <StatusTile
          label="FastAPI 服务状态"
          :tag="healthTag"
          :title="healthTitle"
          :description="healthDescription"
          :tone="healthTone"
          :loading="healthState === 'checking'"
          :icon="Monitor"
        />
        <StatusTile
          label="抖音登录状态"
          :tag="authTag"
          :title="authTitle"
          :description="authDescription"
          :tone="authTone"
          :loading="authState === 'checking'"
          :icon="UserFilled"
        >
          <template #actions>
            <div class="flex flex-wrap gap-2 max-[520px]:flex-col">
              <ElButton
                v-if="authState === 'ready'"
                type="primary"
                :icon="VideoPlay"
                :disabled="loginPending"
                class="!ml-0 max-[520px]:w-full"
                @click="$router.push('/capture')"
              >
                进入抓取管理
              </ElButton>
              <ElButton
                v-else
                type="primary"
                :icon="authState === 'failed' ? Refresh : Key"
                :loading="authState === 'failed' ? checking : loginPending"
                :disabled="authState === 'checking' || (authState === 'missing' && !canStartLogin)"
                class="!ml-0 max-[520px]:w-full"
                @click="authState === 'failed' ? checkServices() : performLogin()"
              >
                {{ authActionLabel }}
              </ElButton>
              <ElButton
                v-if="authState === 'ready'"
                :loading="loginPending"
                :disabled="!canStartLogin"
                class="!ml-0 max-[520px]:w-full"
                @click="performLogin"
              >
                重新登录
              </ElButton>
              <ElButton
                v-if="authState === 'ready'"
                :disabled="!canStartLogin"
                class="!ml-0 max-[520px]:w-full"
                @click="performResetLogin"
              >
                清空缓存
              </ElButton>
            </div>
          </template>
        </StatusTile>
      </div>

      <div
        class="flex flex-wrap items-center justify-between gap-2 border-t border-line bg-panel-soft px-5 py-3 text-[11px] text-muted-strong"
      >
        <span class="inline-flex items-center gap-1.5">
          <span class="h-1.5 w-1.5 rounded-full bg-success" aria-hidden="true" />
          数据与视频文件仅保存在本机
        </span>
        <span>登录 Profile 保存在本地 Runtime</span>
      </div>
    </section>

    <div v-if="loginPending || loginNotice || loginError" class="mt-4 grid gap-2">
      <ElAlert
        v-if="loginPending"
        title="请在浏览器中完成扫码登录"
        description="扫码登录成功后，直接关闭弹出的浏览器窗口即可自动保存缓存并返回。"
        type="info"
        :closable="false"
        show-icon
      />
      <ElAlert
        v-else-if="loginNotice"
        title="登录流程结果"
        :description="loginNotice"
        :type="loginNoticeType"
        :closable="false"
        show-icon
      />
      <ElAlert
        v-if="loginError"
        title="登录失败"
        :description="loginError"
        type="error"
        :closable="false"
        show-icon
      />
    </div>

    <details
      class="group mt-5 overflow-hidden rounded-panel bg-panel shadow-panel ring-1 ring-line/40"
      :open="Boolean(errorMessage)"
    >
      <summary
        class="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4 [&::-webkit-details-marker]:hidden"
      >
        <span class="grid gap-0.5">
          <strong class="text-[13px] font-semibold text-ink">连接诊断</strong>
          <small class="text-[11px] text-muted-strong">{{ diagnosticSummary }}</small>
        </span>
        <ElIcon class="shrink-0 text-muted-strong transition-transform duration-150 group-open:rotate-180">
          <ArrowDown />
        </ElIcon>
      </summary>

      <dl class="grid grid-cols-2 border-t border-line max-[720px]:grid-cols-1">
        <div class="border-b border-r border-line px-5 py-3 odd:border-r max-[720px]:border-r-0">
          <dt class="mb-1 text-[11px] font-semibold text-muted-strong">API 服务地址</dt>
          <dd class="break-words font-mono text-[11px] leading-5 text-muted">{{ configuredApiBaseUrl }}</dd>
        </div>
        <div class="border-b border-line px-5 py-3">
          <dt class="mb-1 text-[11px] font-semibold text-muted-strong">请求方式</dt>
          <dd class="break-words font-mono text-[11px] leading-5 text-muted">{{ requestMode }}</dd>
        </div>
        <div class="border-b border-r border-line px-5 py-3 max-[720px]:border-r-0">
          <dt class="mb-1 text-[11px] font-semibold text-muted-strong">健康检查结果</dt>
          <dd class="break-words font-mono text-[11px] leading-5 text-muted">{{ healthResult }}</dd>
        </div>
        <div class="border-b border-line px-5 py-3">
          <dt class="mb-1 text-[11px] font-semibold text-muted-strong">登录状态结果</dt>
          <dd class="break-words font-mono text-[11px] leading-5 text-muted">{{ authResult }}</dd>
        </div>
        <div class="col-span-2 px-5 py-3 max-[720px]:col-span-1">
          <dt class="mb-1 text-[11px] font-semibold text-muted-strong">错误信息</dt>
          <dd class="break-words font-mono text-[11px] leading-5 text-muted">{{ errorMessage || '无' }}</dd>
        </div>
      </dl>
    </details>

    <section class="mt-5 overflow-hidden rounded-panel bg-panel shadow-panel ring-1 ring-line/40">
      <div class="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4">
        <div>
          <h2 class="text-[16px] font-semibold text-ink">工作区概况</h2>
          <p class="mt-1 text-[12px] text-muted">
            {{ summaryError ? '概况暂不可用，服务恢复后可刷新。' : '本地视频与下载队列的当前快照。' }}
          </p>
        </div>
        <RouterLink
          to="/videos"
          class="inline-flex items-center gap-1.5 text-[12px] font-semibold text-brand no-underline hover:text-brand-strong"
        >
          打开视频库
          <ElIcon><ArrowRight /></ElIcon>
        </RouterLink>
      </div>

      <div class="grid grid-cols-2 gap-px bg-line xl:grid-cols-4">
        <RouterLink
          to="/videos"
          class="group min-h-[108px] bg-panel px-5 py-4 no-underline transition hover:bg-panel-soft"
        >
          <span class="flex items-center justify-between gap-3 text-[11px] font-semibold text-muted">
            视频库
            <ElIcon class="text-muted-strong transition group-hover:translate-x-0.5 group-hover:text-brand"><ArrowRight /></ElIcon>
          </span>
          <span v-if="summaryLoading" class="mt-3 block h-7 w-16 animate-pulse rounded bg-line/70" />
          <strong v-else class="mt-2 block text-[28px] font-semibold leading-none tabular-nums text-ink">
            {{ summaryError ? '—' : summary.total }}
          </strong>
          <small class="mt-1 block text-[11px] text-muted-strong">条本地记录</small>
        </RouterLink>

        <RouterLink
          to="/videos"
          class="group min-h-[108px] bg-panel px-5 py-4 no-underline transition hover:bg-panel-soft"
        >
          <span class="flex items-center justify-between gap-3 text-[11px] font-semibold text-muted">
            待下载
            <ElIcon class="text-muted-strong transition group-hover:translate-x-0.5 group-hover:text-brand"><ArrowRight /></ElIcon>
          </span>
          <span v-if="summaryLoading" class="mt-3 block h-7 w-16 animate-pulse rounded bg-line/70" />
          <strong v-else class="mt-2 block text-[28px] font-semibold leading-none tabular-nums text-warning">
            {{ summaryError ? '—' : summary.pending_total }}
          </strong>
          <small class="mt-1 block text-[11px] text-muted-strong">条视频等待下载</small>
        </RouterLink>

        <RouterLink
          to="/downloads"
          class="group min-h-[108px] bg-panel px-5 py-4 no-underline transition hover:bg-panel-soft"
        >
          <span class="flex items-center justify-between gap-3 text-[11px] font-semibold text-muted">
            队列待处理
            <ElIcon class="text-muted-strong transition group-hover:translate-x-0.5 group-hover:text-brand"><ArrowRight /></ElIcon>
          </span>
          <span v-if="summaryLoading" class="mt-3 block h-7 w-16 animate-pulse rounded bg-line/70" />
          <strong v-else class="mt-2 block text-[28px] font-semibold leading-none tabular-nums text-brand">
            {{ summaryError ? '—' : summary.waiting_tasks }}
          </strong>
          <small class="mt-1 block text-[11px] text-muted-strong">个任务等待执行</small>
        </RouterLink>

        <RouterLink
          to="/downloads"
          class="group min-h-[108px] bg-panel px-5 py-4 no-underline transition hover:bg-panel-soft"
        >
          <span class="flex items-center justify-between gap-3 text-[11px] font-semibold text-muted">
            下载失败
            <ElIcon class="text-muted-strong transition group-hover:translate-x-0.5 group-hover:text-brand"><ArrowRight /></ElIcon>
          </span>
          <span v-if="summaryLoading" class="mt-3 block h-7 w-16 animate-pulse rounded bg-line/70" />
          <strong
            v-else
            class="mt-2 block text-[28px] font-semibold leading-none tabular-nums"
            :class="summary.failed_tasks ? 'text-error' : 'text-ink'"
          >
            {{ summaryError ? '—' : summary.failed_tasks }}
          </strong>
          <small class="mt-1 block text-[11px] text-muted-strong">个任务需要处理</small>
        </RouterLink>
      </div>
    </section>
  </div>
</template>
