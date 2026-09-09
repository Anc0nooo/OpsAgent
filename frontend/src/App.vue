<script setup lang="ts">
/**
 * 应用根组件：左侧边栏 + 右侧主内容区（经典对话应用布局）
 * - 左侧 AppSidebar：新建对话 + 搜索 + 历史列表 + 设置
 * - 右侧：顶部标题栏（会话标题 + 对话/知识库切换 + 设置）+ RouterView
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SettingsDialog from './components/SettingsDialog.vue'
import AppSidebar from './components/AppSidebar.vue'
import VersionDialog from './components/VersionDialog.vue'
import { loadUser } from './store/user'
import { refreshSettingsStatus, settingsStatus } from './store/settings'
import { getToken } from './api/token'
import { getVersion, type VersionInfo } from './api/version'

const route = useRoute()
const router = useRouter()

// ---------------- 侧边栏状态 ----------------
const sidebarCollapsed = ref(false)
const currentSessionId = ref<string | null>(null)
const sidebarRef = ref<InstanceType<typeof AppSidebar> | null>(null)

// 移动端检测：< 768px 视为移动端（侧边栏改为 Vant 抽屉）
const isMobile = ref(false)
/** 移动端抽屉是否打开（仅 isMobile 时使用） */
const drawerOpen = ref(false)
function checkMobile() {
  const mobile = window.innerWidth < 768
  isMobile.value = mobile
  if (mobile) {
    // 移动端：PC 折叠态无意义，保持 false；抽屉默认关闭
    sidebarCollapsed.value = false
    drawerOpen.value = false
  }
}

// ---------------- 主视图切换（对话/知识库） ----------------
const currentView = computed(() => {
  if (route.path === '/knowledge') return 'knowledge'
  return 'chat'
})

function switchView(view: 'chat' | 'knowledge') {
  if (view === 'knowledge') {
    router.push('/knowledge')
  } else {
    router.push('/')
  }
}

// ---------------- 顶部标题栏标题 ----------------
const headerTitle = computed(() => {
  if (route.path === '/knowledge') return '知识库管理'
  return '对话'
})

// ---------------- 设置弹窗 ----------------
const showSettings = ref(false)
const isFirstTime = ref(false)
const checkedAuto = ref(false)

async function checkAutoPopup() {
  if (checkedAuto.value) return
  checkedAuto.value = true
  // 拉取当前用户配置状态（存全局 store，侧边栏共用）
  await refreshSettingsStatus()
  const st = settingsStatus.value
  const hasKey = !!st && st.chat_configured && st.dashscope_configured
  const dismissedAt = localStorage.getItem('opsagent_settings_dismissed_at')
  const within24h = dismissedAt && (Date.now() - parseInt(dismissedAt, 10)) < 24 * 60 * 60 * 1000
  if (!hasKey && !within24h) {
    isFirstTime.value = true
    showSettings.value = true
  }
}

function onSaved() {
  showSettings.value = false
  // 保存成功后刷新全局配置状态（侧边栏实时更新）
  void refreshSettingsStatus()
}

function onLater() {
  localStorage.setItem('opsagent_settings_dismissed_at', String(Date.now()))
  showSettings.value = false
}

function openSettingsFromChild(e: Event) {
  const detail = (e as CustomEvent).detail || {}
  isFirstTime.value = !!detail.firstTime
  showSettings.value = true
}

// ---------------- 版本更新弹窗 ----------------
const LAST_VERSION_KEY = 'opsagent_last_version'
const showVersion = ref(false)
const versionInfo = ref<VersionInfo>({ version: '', changelog: '', updated_at: '' })
const versionChecked = ref(false)

/** 拉取版本并与本地 last_version 比对，不一致则弹更新弹窗（每次登录会话只检查一次） */
async function checkVersionUpdate() {
  if (versionChecked.value || !getToken()) return
  versionChecked.value = true
  try {
    const info = await getVersion()
    versionInfo.value = info
    const last = localStorage.getItem(LAST_VERSION_KEY)
    // 本地记录与当前版本不同（含首次登录 last=null）→ 弹窗
    if (info.version && last !== info.version) {
      showVersion.value = true
    }
  } catch {
    // 版本接口失败不阻断使用
  }
}

/** 「知道了」：记录当前版本，之后不再弹出 */
function onVersionConfirm() {
  if (versionInfo.value.version) {
    localStorage.setItem(LAST_VERSION_KEY, versionInfo.value.version)
  }
}

// ---------------- 侧边栏事件处理 ----------------
function onNewChat() {
  currentSessionId.value = null
  // 移动端：新建对话后收起抽屉
  drawerOpen.value = false
  // 如果当前在知识库页，切回对话页
  if (route.path !== '/') {
    router.push('/')
  }
  // 通知 ChatView 清空当前会话
  window.dispatchEvent(new CustomEvent('opsagent:new-chat'))
}

function onSelectSession(sessionId: string) {
  currentSessionId.value = sessionId
  // 切到对话页并携带 session 参数
  if (route.path !== '/') {
    router.push(`/?session=${sessionId}`)
  } else {
    // 已在对话页，通过事件通知 ChatView 加载会话
    window.dispatchEvent(new CustomEvent('opsagent:load-session', { detail: { sessionId } }))
  }
  // 移动端选择会话后自动收起抽屉
  drawerOpen.value = false
}

function onToggleCollapse() {
  // 移动端侧边栏在抽屉里：折叠按钮 = 关闭抽屉
  if (isMobile.value) {
    drawerOpen.value = false
    return
  }
  sidebarCollapsed.value = !sidebarCollapsed.value
}

function openSettings() {
  isFirstTime.value = false
  drawerOpen.value = false
  showSettings.value = true
}

// ---------------- 监听 ChatView 会话变化（同步 currentSessionId） ----------------
function onSessionCreated(e: Event) {
  const detail = (e as CustomEvent).detail
  if (detail?.sessionId) {
    currentSessionId.value = detail.sessionId
    // 刷新侧边栏列表
    sidebarRef.value?.refreshSessions()
  }
}

onMounted(() => {
  checkMobile()
  // 已登录时才拉用户信息 / 检查配置 / 检查版本更新（未登录由路由守卫跳 /login）
  if (getToken()) {
    loadUser()
    checkAutoPopup()
    checkVersionUpdate()
  }
  window.addEventListener('opsagent:open-settings', openSettingsFromChild)
  window.addEventListener('opsagent:session-created', onSessionCreated)
  window.addEventListener('resize', checkMobile)
})

// 登录后从 /login 跳回应用时，触发版本检查（onMounted 仅一次，登录发生在其后）
watch(() => route.path, (p) => {
  if (p !== '/login' && getToken()) {
    checkVersionUpdate()
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('opsagent:open-settings', openSettingsFromChild)
  window.removeEventListener('opsagent:session-created', onSessionCreated)
  window.removeEventListener('resize', checkMobile)
})
</script>

<template>
  <!-- 登录/注册页：独立全屏布局（无侧边栏） -->
  <div v-if="route.path === '/login'" class="login-shell">
    <RouterView />
  </div>

  <!-- 管理后台：独立全屏布局（无侧边栏，仅管理员可访问） -->
  <div v-else-if="route.path === '/admin'" class="admin-shell">
    <RouterView />
  </div>

  <div v-else class="app-layout">
    <!-- 移动端抽屉遮罩（点击关闭）；桌面端 display:none -->
    <div class="drawer-mask" :class="{ show: drawerOpen }" @click="drawerOpen = false" />

    <!-- 侧边栏宿主：桌面端为左侧固定栏（常规 flex 项）；
         移动端为离屏抽屉（position:fixed + translateX(-100%)，.open 滑入），
         平台切换完全由 CSS 媒体查询控制，不依赖 JS 判断 -->
    <div class="sidebar-host" :class="{ open: drawerOpen }">
      <AppSidebar
        ref="sidebarRef"
        :collapsed="isMobile ? false : sidebarCollapsed"
        :current-session-id="currentSessionId"
        @new-chat="onNewChat"
        @select-session="onSelectSession"
        @toggle-collapse="onToggleCollapse"
        @open-settings="openSettings"
      />
    </div>

    <!-- 右侧主内容区 -->
    <div class="main-area">
      <!-- 顶部标题栏 -->
      <header class="main-header">
        <!-- 移动端汉堡菜单按钮（呼出抽屉） -->
        <button
          class="menu-btn mobile-only"
          aria-label="打开菜单"
          @click="drawerOpen = true"
        >
          <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <line x1="3" y1="6" x2="21" y2="6"/>
            <line x1="3" y1="12" x2="21" y2="12"/>
            <line x1="3" y1="18" x2="21" y2="18"/>
          </svg>
        </button>
        <div class="header-title">{{ headerTitle }}</div>
        <div class="header-actions">
          <!-- 对话/知识库切换 -->
          <div class="view-switcher">
            <button
              class="switch-btn"
              :class="{ active: currentView === 'chat' }"
              @click="switchView('chat')"
            >对话</button>
            <button
              class="switch-btn"
              :class="{ active: currentView === 'knowledge' }"
              @click="switchView('knowledge')"
            >知识库</button>
          </div>
          <!-- API Key 状态指示（移动端隐藏，避免顶栏拥挤；状态在设置弹窗内可见） -->
          <span
            v-if="settingsStatus && (!settingsStatus.chat_configured || !settingsStatus.dashscope_configured)"
            class="key-indicator pc-only"
            title="API Key 未配置，点击设置"
            @click="() => { isFirstTime = true; showSettings = true }"
          >⚠ 未配置</span>
          <!-- 设置按钮 -->
          <button
            class="settings-btn"
            :title="(settingsStatus?.chat_configured && settingsStatus?.dashscope_configured) ? '设置' : '模型未配置，请点击配置'"
            @click="openSettings"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="3"/>
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
            </svg>
          </button>
        </div>
      </header>

      <!-- 主内容 -->
      <main class="main-content">
        <RouterView />
      </main>
    </div>

    <!-- 设置弹窗 -->
    <SettingsDialog
      v-model:visible="showSettings"
      :first-time="isFirstTime"
      @saved="onSaved"
      @update:visible="(v) => { if (!v && isFirstTime) onLater(); }"
    />
  </div>

  <!-- 版本更新弹窗（登录后版本号与本地不一致时弹出；所有页面通用） -->
  <VersionDialog
    v-model:visible="showVersion"
    :info="versionInfo"
    @confirm="onVersionConfirm"
  />
</template>

<style scoped>
/* 登录页独立布局外壳 */
.login-shell {
  height: 100vh;
  overflow: hidden;
}

/* 管理后台独立布局外壳 */
.admin-shell {
  height: 100vh;
  overflow: auto;
  background: #f5f5f7;
}

.app-layout {
  height: 100vh;
  display: flex;
  overflow: hidden;
}

/* 右侧主区 */
.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
  min-height: 0; /* flex 子项可收缩，避免子内容把布局撑爆（配合内部 overflow） */
}

/* 顶部标题栏 */
.main-header {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 20px;
  height: 52px;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border);
  z-index: 10;
}

/* 移动端汉堡按钮（桌面端 display:none 由 .mobile-only 控制） */
.menu-btn {
  flex: none;
  width: 40px;
  height: 40px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-main);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.header-title {
  font-size: 14.5px;
  font-weight: 600;
  color: var(--text-main);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: none;
}

/* 对话/知识库切换器 */
.view-switcher {
  display: flex;
  gap: 2px;
  padding: 3px;
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.04);
}
.switch-btn {
  padding: 5px 14px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-sub);
  font-size: 12.5px;
  cursor: pointer;
  transition: all 0.15s;
}
.switch-btn:hover {
  color: var(--text-main);
}
.switch-btn.active {
  background: #fff;
  color: var(--primary);
  font-weight: 500;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

/* API Key 状态指示 */
.key-indicator {
  padding: 4px 10px;
  border-radius: 6px;
  background: rgba(245, 63, 45, 0.08);
  color: #f53f2d;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.key-indicator:hover {
  background: rgba(245, 63, 45, 0.15);
}

/* 设置按钮 */
.settings-btn {
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-sub);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.settings-btn:hover {
  color: var(--primary);
  background: var(--primary-weak);
}

/* 主内容 */
.main-content {
  flex: 1;
  overflow: hidden;
  min-height: 0; /* flex 子项可收缩，保证内部滚动容器（对话消息区）正常工作 */
}

/* ============================================================
   侧边栏宿主 + 抽屉遮罩（桌面默认）
   - 桌面端：.sidebar-host 为常规 flex 项（固定在左侧）；遮罩 display:none
   - 移动端：见下方 @media，host 变离屏抽屉，mask 浮层
   ============================================================ */
.sidebar-host {
  flex: none;
  /* 作为 flex 容器：内部 .sidebar 纵向 stretch 填满宿主全高。
     PC 端宿主是 .app-layout(flex row) 的子项（被 stretch 到整屏高），
     若宿主不是 flex，.sidebar 高度由内容撑开，底部用户栏会停在中间；
     设为 flex 后 .sidebar 自动撑满，用户栏贴底。移动端宿主为 fixed 全高，同样适用。 */
  display: flex;
}
.drawer-mask {
  display: none;
}

/* ============================================================
   移动端适配（< 768px）：
   - 布局高度改用 --app-height（软键盘弹出时跟随 visualViewport 收缩）
   - 侧边栏：position:fixed + translateX(-100%) 离屏，汉堡呼出滑入，遮罩点击关闭
   - 主内容区铺满全宽；顶栏：汉堡 + 居中标题 + 操作区
   ============================================================ */
@media (max-width: 768px) {
  .login-shell,
  .app-layout {
    /* 100vh 在手机浏览器会被地址栏遮挡；100dvh 跟随动态视口；
       --app-height 为 JS 监听 visualViewport 写入的可视高度（软键盘弹出时收缩） */
    height: 100vh;
    height: 100dvh;
    height: var(--app-height, 100dvh);
    padding: 0;
    margin: 0;
    width: 100%;
    max-width: none;
  }

  /* 主内容区在移动端铺满整个屏幕宽度（侧边栏离屏，不占 flex 空间），
     去掉任何居中限宽 */
  .main-area {
    width: 100%;
    max-width: none;
    margin: 0;
    min-width: 0;
  }
  .main-content {
    width: 100%;
    max-width: none;
    margin: 0;
    /* 移动端主内容作为纵向滚动容器（知识库等文档流页面在内部滚动）；
       对话页自身为 100% 高 + 内部消息区滚动，互不冲突 */
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
  }

  /* 侧边栏：离屏抽屉 */
  .sidebar-host {
    position: fixed;
    left: 0;
    top: 0;
    bottom: 0;
    width: 84vw;
    max-width: 300px;
    transform: translateX(-100%);
    transition: transform 0.25s ease;
    z-index: 1001;
    box-shadow: 2px 0 12px rgba(0, 0, 0, 0.18);
    will-change: transform;
  }
  .sidebar-host.open {
    transform: translateX(0);
  }
  /* 抽屉内侧边栏填满、去除自带边框/阴影（定位由宿主负责） */
  .sidebar-host :deep(.sidebar),
  .sidebar-host :deep(.sidebar.collapsed) {
    width: 100% !important;
    height: 100%;
    border-right: none;
    box-shadow: none;
  }

  /* 遮罩：覆盖全屏，半透明黑色，点击关闭 */
  .drawer-mask {
    display: block;
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.32);
    z-index: 1000;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.25s;
  }
  .drawer-mask.show {
    opacity: 1;
    pointer-events: auto;
  }

  .main-header {
    padding: 0 8px 0 4px;
    gap: 4px;
  }
  .header-title {
    flex: 1;
    text-align: center;
    font-size: 14px;
    padding: 0 4px;
  }
  .header-actions {
    gap: 4px;
  }
  /* 对话/知识库切换器在移动端收紧 */
  .view-switcher {
    padding: 2px;
  }
  .switch-btn {
    padding: 6px 10px;
    font-size: 12.5px;
  }
  .settings-btn {
    width: 40px;
    height: 40px;
  }
}
</style>
