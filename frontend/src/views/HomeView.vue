<script setup>
import { computed, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'

import { getAuthStatus, isAuthRequiredError, login } from '../api/auth'
import { configuredApiBaseUrl } from '../api'
import { getHealth } from '../api/system'
import StatusTile from '../components/StatusTile.vue'

const checking = ref(true)
const healthState = ref('checking')
const authState = ref('checking')
const healthResponse = ref(null)
const authResponse = ref(null)
const lastCheckedAt = ref(null)
const errorMessage = ref('')
const loginPending = ref(false)
const loginError = ref('')
const loginNotice = ref('')
const loginNoticeType = ref('info')

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
  if (healthState.value === 'failed') return '确认后端服务已在本机 8000 端口启动。'
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
  import.meta.env.DEV ? 'Vite 开发代理 /backend' : '直连 API',
)

async function checkServices() {
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

  lastCheckedAt.value = new Date()
  checking.value = false
}

function applyAuthResponse(response) {
  authResponse.value = response
  authState.value = response?.data?.login_ready ? 'ready' : 'missing'
}

async function performLogin() {
  if (!canStartLogin.value || loginPending.value) return
  loginPending.value = true
  loginError.value = ''
  loginNoticeType.value = 'info'
  loginNotice.value = '请在弹出的浏览器中完成抖音扫码或登录，处理完成前请勿关闭页面。'

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

onMounted(checkServices)
</script>

<template>
  <div class="page-view">

    <div class="page-body">
      <section class="workspace-heading">
        <div>
          <p class="section-kicker">服务联调</p>
          <h2>API 连接状态</h2>
        </div>
        <ElButton
          type="primary"
          :icon="Refresh"
          :loading="checking"
          @click="checkServices"
        >
          重新检测
        </ElButton>
      </section>

      <ElAlert
        v-if="errorMessage"
        class="connection-alert"
        title="连接异常"
        :description="errorMessage"
        type="error"
        show-icon
        :closable="false"
      />

      <section class="status-grid" aria-label="服务状态">
        <StatusTile
          label="FastAPI 服务状态"
          :tag="healthTag"
          :title="healthTitle"
          :description="healthDescription"
          :tone="healthTone"
          :loading="healthState === 'checking'"
        />
        <StatusTile
          label="抖音登录状态"
          :tag="authTag"
          :title="authTitle"
          :description="authDescription"
          :tone="authTone"
          :loading="authState === 'checking'"
        />
      </section>

      <section class="login-panel" aria-label="抖音登录">
        <div class="login-copy">
          <p class="section-kicker">抖音账号</p>
          <h3>{{ authTitle }}</h3>
          <p>{{ authDescription }}</p>
        </div>
        <div class="login-actions">
          <ElTag :type="authTone" effect="light">{{ authTag }}</ElTag>
          <ElButton
            type="primary"
            :loading="loginPending"
            :disabled="!canStartLogin"
            @click="performLogin"
          >
            {{ loginButtonLabel }}
          </ElButton>
          <ElButton :loading="checking" :disabled="loginPending" @click="checkServices">
            检查状态
          </ElButton>
        </div>

        <ElAlert
          v-if="loginPending"
          class="login-feedback"
          title="请在浏览器中完成登录"
          description="扫码或登录完成后，后端会结束登录流程；页面随后重新检查真实登录状态。"
          type="info"
          :closable="false"
          show-icon
        />
        <ElAlert
          v-else-if="loginNotice"
          class="login-feedback"
          title="登录流程结果"
          :description="loginNotice"
          :type="loginNoticeType"
          :closable="false"
          show-icon
        />
        <ElAlert
          v-if="loginError"
          class="login-feedback"
          title="登录失败"
          :description="loginError"
          type="error"
          :closable="false"
          show-icon
        />
      </section>

      <section class="connection-panel">
        <div class="panel-heading">
          <div>
            <p class="section-kicker">连接详情</p>
            <h3>最近一次检测</h3>
          </div>
          <span class="connection-time">{{ lastCheckedLabel }}</span>
        </div>

        <ElDescriptions :column="1" border class="connection-descriptions">
          <ElDescriptionsItem label="API 服务地址">
            {{ configuredApiBaseUrl }}
          </ElDescriptionsItem>
          <ElDescriptionsItem label="请求方式">
            {{ requestMode }}
          </ElDescriptionsItem>
          <ElDescriptionsItem label="健康检查结果">
            {{ healthResult }}
          </ElDescriptionsItem>
          <ElDescriptionsItem label="登录状态结果">
            {{ authResult }}
          </ElDescriptionsItem>
          <ElDescriptionsItem label="错误信息">
            {{ errorMessage || '无' }}
          </ElDescriptionsItem>
        </ElDescriptions>
      </section>
    </div>
  </div>
</template>

<style scoped>
.login-panel {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 14px 22px;
  margin-top: 16px;
  padding: 18px 20px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 8px;
}

.login-copy .section-kicker {
  color: #748187;
}

.login-copy h3 {
  margin: 0;
  color: var(--ink);
  font-size: 17px;
  letter-spacing: 0;
}

.login-copy p:last-child {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.login-actions {
  display: flex;
  align-items: center;
  gap: 9px;
}

.login-feedback {
  grid-column: 1 / -1;
}

@media (max-width: 720px) {
  .login-panel {
    grid-template-columns: 1fr;
    padding: 16px;
  }

  .login-actions {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
