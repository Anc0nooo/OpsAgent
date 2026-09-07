/**
 * 路由配置
 * - /login 登录/注册页（公开）
 * - / 对话页（历史会话已移入左侧边栏，不再单独路由）
 * - /knowledge 知识库管理页
 * 路由守卫：无 token 访问业务页 → 跳 /login；有 token 访问 /login → 跳首页
 */
import { createRouter, createWebHistory } from 'vue-router'
import { getToken } from '../api/token'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/LoginView.vue'),
      meta: { title: '登录', public: true },
    },
    {
      path: '/',
      name: 'chat',
      component: () => import('../views/ChatView.vue'),
      meta: { title: '运维对话' },
    },
    {
      path: '/knowledge',
      name: 'knowledge',
      component: () => import('../views/KnowledgeView.vue'),
      meta: { title: '知识库管理' },
    },
  ],
})

// 全局前置守卫：登录态校验
router.beforeEach((to) => {
  const token = getToken()
  if (!to.meta.public && !token) {
    // 未登录访问业务页 → 跳登录
    return { path: '/login' }
  }
  if (to.path === '/login' && token) {
    // 已登录访问登录页 → 跳首页
    return { path: '/' }
  }
})

// 路由后置守卫：同步页面标题
router.afterEach((to) => {
  const title = to.meta.title as string | undefined
  document.title = title ? `${title} - OpsAgent` : 'OpsAgent 运维智能体'
})

export default router
