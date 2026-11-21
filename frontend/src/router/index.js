import { createRouter, createWebHashHistory } from 'vue-router'

import HomeView from '../views/HomeView.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: HomeView,
    },
    {
      path: '/videos',
      name: 'videos',
      component: () => import('../views/VideosView.vue'),
    },
    {
      path: '/capture',
      name: 'capture',
      component: () => import('../views/CaptureView.vue'),
    },
    {
      path: '/downloads',
      name: 'downloads',
      component: () => import('../views/DownloadsView.vue'),
    },
    {
      path: '/analytics',
      name: 'analytics',
      component: () => import('../views/AnalyticsView.vue'),
    },
  ],
})

export default router
