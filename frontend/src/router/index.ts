/**
 * 路由配置
 * - /login 登录/注册页（公开）
 * - / 对话页（历史会话已移入左侧边栏，不再单独路由）
 * - /knowledge 知识库管理页
 * - /admin 管理后台（仅 role == 'ancon' 可访问）
 * 路由守卫：无 token → 跳 /login；非管理员访问 /admin → 跳首页
 */
import { createRouter, createWebHistory } from 'vue-router'
import { getToken } from '../api/token'
import { role } from '../store/user'

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
      meta: { title: '对话' },
    },
    {
      path: '/knowledge',
      name: 'knowledge',
      component: () => import('../views/KnowledgeView.vue'),
      meta: { title: '知识库管理' },
    },
    {
      path: '/admin',
      name: 'admin',
      component: () => import('../views/AdminView.vue'),
      meta: { title: '管理后台', requiresAdmin: true },
    },
  ],
})

// 全局前置守卫：登录态 + 管理员角色校验
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
  // 管理员页面：非 ancon 角色 → 跳首页
  if (to.meta.requiresAdmin && role.value !== 'ancon') {
    return { path: '/' }
  }
})

// 路由后置守卫：浏览器标签页标题统一为 OpsAgent（页面内大标题由各视图/顶栏自行展示）
router.afterEach(() => {
  document.title = 'OpsAgent'
})

export default router
