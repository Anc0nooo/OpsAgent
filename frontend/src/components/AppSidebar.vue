<script setup lang="ts">
/**
 * 应用左侧边栏（参考 DeepSeek 风格）
 * - 顶部：折叠按钮 + 新建对话
 * - 搜索框 + 管理图标（进入多选模式）
 * - 历史会话按日期分组（置顶 → 今天 → 昨天 → 7 天内 → 更早）
 * - 会话项 hover 显示「置顶」「删除」；多选模式显示复选框 + 底部批量删除栏
 * - 搜索时平铺结果，不分组
 * - 底部：设置 + 模型状态
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { listSessions, pinSession, batchDeleteSessions, type SessionItem } from '../api/chat'
import { removeAvatar, uploadAvatar } from '../api/auth'
import { avatar, avatarColor, avatarInitial, doLogout, loadUser, role, username } from '../store/user'
import { refreshSettingsStatus, settingsStatus } from '../store/settings'
import { fileToAvatarDataUrl } from '../utils/avatar'
import { confirmDanger } from '../utils/dialog'
import { ElMessage } from 'element-plus'

const props = defineProps<{
  collapsed: boolean              // 是否折叠（折叠后只显示图标，宽度 60px）
  currentSessionId: string | null // 当前选中的会话（高亮）
}>()

const emit = defineEmits<{
  'new-chat': []
  'select-session': [sessionId: string]
  'toggle-collapse': []
  'open-settings': []
}>()

// ---------------- 会话列表 ----------------
const sessions = ref<SessionItem[]>([])
const loading = ref(false)
const searchKeyword = ref('')
/** 多选管理模式 */
const manageMode = ref(false)
/** 选中的会话 id 列表（array 便于响应式） */
const selectedIds = ref<string[]>([])

async function refreshSessions() {
  loading.value = true
  try {
    const list = await listSessions()
    // 后端 is_pinned 返回 0/1，归一为 boolean
    sessions.value = list.map((s) => ({ ...s, is_pinned: !!s.is_pinned }))
  } catch {
    sessions.value = []
  } finally {
    loading.value = false
  }
}

const isSearching = computed(() => searchKeyword.value.trim().length > 0)

/** 搜索过滤（标题模糊匹配） */
const filteredSessions = computed(() => {
  const kw = searchKeyword.value.trim().toLowerCase()
  if (!kw) return sessions.value
  return sessions.value.filter((s) => (s.title || '').toLowerCase().includes(kw))
})

interface SessionGroup { label: string; items: SessionItem[] }

/** 按日期分组：置顶 → 今天 → 昨天 → 7 天内 → 更早 */
const groupedSessions = computed<SessionGroup[]>(() => {
  const now = new Date()
  const today0 = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday0 = new Date(today0.getTime() - 86400000)
  const sevenDaysAgo = new Date(today0.getTime() - 7 * 86400000)

  const pinned: SessionItem[] = []
  const buckets: SessionGroup[] = [
    { label: '今天', items: [] },
    { label: '昨天', items: [] },
    { label: '7 天内', items: [] },
    { label: '更早', items: [] },
  ]
  for (const s of sessions.value) {
    if (s.is_pinned) { pinned.push(s); continue }
    const d = new Date(s.updated_at || s.created_at)
    if (isNaN(d.getTime())) { buckets[3].items.push(s); continue }
    if (d >= today0) buckets[0].items.push(s)
    else if (d >= yesterday0) buckets[1].items.push(s)
    else if (d >= sevenDaysAgo) buckets[2].items.push(s)
    else buckets[3].items.push(s)
  }
  const groups: SessionGroup[] = []
  if (pinned.length) groups.push({ label: '置顶', items: pinned })
  for (const b of buckets) if (b.items.length) groups.push(b)
  return groups
})

/** 会话标题省略（超 22 字） */
function shortTitle(title: string): string {
  if (!title) return '未命名会话'
  return title.length > 22 ? title.slice(0, 22) + '…' : title
}

function isSelected(id: string): boolean {
  return selectedIds.value.includes(id)
}

/** 切换置顶 / 取消置顶 */
async function onTogglePin(e: Event, s: SessionItem) {
  e.stopPropagation()
  const next = !s.is_pinned
  try {
    await pinSession(s.id, next)
    s.is_pinned = next
  } catch { /* 静默 */ }
}

/** 单个删除（hover 图标，复用批量接口） */
async function onDeleteOne(e: Event, s: SessionItem) {
  e.stopPropagation()
  if (!(await confirmDanger(`确认删除会话「${shortTitle(s.title)}」？此操作不可恢复。`))) return
  try {
    await batchDeleteSessions([s.id])
    sessions.value = sessions.value.filter((x) => x.id !== s.id)
    selectedIds.value = selectedIds.value.filter((id) => id !== s.id)
    if (props.currentSessionId === s.id) emit('new-chat')
    ElMessage.success('已删除')
  } catch {
    ElMessage.error('删除失败')
  }
}

/** 多选：勾选 / 取消（array 响应式） */
function onToggleSelect(s: SessionItem) {
  const idx = selectedIds.value.indexOf(s.id)
  if (idx >= 0) selectedIds.value.splice(idx, 1)
  else selectedIds.value.push(s.id)
}

/** 分组全选 / 全不选 */
function onGroupSelectAll(group: SessionGroup, e: Event) {
  const checked = (e.target as HTMLInputElement).checked
  const ids = new Set(group.items.map((s) => s.id))
  if (checked) {
    const merged = new Set([...selectedIds.value, ...ids])
    selectedIds.value = Array.from(merged)
  } else {
    selectedIds.value = selectedIds.value.filter((id) => !ids.has(id))
  }
}

function isGroupAllSelected(group: SessionGroup): boolean {
  return group.items.length > 0 && group.items.every((s) => isSelected(s.id))
}

const allSelected = computed(() =>
  sessions.value.length > 0 && sessions.value.every((s) => isSelected(s.id)),
)
function onSelectAll(e: Event) {
  const checked = (e.target as HTMLInputElement).checked
  selectedIds.value = checked ? sessions.value.map((s) => s.id) : []
}

const selectedCount = computed(() => selectedIds.value.length)

/** 进入 / 退出多选模式 */
function toggleManageMode() {
  manageMode.value = !manageMode.value
  if (!manageMode.value) selectedIds.value = []
}

/** 批量删除（确认弹窗） */
async function onBatchDelete() {
  if (!selectedCount.value) return
  const ids = [...selectedIds.value]
  if (!(await confirmDanger(`确认删除选中的 ${ids.length} 个会话？此操作不可恢复。`))) return
  try {
    await batchDeleteSessions(ids)
    const removed = new Set(ids)
    sessions.value = sessions.value.filter((s) => !removed.has(s.id))
    if (props.currentSessionId && removed.has(props.currentSessionId)) emit('new-chat')
    selectedIds.value = []
    ElMessage.success(`已删除 ${ids.length} 个会话`)
  } catch {
    ElMessage.error('删除失败')
  }
}

/** 点击会话项：多选模式勾选，否则切换会话 */
function onSessionClick(s: SessionItem) {
  if (manageMode.value) onToggleSelect(s)
  else emit('select-session', s.id)
}

/** 清除搜索 */
function clearSearch() {
  searchKeyword.value = ''
}

// ---------------- 底部模型配置状态（全局 store，保存后实时更新） ----------------
const modelConfigured = computed(() => !!settingsStatus.value?.dashscope_configured)
const modelLabel = computed(() => {
  if (!settingsStatus.value) return '未连接'
  if (!modelConfigured.value) return 'API 未配置'
  const model = settingsStatus.value.chat_model || 'qwen'
  return `API 已配置 · ${model}`
})

// ---------------- 用户区（头像 + 用户名 + 菜单） ----------------
const router = useRouter()
const showUserMenu = ref(false)
const avatarInput = ref<HTMLInputElement | null>(null)
const avatarUploading = ref(false)

function toggleUserMenu() {
  showUserMenu.value = !showUserMenu.value
}

function closeUserMenu() {
  showUserMenu.value = false
}

/** 菜单：上传/更换头像（触发隐藏文件选择框） */
function triggerAvatarUpload() {
  closeUserMenu()
  avatarInput.value?.click()
}

/** 文件选择后：压缩为 256px JPEG data URL → 上传 → 更新本地头像 */
async function onAvatarFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = '' // 允许重复选择同一文件
  if (!file) return
  if (!file.type.startsWith('image/')) {
    ElMessage.warning('请选择图片文件')
    return
  }
  avatarUploading.value = true
  try {
    const dataUrl = await fileToAvatarDataUrl(file)
    const blob = await (await fetch(dataUrl)).blob()
    const saved = await uploadAvatar(blob, 'avatar.jpg')
    avatar.value = saved || dataUrl
    ElMessage.success('头像已更新')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '头像上传失败')
  } finally {
    avatarUploading.value = false
  }
}

/** 菜单：移除头像（恢复默认首字符） */
async function onRemoveAvatar() {
  closeUserMenu()
  try {
    await removeAvatar()
    avatar.value = ''
    ElMessage.success('已恢复默认头像')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '移除失败')
  }
}

/** 菜单：退出登录 */
async function onLogout() {
  closeUserMenu()
  doLogout()
  router.push('/login')
}

/** 菜单：管理后台 */
function goAdmin() {
  closeUserMenu()
  router.push('/admin')
}

onMounted(async () => {
  // 登录态下拉取当前用户（头像/用户名）与配置状态；401 由拦截器统一处理
  loadUser()
  refreshSettingsStatus()
  await refreshSessions()
})

// 暴露刷新方法供父组件调用
defineExpose({ refreshSessions })
</script>

<template>
  <aside class="sidebar" :class="{ collapsed: props.collapsed }">
    <!-- 顶部：折叠按钮 + 新建对话 -->
    <div class="sidebar-top">
      <button class="icon-btn collapse-btn" title="折叠/展开" @click="emit('toggle-collapse')">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="3" y1="6" x2="21" y2="6"/>
          <line x1="3" y1="12" x2="21" y2="12"/>
          <line x1="3" y1="18" x2="21" y2="18"/>
        </svg>
      </button>
      <button v-if="!props.collapsed" class="new-chat-btn" @click="emit('new-chat')">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="12" y1="5" x2="12" y2="19"/>
          <line x1="5" y1="12" x2="19" y2="12"/>
        </svg>
        <span>新建对话</span>
      </button>
      <button v-else class="icon-btn new-chat-icon" title="新建对话" @click="emit('new-chat')">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="12" y1="5" x2="12" y2="19"/>
          <line x1="5" y1="12" x2="19" y2="12"/>
        </svg>
      </button>
    </div>

    <!-- 搜索框 + 管理图标（折叠时隐藏） -->
    <div v-if="!props.collapsed" class="search-row">
      <div class="search-box">
        <svg class="search-icon" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="11" cy="11" r="8"/>
          <line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input
          v-model="searchKeyword"
          class="search-input"
          placeholder="搜索历史会话"
        />
        <button v-if="searchKeyword" class="search-clear" title="清除" @click="clearSearch">×</button>
      </div>
      <button
        class="icon-btn manage-btn"
        :class="{ active: manageMode }"
        :title="manageMode ? '退出管理' : '多选管理'"
        @click="toggleManageMode"
      >
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="9 11 12 14 22 4"/>
          <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
        </svg>
      </button>
    </div>

    <!-- 管理员入口（仅 role == 'ancon' 可见） -->
    <button
      v-if="role === 'ancon'"
      class="nav-btn admin-nav"
      :class="{ collapsed: props.collapsed }"
      :title="props.collapsed ? '管理后台' : ''"
      @click="router.push('/admin')"
    >
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
        <polyline points="9 22 9 12 15 12 15 22"/>
      </svg>
      <span v-if="!props.collapsed">管理后台</span>
    </button>

    <!-- 历史会话列表 -->
    <div v-if="!props.collapsed" class="session-list">
      <div v-if="loading" class="empty-hint">加载中…</div>
      <div v-else-if="sessions.length === 0" class="empty-hint">暂无历史会话</div>

      <!-- 搜索结果：平铺不分组 -->
      <template v-else-if="isSearching">
        <div v-if="filteredSessions.length === 0" class="empty-hint">未找到匹配的会话</div>
        <template v-else>
          <div class="group-label">搜索结果（{{ filteredSessions.length }}）</div>
          <div
            v-for="s in filteredSessions"
            :key="s.id"
            class="session-item"
            :class="{ active: props.currentSessionId === s.id, checked: isSelected(s.id) }"
            @click="onSessionClick(s)"
          >
            <label v-if="manageMode" class="item-check" @click.stop>
              <input type="checkbox" :checked="isSelected(s.id)" @change="onToggleSelect(s)" />
            </label>
            <svg v-if="s.is_pinned" class="pin-mark" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>
            </svg>
            <span class="session-title">{{ shortTitle(s.title) }}</span>
            <div v-if="!manageMode" class="item-actions">
              <button class="item-btn pin-btn" :class="{ active: s.is_pinned }" title="置顶/取消" @click="onTogglePin($event, s)">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>
                </svg>
              </button>
              <button class="item-btn del-btn" title="删除" @click="onDeleteOne($event, s)">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="3 6 5 6 21 6"/>
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                </svg>
              </button>
            </div>
          </div>
        </template>
      </template>

      <!-- 按日期分组 -->
      <template v-else>
        <div v-if="manageMode" class="group-label select-all-row">
          <label class="item-check">
            <input type="checkbox" :checked="allSelected" @change="onSelectAll" />
            <span>全选</span>
          </label>
        </div>
        <div v-for="group in groupedSessions" :key="group.label" class="session-group">
          <div class="group-label">
            <span>{{ group.label }}</span>
            <label v-if="manageMode" class="group-select-all" @click.stop>
              <input type="checkbox" :checked="isGroupAllSelected(group)" @change="onGroupSelectAll(group, $event)" />
            </label>
          </div>
          <div
            v-for="s in group.items"
            :key="s.id"
            class="session-item"
            :class="{ active: props.currentSessionId === s.id, checked: isSelected(s.id) }"
            @click="onSessionClick(s)"
          >
            <label v-if="manageMode" class="item-check" @click.stop>
              <input type="checkbox" :checked="isSelected(s.id)" @change="onToggleSelect(s)" />
            </label>
            <svg v-if="s.is_pinned" class="pin-mark" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>
            </svg>
            <span class="session-title">{{ shortTitle(s.title) }}</span>
            <div v-if="!manageMode" class="item-actions">
              <button class="item-btn pin-btn" :class="{ active: s.is_pinned }" title="置顶/取消" @click="onTogglePin($event, s)">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>
                </svg>
              </button>
              <button class="item-btn del-btn" title="删除" @click="onDeleteOne($event, s)">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="3 6 5 6 21 6"/>
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                </svg>
              </button>
            </div>
          </div>
        </div>
      </template>
    </div>

    <!-- 折叠时的迷你历史列表（只显示图标） -->
    <div v-else class="session-list-mini">
      <button
        v-for="s in sessions.slice(0, 10)"
        :key="s.id"
        class="mini-session"
        :class="{ active: props.currentSessionId === s.id }"
        :title="s.title"
        @click="emit('select-session', s.id)"
      >
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
      </button>
    </div>

    <!-- 多选模式底部操作栏 -->
    <div v-if="!props.collapsed && manageMode" class="batch-bar">
      <span class="batch-count">已选 {{ selectedCount }} 项</span>
      <div class="batch-actions">
        <button class="batch-btn cancel" @click="toggleManageMode">取消</button>
        <button class="batch-btn danger" :disabled="!selectedCount" @click="onBatchDelete">删除</button>
      </div>
    </div>

    <!-- 底部：用户（头像+用户名） + 模型状态 + 设置 -->
    <div class="sidebar-bottom" :class="{ collapsed: props.collapsed }">
      <div class="user-chip" :title="username" @click="toggleUserMenu">
        <span
          class="user-avatar"
          :class="{ uploading: avatarUploading }"
          :style="avatar ? {} : { background: avatarColor() }"
        >
          <img v-if="avatar" :src="avatar" alt="头像" />
          <template v-else>{{ avatarInitial() }}</template>
        </span>
        <span v-if="!props.collapsed" class="user-name">{{ username || '未登录' }}</span>
      </div>
      <span
        v-if="!props.collapsed"
        class="model-status"
        :class="settingsStatus ? (modelConfigured ? 'status-ok' : 'status-warn') : ''"
        :title="settingsStatus ? `${settingsStatus.chat_model || 'qwen'} · ${settingsStatus.embed_model}` : ''"
      >
        <span class="status-dot"></span>{{ modelLabel }}
      </span>
      <button class="icon-btn settings-btn" title="设置" @click="emit('open-settings')">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="3"/>
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
        </svg>
      </button>

      <!-- 用户菜单（管理后台 / 头像上传 / 移除 / 退出登录） -->
      <div v-if="showUserMenu" class="menu-backdrop" @click="closeUserMenu"></div>
      <div v-if="showUserMenu" class="user-menu">
        <button v-if="role === 'ancon'" class="menu-item" @click="goAdmin">管理后台</button>
        <div v-if="role === 'ancon'" class="menu-divider"></div>
        <button class="menu-item" @click="triggerAvatarUpload">
          {{ avatar ? '更换头像' : '上传头像' }}
        </button>
        <button v-if="avatar" class="menu-item" @click="onRemoveAvatar">移除头像</button>
        <div class="menu-divider"></div>
        <button class="menu-item danger" @click="onLogout">退出登录</button>
      </div>
      <input ref="avatarInput" type="file" accept="image/*" class="avatar-input" @change="onAvatarFileChange" />
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 280px;
  flex: none;
  display: flex;
  flex-direction: column;
  background: #f7f7f8;
  border-right: 1px solid var(--border);
  transition: width 0.2s ease;
  overflow: hidden;
}
.sidebar.collapsed {
  width: 60px;
}

/* 顶部区域 */
.sidebar-top {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 12px 8px;
  flex: none;
}
.icon-btn {
  width: 32px;
  height: 32px;
  flex: none;
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
.icon-btn:hover {
  background: rgba(0, 0, 0, 0.06);
  color: var(--text-main);
}
.icon-btn.active {
  background: var(--primary-weak);
  color: var(--primary);
}
.new-chat-btn {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
  color: var(--text-main);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}
.new-chat-btn:hover {
  border-color: var(--primary);
  color: var(--primary);
  background: var(--primary-weak);
}
.new-chat-icon {
  margin: 0 auto;
}

/* 搜索 + 管理图标 */
.search-row {
  display: flex;
  gap: 6px;
  padding: 0 12px 8px;
  flex: none;
  align-items: center;
}
.search-box {
  position: relative;
  flex: 1;
}
.search-icon {
  position: absolute;
  left: 10px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-sub);
  pointer-events: none;
}
.search-input {
  width: 100%;
  box-sizing: border-box;
  padding: 8px 28px 8px 30px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
  background: #fff;
  color: var(--text-main);
  outline: none;
  transition: border-color 0.15s;
}
.search-input:focus {
  border-color: var(--primary);
}
.search-input::placeholder {
  color: var(--text-sub);
}
.search-clear {
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  width: 18px;
  height: 18px;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.08);
  color: var(--text-sub);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}
.search-clear:hover {
  background: rgba(0, 0, 0, 0.15);
  color: var(--text-main);
}
.manage-btn {
  flex: none;
}

/* 会话列表 */
.session-list {
  flex: 1;
  min-height: 0; /* flex 子项可收缩：会话过多时此处内部滚动，不把底部用户栏挤出屏幕 */
  overflow-y: auto;
  padding: 4px 8px;
}
.session-list::-webkit-scrollbar {
  width: 4px;
}
.session-list::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.15);
  border-radius: 2px;
}
.empty-hint {
  padding: 24px 12px;
  text-align: center;
  color: var(--text-sub);
  font-size: 13px;
}
.session-group {
  margin-bottom: 6px;
}
.group-label {
  padding: 8px 8px 4px;
  font-size: 12px;
  color: var(--text-sub);
  font-weight: 600;
  user-select: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.group-select-all {
  cursor: pointer;
  display: flex;
  align-items: center;
}
.group-select-all input[type="checkbox"] {
  width: 14px;
  height: 14px;
  accent-color: var(--primary);
  cursor: pointer;
}
.select-all-row {
  padding: 6px 8px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 4px;
}

/* 会话项 */
.session-item {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 40px;
  padding: 0 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.12s;
  position: relative;
}
.session-item:hover {
  background: rgba(0, 0, 0, 0.04);
}
.session-item.active {
  background: var(--primary-weak);
}
.session-item.checked {
  background: rgba(21, 93, 202, 0.06);
}
.item-check {
  flex: none;
  display: flex;
  align-items: center;
  cursor: pointer;
}
.item-check input[type="checkbox"] {
  width: 15px;
  height: 15px;
  accent-color: var(--primary);
  cursor: pointer;
}
.pin-mark {
  flex: none;
  color: var(--primary);
}
.session-title {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  color: var(--text-main);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session-item.active .session-title {
  color: var(--primary);
  font-weight: 500;
}
.item-actions {
  flex: none;
  display: flex;
  gap: 2px;
  opacity: 0;
  transition: opacity 0.15s;
}
.session-item:hover .item-actions {
  opacity: 1;
}
.item-btn {
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-sub);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.item-btn:hover {
  background: rgba(0, 0, 0, 0.06);
  color: var(--text-main);
}
.pin-btn.active {
  color: var(--primary);
}
.del-btn:hover {
  background: rgba(245, 63, 45, 0.1);
  color: #f53f2d;
}

/* 折叠迷你列表 */
.session-list-mini {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px 8px;
  align-items: center;
}
.mini-session {
  width: 36px;
  height: 36px;
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
.mini-session:hover {
  background: rgba(0, 0, 0, 0.06);
  color: var(--text-main);
}
.mini-session.active {
  background: var(--primary-weak);
  color: var(--primary);
}

/* 多选底部操作栏 */
.batch-bar {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 14px;
  border-top: 1px solid var(--border);
  background: #fff;
}
.batch-count {
  font-size: 13px;
  color: var(--text-main);
  font-weight: 500;
}
.batch-actions {
  display: flex;
  gap: 6px;
}
.batch-btn {
  padding: 6px 14px;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.batch-btn.cancel {
  background: transparent;
  color: var(--text-sub);
  border: 1px solid var(--border);
}
.batch-btn.cancel:hover {
  color: var(--text-main);
  border-color: var(--text-sub);
}
.batch-btn.danger {
  background: #f53f2d;
  color: #fff;
}
.batch-btn.danger:hover:not(:disabled) {
  filter: brightness(1.08);
}
.batch-btn.danger:disabled {
  opacity: 0.5;
  cursor: default;
}

/* 管理员导航按钮 */
.nav-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  width: calc(100% - 16px);
  margin: 4px 8px;
  padding: 8px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-sub);
  font-size: 13px;
  cursor: pointer;
  flex: none;
  transition: background 0.15s;
}
.nav-btn:hover {
  background: var(--hover-bg, #ececec);
}
.nav-btn.collapsed {
  justify-content: center;
  width: 36px;
  margin: 4px auto;
}
.admin-nav {
  color: #e8533f;
}
.admin-nav:hover {
  background: rgba(232, 83, 63, 0.08);
}

/* 底部 */
.sidebar-bottom {
  flex: none;
  margin-top: auto; /* 兜底：始终贴侧边栏最底部（即使历史列表很短/为空） */
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-top: 1px solid var(--border);
}
/* 收起态：纵向排列（头像在上、设置在下），不与用户名/状态文字挤在同一行 */
.sidebar-bottom.collapsed {
  flex-direction: column;
  justify-content: center;
  gap: 8px;
  padding: 10px 6px;
}
.sidebar-bottom.collapsed .user-chip {
  margin-left: 0;
  padding: 0;
}
.settings-btn {
  flex: none;
}
.sidebar-bottom.collapsed .settings-btn {
  margin-left: 0;
}
/* 模型状态：占满中间剩余空间（flex-grow），过长文字省略号，
   自然把右侧齿轮顶到最右。不要给齿轮加 margin-left:auto——
   auto margin 会先于 flex-grow 吃掉剩余空间，两者竞争会在窄抽屉下
   导致模型文字被压缩、齿轮悬在中部、右侧留空。 */
.model-status {
  flex: 1 1 auto;
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 配置状态配色：已配置绿色 / 未配置橙色 / 未连接灰色 */
.model-status .status-dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-sub);
}
.model-status.status-ok {
  color: #18b26b;
}
.model-status.status-ok .status-dot {
  background: #18b26b;
}
.model-status.status-warn {
  color: #f59e0b;
}
.model-status.status-warn .status-dot {
  background: #f59e0b;
}

/* 用户区：头像 + 用户名 */
.user-chip {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px 4px 4px;
  margin-left: -4px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
  max-width: 45%;
}
.user-chip:hover {
  background: rgba(0, 0, 0, 0.05);
}
.user-avatar {
  width: 30px;
  height: 30px;
  flex: none;
  border-radius: 50%;
  overflow: hidden;
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  user-select: none;
}
.user-avatar.uploading {
  opacity: 0.6;
}
.user-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.user-name {
  min-width: 0;
  font-size: 13px;
  color: var(--text-main);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 用户菜单 */
.menu-backdrop {
  position: fixed;
  inset: 0;
  z-index: 190;
}
.user-menu {
  position: absolute;
  left: 10px;
  bottom: calc(100% + 8px);
  min-width: 132px;
  padding: 5px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.1);
  z-index: 200;
  display: flex;
  flex-direction: column;
}
.menu-item {
  padding: 8px 12px;
  border: none;
  border-radius: 6px;
  background: transparent;
  text-align: left;
  font-size: 13px;
  color: var(--text-main);
  cursor: pointer;
  transition: background 0.12s;
  white-space: nowrap;
}
.menu-item:hover {
  background: rgba(0, 0, 0, 0.05);
}
.menu-item.danger {
  color: #f53f2d;
}
.menu-item.danger:hover {
  background: rgba(245, 63, 45, 0.08);
}
.menu-divider {
  height: 1px;
  margin: 4px 6px;
  background: var(--border);
}
.avatar-input {
  display: none;
}

/* 移动端响应式：< 768px 侧边栏由 App.vue 的 .sidebar-host 离屏抽屉承载，
   这里只需填满抽屉容器（定位 / 遮罩 / 滑入位移全部在 App.vue 媒体查询中） */
@media (max-width: 768px) {
  .sidebar,
  .sidebar.collapsed {
    width: 100%;
    height: 100%;
    border-right: none;
    box-shadow: none;
  }
}
</style>
