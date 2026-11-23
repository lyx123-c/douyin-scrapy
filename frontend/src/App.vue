<script setup>
import AppHeader from './components/AppHeader.vue'
import AppSidebar from './components/AppSidebar.vue'
</script>

<template>
  <a class="skip-link" href="#main-content">跳到主要内容</a>
  <div class="flex h-screen overflow-hidden bg-canvas max-[760px]:block max-[760px]:h-auto max-[760px]:min-h-screen">
    <AppSidebar />
    <div class="flex h-screen min-w-0 flex-1 flex-col max-[760px]:h-auto max-[760px]:min-h-[calc(100vh-58px)]">
      <AppHeader />
      <main
        id="main-content"
        class="flex-1 overflow-auto bg-canvas max-[760px]:overflow-visible"
        tabindex="-1"
      >
        <RouterView />
      </main>
    </div>
  </div>
</template>
