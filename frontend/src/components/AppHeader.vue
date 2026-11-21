<template>
  <header class="app-topbar">
    <RouterLink class="brand-link" to="/">
      <div class="brand-block">
        <div class="brand-mark" aria-hidden="true">
          <span class="brand-mark__play" />
        </div>
        <div>
          <p class="brand-eyebrow">LOCAL WORKBENCH</p>
          <h1>抖音视频工作台</h1>
        </div>
      </div>
    </RouterLink>

    <nav class="app-nav" aria-label="主导航">
      <RouterLink class="app-nav__link" to="/">服务状态</RouterLink>
      <RouterLink class="app-nav__link" to="/videos">视频库</RouterLink>
      <RouterLink class="app-nav__link" to="/capture">抓取管理</RouterLink>
      <RouterLink class="app-nav__link" to="/downloads">下载管理</RouterLink>
      <RouterLink class="app-nav__link" to="/analytics">统计分析</RouterLink>
    </nav>

    <div class="topbar-meta">
      <span>Vue 3</span>
      <span class="meta-divider" />
      <span>FastAPI</span>
    </div>
  </header>
</template>
