<script setup>
import {
  Connection,
  DataAnalysis,
  Download,
  Film,
  Setting,
  VideoCamera,
} from '@element-plus/icons-vue'

const appIconUrl = `${import.meta.env.BASE_URL}app-icon.png`
const navigation = [

  { to: '/', label: '工作台状态', description: 'Runtime 连接与登录状态', icon: Connection },
  { to: '/videos', label: '视频库', description: '浏览与管理视频记录', icon: Film },
  { to: '/capture', label: '抓取管理', description: '运行视频抓取任务', icon: VideoCamera },
  { to: '/downloads', label: '下载管理', description: '处理下载队列与重试', icon: Download },
  { to: '/analytics', label: '统计分析', description: '查看下载数据指标', icon: DataAnalysis },
  { to: '/settings', label: '设置', description: '存储位置与运行配置', icon: Setting },
]
</script>

<template>
  <aside
    class="flex h-screen w-[236px] shrink-0 flex-col border-r border-white/10 bg-sidebar px-3 py-4 text-sidebar-foreground max-[760px]:h-auto max-[760px]:w-full max-[760px]:flex-row max-[760px]:items-center max-[760px]:gap-2 max-[760px]:overflow-x-auto max-[760px]:border-r-0 max-[760px]:border-b max-[760px]:px-3 max-[760px]:py-2"
  >
    <RouterLink
      class="flex shrink-0 items-center gap-3 rounded-panel px-2 py-1.5 no-underline max-[760px]:p-0"
      to="/"
      aria-label="抖音视频工作台首页"
    >
      <img
        :src="appIconUrl"
        class="h-9 w-9 shrink-0 rounded-control object-cover shadow-[0_8px_20px_-12px_rgba(17,25,31,0.9)] ring-1 ring-white/10"
        alt=""
        aria-hidden="true"
      />
      <span class="grid min-w-0 gap-0.5 max-[500px]:hidden">
        <strong class="truncate text-[14px] font-semibold text-sidebar-foreground">抖音视频工作台</strong>
        <small class="text-[10px] font-medium text-sidebar-muted">LOCAL WORKBENCH</small>
      </span>
    </RouterLink>

    <nav
      class="mt-6 grid min-h-0 flex-1 content-start gap-1 max-[760px]:mt-0 max-[760px]:flex max-[760px]:items-center max-[760px]:overflow-x-auto"
      aria-label="主导航"
    >
      <p class="px-3 pb-2 pt-1 text-[11px] font-semibold text-sidebar-muted max-[760px]:hidden">工作区</p>
      <RouterLink
        v-for="item in navigation"
        :key="item.to"
        :to="item.to"
        custom
        v-slot="{ href, navigate, isExactActive }"
      >
        <a
          :href="href"
          :aria-current="isExactActive ? 'page' : undefined"
          :title="item.label"
          class="group relative flex min-h-11 shrink-0 items-center gap-3 rounded-control px-3 py-2 no-underline transition duration-150"
          :class="isExactActive
            ? 'bg-brand/20 text-white'
            : 'text-sidebar-muted hover:bg-white/[0.055] hover:text-sidebar-foreground'"
          @click="navigate"
        >
          <span
            class="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-r-full bg-emerald-200 transition"
            :class="isExactActive ? 'opacity-100' : 'opacity-0'"
            aria-hidden="true"
          />
          <ElIcon class="shrink-0 text-[17px]"><component :is="item.icon" /></ElIcon>
          <span class="grid min-w-0 gap-0.5">
            <strong class="whitespace-nowrap text-[12.5px] font-semibold">{{ item.label }}</strong>
            <small
              class="truncate text-[10px] max-[760px]:hidden"
              :class="isExactActive ? 'text-emerald-100/75' : 'text-sidebar-muted'"
            >
              {{ item.description }}
            </small>
          </span>
        </a>
      </RouterLink>
    </nav>

    <div
      class="mt-auto flex items-center gap-3 rounded-panel border border-white/10 bg-white/[0.04] p-3 max-[760px]:hidden"
    >
      <span class="h-2 w-2 shrink-0 rounded-full bg-emerald-300 shadow-[0_0_0_4px_rgba(110,231,183,0.1)]" />
      <span class="grid gap-0.5">
        <strong class="text-[11px] font-semibold text-sidebar-foreground">本地 Runtime</strong>
        <small class="text-[10px] text-sidebar-muted">所有数据仅保存在本机</small>
      </span>
    </div>
  </aside>
</template>
