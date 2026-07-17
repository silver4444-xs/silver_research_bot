import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'dashboard', component: () => import('./views/Dashboard.vue') },
  { path: '/papers', name: 'paper-list', component: () => import('./views/PaperList.vue') },
  { path: '/papers/upload', name: 'paper-upload', component: () => import('./views/PaperUpload.vue') },
  { path: '/papers/:id', name: 'paper-detail', component: () => import('./views/PaperDetail.vue'), props: true },
  { path: '/compare', name: 'compare', component: () => import('./views/Compare.vue') },
  { path: '/agent', name: 'agent', component: () => import('./views/AgentChat.vue') },
  { path: '/rag', name: 'rag', component: () => import('./views/RAGSearch.vue') },
]

export default createRouter({ history: createWebHistory(), routes })
