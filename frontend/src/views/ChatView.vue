<script setup lang="ts">
/**
 * 对话页（主页面）—— 阶段 4 已接通后端 SSE
 * - 流式打字机效果（status 阶段提示 + delta 逐段追加）
 * - AI 回复 markdown 渲染（marked + DOMPurify 防注入）
 * - 挂起态：SQL 卡片提示条（轮次 + 复制 SQL + 脱敏提醒），输入框切换为"回传结果"
 * - 会话策略：每次进入对话页默认新对话窗口；侧边栏/历史页跳转 ?session=xxx 时才加载指定会话
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { getHealth, type HealthInfo } from '../api'
import { getSettingsStatus, type SettingsStatus } from '../api/settings'
import { exportFile, loadSession, saveSessionKnowledge, streamChat, type AnswerEvaluation, type PendingQuery, type SourceRef, type SummarySections } from '../api/chat'
import DocViewDialog from '../components/DocViewDialog.vue'

/** 消息结构 */
interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  /** 是否正在流式接收中 */
  streaming?: boolean
  /** 流式处理阶段提示（status 事件） */
  status?: string
  /** 四段总结卡片（方案完成时由 done 事件携带） */
  sections?: SummarySections
  /** 引用来源文档（可点击查看，仅流式 done 事件携带） */
  sources?: SourceRef[]
  /** 回答质量自评卡片（仅工作类问题携带；闲聊无此字段） */
  evaluation?: AnswerEvaluation
  /** 后端分类错误码（error 事件携带） */
  error_code?: string
  /** 方案摘要面板是否展开（默认 false，点按钮才展开） */
  summaryOpen?: boolean
  /** 统一入库面板中是否选中（默认 false） */
  selectedForKb?: boolean
}

const SESSION_KEY = 'opsagent_session_id'

const health = ref<HealthInfo | null>(null)
const settingsStatus = ref<SettingsStatus | null>(null)
const messages = ref<ChatMessage[]>([])
const input = ref('')
const sending = ref(false)
/** 当前会话 ID（每次进入对话页默认新对话：null；仅 ?session=xxx 跳转或点击侧边栏会话才加载历史） */
const sessionId = ref<string | null>(null)
/** 挂起的只读查询（QUERY_PENDING 态非空） */
const pending = ref<PendingQuery | null>(null)

// 文档查看/编辑弹窗（点击方案引用来源打开）
const viewerDocId = ref<number | null>(null)
const viewerVisible = ref(false)
function openSource(s: SourceRef) {
  viewerDocId.value = s.doc_id
  viewerVisible.value = true
}

// DOM 引用：消息滚动容器 / 输入框
const scrollRef = ref<HTMLElement | null>(null)
const textareaRef = ref<HTMLTextAreaElement | null>(null)
// 路由（历史页跳转 ?session=xxx 指定会话）
const route = useRoute()
const router = useRouter()

/** 快捷问题（贴合运维场景，点击填入输入框） */
const quickQuestions = [
  'SQL 突然变慢怎么排查？',
  '基于 LIS_LIFEALERT 写个今日危急值统计视图',
  '护理评估单上传平台失败，怎么排查？',
  '服务器C 盘快满了怎么清理？',
]

/** markdown → 安全 HTML（AI 消息渲染） */
function renderMd(text: string): string {
  const html = marked.parse(text, { async: false, breaks: true }) as string
  return DOMPurify.sanitize(html)
}

/** 评估等级配色：goodIsHigh=true 时"高"为好（命中度）；false 时"低"为好（幻觉风险） */
function levelTone(level: string | undefined, goodIsHigh: boolean): string {
  if (!level) return ''
  const good = goodIsHigh ? '高' : '低'
  const bad = goodIsHigh ? '低' : '高'
  if (level === good) return 'good'
  if (level === '中') return 'warn'
  if (level === bad) return 'bad'
  return ''
}
/** 准确度百分比配色：≥90 绿，70~89 橙，<70 红 */
function accuracyTone(acc: string | undefined): string {
  const n = parseInt((acc || '').replace(/[^\d]/g, ''), 10)
  if (isNaN(n)) return ''
  if (n >= 90) return 'good'
  if (n >= 70) return 'warn'
  return 'bad'
}

/** 自动滚动到最新消息 */
function scrollToBottom() {
  nextTick(() => {
    scrollRef.value?.scrollTo({ top: scrollRef.value.scrollHeight, behavior: 'smooth' })
  })
}

/** 输入框自适应高度（上限 160px） */
function autoResize() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 160) + 'px'
}

/** 输入框 placeholder：挂起态切换为回传提示 */
const inputPlaceholder = computed(() =>
  pending.value
    ? `粘贴 SQL 执行结果（请先脱敏，不要包含完整患者信息）… 第 ${pending.value.round}/5 轮`
    : '输入你的运维问题…（Enter 发送，Shift+Enter 换行）',
)

/** 发送消息（SSE 流式） */
async function handleSend() {
  const text = input.value.trim()
  if (!text || sending.value) return

  // 1. 追加用户消息与 AI 占位消息（流式接收中）
  messages.value.push({ role: 'user', content: text })
  messages.value.push({ role: 'assistant', content: '', streaming: true, status: '连接服务…' })
  // 注意：push 后必须从数组取回响应式代理引用再修改字段；
  // 直接修改 push 前的原始对象会绕过 Vue 代理，导致流式内容不渲染（页面卡在"连接服务…"）
  const aiMsg = messages.value[messages.value.length - 1]
  input.value = ''
  sending.value = true
  scrollToBottom()
  autoResize()

  try {
    // 2. SSE 流式：status 阶段提示 + delta 打字机 + done 落定状态
    const done = await streamChat(text, sessionId.value, {
      onStatus: (stage, statusText) => {
        // 阶段切换时更新提示（生成方案阶段开始后隐藏，交给正文流式）
        aiMsg.status = stage === 'plan' || stage === 'reply' ? '' : statusText
      },
      onDelta: (content) => {
        aiMsg.status = ''
        aiMsg.content += content
        scrollToBottom()
      },
    })
    // 3. 会话与挂起态落定
    if (done.session_id) {
      sessionId.value = done.session_id
      localStorage.setItem(SESSION_KEY, done.session_id)
      // 通知侧边栏刷新历史列表 + 同步当前会话高亮
      window.dispatchEvent(new CustomEvent('opsagent:session-created', { detail: { sessionId: done.session_id } }))
    }
    pending.value = done.need_query ? done.pending_query || null : null
    // 方案完成：记录四段总结卡片（供折叠卡片渲染与导出）
    if (done.state === 'DONE' && done.sections) {
      aiMsg.sections = done.sections
    }
    if (done.sources) aiMsg.sources = done.sources
    // 回答质量自评卡片（闲聊直答的 done 事件不含该字段，自动不显示）
    if (done.evaluation) aiMsg.evaluation = done.evaluation
  } catch (e) {
    const err = e as Error & { error_code?: string; detail?: string }
    aiMsg.status = ''
    aiMsg.error_code = err.error_code
    // 分类错误：后端 error 事件已给出友好提示；网络异常给通用提示
    if (err.error_code) {
      aiMsg.content = (aiMsg.content ? aiMsg.content + '\n\n' : '') + err.message
    } else {
      aiMsg.content = (aiMsg.content ? aiMsg.content + '\n\n' : '')
        + `请求失败：${err.message}\n\n请确认后端已启动（run_local.bat）后再试。`
    }
  } finally {
    aiMsg.streaming = false
    aiMsg.status = ''
    sending.value = false
    scrollToBottom()
  }
}

/** 复制挂起的 SQL（人工去真实环境执行） */
async function copyPendingSql() {
  if (!pending.value) return
  await navigator.clipboard.writeText(pending.value.sql).catch(() => undefined)
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}
const copied = ref(false)

/** 导出状态（按消息索引与类型记录错误/成功提示） */
const exportState = ref<{ key: string; state: 'loading' | 'ok' | 'error'; msg?: string } | null>(null)

/** 结果提示保留时长（成功/失败状态展示更久，便于用户看清反馈） */
const STATE_MS = 6000

/** 通用导出执行（错误与成功提示 6s 自动清除） */
async function runExport(key: string, kind: 'markdown' | 'sql', body?: Record<string, unknown>) {
  exportState.value = { key, state: 'loading' }
  try {
    await exportFile(kind, body)
    exportState.value = { key, state: 'ok' }
  } catch (e) {
    exportState.value = { key, state: 'error', msg: e instanceof Error ? e.message : '导出失败' }
  } finally {
    setTimeout(() => (exportState.value = null), STATE_MS)
  }
}

/** 导出方案 .md（导出当前会话最后一条方案） */
function exportPlanMd() {
  if (!sessionId.value) return
  runExport('md', 'markdown', { session_id: sessionId.value })
}

/** 导出挂起查询 .sql */
function exportPendingSql() {
  if (!sessionId.value) return
  runExport('sql', 'sql', { session_id: sessionId.value })
}

/** 四段卡片 → 渲染数据（过滤空段，固定顺序） */
const SECTION_META: { key: keyof SummarySections; title: string }[] = [
  { key: 'conclusion', title: '结论' },
  { key: 'analysis', title: '原因分析' },
  { key: 'steps', title: '处理步骤' },
  { key: 'risks', title: '风险与验证' },
]
function sectionCards(s: SummarySections) {
  return SECTION_META
    .map(({ key, title }) => ({ key, title, body: (s[key] as string | null) || '' }))
    .filter((c) => c.body.trim() && c.key !== 'raw')
}

/** 开始新会话（清除当前上下文） */
function newSession() {
  sessionId.value = null
  localStorage.removeItem(SESSION_KEY)
  pending.value = null
  messages.value = []
}

/** 打开设置弹窗（通过自定义事件通知 App.vue） */
function goToSettings() {
  window.dispatchEvent(new CustomEvent('opsagent:open-settings', { detail: { firstTime: false } }))
}

// ---- 统一入库面板 ----

/** 入库面板是否展开 */
const kbPanelOpen = ref(false)
const batchKbState = ref<'idle' | 'loading' | 'ok' | 'error'>('idle')
const batchKbMsg = ref('')

/** 候选 AI 方案（非流式、非错误、有实质内容） */
const kbCandidates = computed(() => {
  return messages.value
    .filter(m => m.role === 'assistant' && !m.streaming && !m.error_code && (m.content || m.sections))
    .map((m, i) => {
      const firstLine = m.content?.split('\n').find(l => l.trim()) || ''
      const title = firstLine.replace(/[#*`]/g, '').slice(0, 50) || `方案 #${i + 1}`
      return { msg: m, title }
    })
})

/** 是否全选 */
const kbAllSelected = computed(() =>
  kbCandidates.value.length > 0 && kbCandidates.value.every(c => c.msg.selectedForKb),
)

/** 选中数量 */
const kbSelectedCount = computed(() =>
  kbCandidates.value.filter(c => c.msg.selectedForKb).length,
)

/** 全选 / 全不选 */
function toggleSelectAll() {
  const target = !kbAllSelected.value
  kbCandidates.value.forEach(c => (c.msg.selectedForKb = target))
}

/** 批量入库（逐条调后端 save_knowledge） */
async function doBatchSave() {
  if (!kbSelectedCount.value || !sessionId.value) return
  batchKbState.value = 'loading'
  batchKbMsg.value = ''
  const selected = kbCandidates.value.filter(c => c.msg.selectedForKb)
  let okCount = 0
  let failCount = 0
  for (const c of selected) {
    try {
      await saveSessionKnowledge(sessionId.value)
      okCount++
      c.msg.selectedForKb = false
    } catch {
      failCount++
    }
  }
  if (okCount > 0 && failCount === 0) {
    batchKbState.value = 'ok'
    batchKbMsg.value = `${okCount} 条已入库 ✓`
  } else if (okCount === 0) {
    batchKbState.value = 'error'
    batchKbMsg.value = '全部入库失败'
  } else {
    batchKbState.value = 'error'
    batchKbMsg.value = `${okCount} 条成功，${failCount} 条失败`
  }
  setTimeout(() => { batchKbState.value = 'idle'; batchKbMsg.value = '' }, 5000)
}

/** 快捷问题点击：填入输入框并聚焦 */
function fillQuestion(q: string) {
  input.value = q
  textareaRef.value?.focus()
  autoResize()
}

/** Enter 发送 / Shift+Enter 换行 */
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    handleSend()
  }
}

// 挂载：健康检查 + 恢复会话历史（支持侧边栏跳转 ?session=xxx）
onMounted(async () => {
  try {
    health.value = await getHealth()
  } catch {
    health.value = null
  }
  getSettingsStatus().then(s => { settingsStatus.value = s }).catch(() => {})
  // 侧边栏跳转：指定会话优先，并清掉 query 防止刷新重复
  const qSession = route.query.session as string | null
  if (qSession) {
    sessionId.value = qSession
    localStorage.setItem(SESSION_KEY, qSession)
    router.replace({ path: '/' })
  }
  // 恢复历史会话（刷新不丢上下文）
  if (sessionId.value) {
    const detail = await loadSession(sessionId.value)
    if (detail && detail.messages.length) {
      messages.value = detail.messages.map((m) => ({ role: m.role, content: m.content }))
      if (detail.state === 'QUERY_PENDING' && detail.pending_query) {
        try {
          pending.value = JSON.parse(detail.pending_query) as PendingQuery
        } catch {
          pending.value = null
        }
      }
      scrollToBottom()
      // 历史方案的四段卡片（最后一条 AI 消息）
      try {
        const r = await fetch(`/api/output/cards/${sessionId.value}`)
        if (r.ok) {
          const j = (await r.json()) as { data?: { sections?: SummarySections } }
          if (j.data?.sections) {
            const lastAi = [...messages.value].reverse().find((m) => m.role === 'assistant')
            if (lastAi) lastAi.sections = j.data.sections
          }
        }
      } catch { /* 卡片加载失败不影响历史恢复 */ }
    }
  }

  // 监听侧边栏事件
  window.addEventListener('opsagent:new-chat', onSidebarNewChat)
  window.addEventListener('opsagent:load-session', onSidebarLoadSession)
})

onBeforeUnmount(() => {
  window.removeEventListener('opsagent:new-chat', onSidebarNewChat)
  window.removeEventListener('opsagent:load-session', onSidebarLoadSession)
})

/** 侧边栏「新建对话」事件：清空当前会话 */
function onSidebarNewChat() {
  newSession()
}

/** 侧边栏「选择历史会话」事件：加载指定会话 */
async function onSidebarLoadSession(e: Event) {
  const detail = (e as CustomEvent).detail
  const sid = detail?.sessionId
  if (!sid || sid === sessionId.value) return
  sessionId.value = sid
  localStorage.setItem(SESSION_KEY, sid)
  messages.value = []
  pending.value = null
  const detail2 = await loadSession(sid)
  if (detail2 && detail2.messages.length) {
    messages.value = detail2.messages.map((m) => ({ role: m.role, content: m.content }))
    if (detail2.state === 'QUERY_PENDING' && detail2.pending_query) {
      try {
        pending.value = JSON.parse(detail2.pending_query) as PendingQuery
      } catch {
        pending.value = null
      }
    }
    scrollToBottom()
    try {
      const r = await fetch(`/api/output/cards/${sid}`)
      if (r.ok) {
        const j = (await r.json()) as { data?: { sections?: SummarySections } }
        if (j.data?.sections) {
          const lastAi = [...messages.value].reverse().find((m) => m.role === 'assistant')
          if (lastAi) lastAi.sections = j.data.sections
        }
      }
    } catch { /* 卡片加载失败不影响历史恢复 */ }
  }
}

// 消息数量变化时保持滚动到底部
watch(() => messages.value.length, scrollToBottom)
</script>

<template>
  <div class="chat-page">
    <!-- 消息区：有消息时展示流式对话 -->
    <div v-if="messages.length" ref="scrollRef" class="msg-scroll">
      <div class="msg-list">
        <template v-for="(m, i) in messages" :key="i">
          <!-- 用户消息：右侧气泡 -->
          <div v-if="m.role === 'user'" class="row user-row">
            <div class="user-bubble">{{ m.content }}</div>
          </div>

          <!-- AI 消息：左侧平铺 + 标签 + markdown 渲染 -->
          <div v-else class="row ai-row">
            <div class="ai-tag">OpsAgent</div>
            <div class="ai-content" :class="{ 'error-msg': m.error_code }">
              <span v-if="m.streaming && !m.content" class="typing-inline">
                <span v-if="m.status" class="status-text">{{ m.status }}</span>
                <span v-else class="typing-dots"><span /><span /><span /></span>
              </span>
              <!-- markdown 渲染（DOMPurify 消毒） -->
              <div v-else class="md-body" v-html="renderMd(m.content)" />

              <!-- 错误消息的快捷操作按钮 -->
              <div v-if="m.error_code" class="error-actions">
                <template v-if="m.error_code === 'invalid_key'">
                  <button class="err-btn" @click="goToSettings">⚙ 去设置 API Key</button>
                </template>
                <template v-else-if="m.error_code === 'quota_exceeded'">
                  <a class="err-btn" href="https://dashscope.console.aliyun.com/overview" target="_blank" rel="noopener">📈 查看百炼用量</a>
                </template>
                <template v-else-if="m.error_code === 'forbidden'">
                  <a class="err-btn" href="https://dashscope.console.aliyun.com/model" target="_blank" rel="noopener">🔧 检查模型授权</a>
                </template>
              </div>

              <!-- 四段总结卡片：默认折叠隐藏，点"查看方案摘要"才展开 -->
              <div v-if="m.sections && !m.streaming" class="summary-toggle">
                <button class="summary-toggle-btn" @click="m.summaryOpen = !m.summaryOpen">
                  {{ m.summaryOpen ? ' 收起方案摘要' : ' 查看方案摘要' }}
                  <span class="summary-count">({{ sectionCards(m.sections).length }} 段)</span>
                </button>
                <button class="export-inline-btn" :disabled="exportState?.key === 'md'" @click="exportPlanMd">
                  {{ exportState?.key === 'md' ? (exportState.state === 'ok' ? '已导出 ✓' : '导出中…') : '导出 .md' }}
                </button>
              </div>
              <div v-if="m.sections && m.summaryOpen && !m.streaming" class="summary-cards">
                <details v-for="c in sectionCards(m.sections)" :key="c.key" class="s-card" :open="c.key === 'conclusion'">
                  <summary class="s-card-title">{{ c.title }}</summary>
                  <div class="s-card-body md-body" v-html="renderMd(c.body)" />
                </details>
              </div>
              <!-- 回答质量评估卡片（仅工作类回答携带；闲聊无 evaluation 不显示） -->
              <div v-if="m.evaluation && !m.streaming" class="eval-card">
                <div class="eval-title">📊 回答质量评估</div>
                <div class="eval-metrics">
                  <div class="eval-metric">
                    <span class="eval-label">知识库命中度</span>
                    <span class="eval-value" :class="levelTone(m.evaluation.source_coverage, true)">
                      {{ m.evaluation.source_coverage || '—' }}
                    </span>
                  </div>
                  <div class="eval-metric">
                    <span class="eval-label">回答准确度</span>
                    <span class="eval-value" :class="accuracyTone(m.evaluation.accuracy)">
                      {{ m.evaluation.accuracy || '—' }}
                    </span>
                  </div>
                  <div class="eval-metric">
                    <span class="eval-label">幻觉风险</span>
                    <span class="eval-value" :class="levelTone(m.evaluation.hallucination_risk, false)">
                      {{ m.evaluation.hallucination_risk || '—' }}
                    </span>
                  </div>
                </div>
              </div>
              <!-- 引用来源：可点击查看原文档 -->
              <div v-if="m.sources && m.sources.length && !m.streaming" class="sources-row">
                <span class="sources-label">📎 来源文档：</span>
                <a
                  v-for="s in m.sources"
                  :key="s.doc_id"
                  class="source-link"
                  @click="openSource(s)"
                >{{ s.doc_title }}</a>
              </div>
            </div>
          </div>
        </template>
        <div class="msg-list-bottom" />
      </div>

      <!-- 会话级统一入库面板（收集当前会话所有 AI 方案，勾选后一次性入库） -->
      <div v-if="kbPanelOpen || kbCandidates.length" class="kb-panel">
        <button class="kb-panel-toggle" @click="kbPanelOpen = !kbPanelOpen">
          <span>选入知识库（{{ kbCandidates.length }} 条候选）</span>
          <span class="kb-arrow" :class="{ open: kbPanelOpen }">▾</span>
        </button>
        <div v-if="kbPanelOpen" class="kb-panel-body">
          <div v-if="!kbCandidates.length" class="kb-empty">暂无已完成的 AI 方案，先问个问题吧</div>
          <template v-else>
            <div class="kb-candidates">
              <label
                v-for="(c, i) in kbCandidates"
                :key="i"
                class="kb-cand-item"
                :class="{ checked: c.msg.selectedForKb }"
              >
                <input type="checkbox" v-model="c.msg.selectedForKb" />
                <span class="kb-cand-title">{{ c.title }}</span>
                <span v-if="c.msg.sections" class="kb-cand-sections">
                  ({{ sectionCards(c.msg.sections).length }} 段)
                </span>
              </label>
            </div>
            <div class="kb-panel-actions">
              <button class="kb-select-all" @click="toggleSelectAll">
                {{ kbAllSelected ? '全不选' : '全选' }}
              </button>
              <button
                class="kb-batch-btn"
                :disabled="!kbSelectedCount || batchKbState === 'loading'"
                @click="doBatchSave"
              >
                <template v-if="batchKbState === 'loading'">入库中…</template>
                <template v-else-if="batchKbState === 'ok'">已入库 ✓</template>
                <template v-else-if="batchKbState === 'error'">失败，重试</template>
                <template v-else>批量入库 ({{ kbSelectedCount }})</template>
              </button>
              <span v-if="batchKbMsg" class="kb-msg" :class="batchKbState">{{ batchKbMsg }}</span>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- 空状态：居中欢迎 -->
    <div v-else class="hero">
      <h1 class="hero-title">今天想排查什么问题？</h1>
      <p class="hero-sub">描述你的问题，我会给出方案包，或在需要真实数据时给出只读 SQL 供你回传结果</p>
      <div class="quick-list">
        <button v-for="q in quickQuestions" :key="q" class="quick-chip" @click="fillQuestion(q)">
          {{ q }}
        </button>
      </div>
    </div>

    <!-- 底部输入区（常驻） -->
    <div class="input-area">
      <!-- 挂起提示条：等待人工回传 SQL 结果 -->
      <div v-if="pending" class="pending-bar">
        <span class="pending-info">
           等待回传 · 第 {{ pending.round }}/5 轮 · {{ pending.purpose }}
        </span>
        <span class="pending-actions">
          <button class="pending-btn" @click="copyPendingSql">{{ copied ? '已复制 ✓' : '复制 SQL' }}</button>
          <button
            class="pending-btn ghost"
            :disabled="exportState?.key === 'sql'"
            @click="exportPendingSql"
          >
            {{ exportState?.key === 'sql' ? (exportState.state === 'ok' ? '已导出 ✓' : exportState.state === 'error' ? (exportState.msg || '导出失败') : '导出中…') : '导出 .sql' }}
          </button>
          <button class="pending-btn ghost" @click="newSession">放弃查询，开新会话</button>
        </span>
      </div>

      <div class="input-box">
        <textarea
          ref="textareaRef"
          v-model="input"
          class="input-textarea"
          rows="1"
          :placeholder="inputPlaceholder"
          @input="autoResize"
          @keydown="onKeydown"
        />
        <div class="input-actions">
          <span class="input-tip">
            <template v-if="pending">回传结果后我会继续分析（结果请先脱敏）</template>
            <template v-else>由知识库检索增强</template>
          </span>
          <button
            class="send-btn"
            :disabled="!input.trim() || sending"
            :class="{ enabled: input.trim() && !sending }"
            title="发送"
            @click="handleSend"
          >
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none">
              <path d="M12 19V5M12 5l-6 6M12 5l6 6" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </button>
        </div>
      </div>
      <!-- 状态小字 -->
      <div class="status-line">
        <template v-if="health">
          后端已连接<template v-if="settingsStatus"> · {{ settingsStatus.chat_provider_name }} · {{ settingsStatus.chat_model }}</template>
          <template v-if="settingsStatus && (!settingsStatus.chat_configured || !settingsStatus.dashscope_configured)"> · 模型未配置</template>
        </template>
        <template v-else>后端未连接</template>
      </div>

      <!-- 文档查看/编辑弹窗（引用来源点击打开） -->
      <DocViewDialog v-model:visible="viewerVisible" :doc-id="viewerDocId" />
    </div>
  </div>
</template>

<style scoped>
.chat-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--bg);
}

/* ---------- 消息区 ---------- */
.msg-scroll {
  flex: 1;
  overflow-y: auto;
}
.msg-list {
  max-width: 760px;
  margin: 0 auto;
  padding: 24px 20px 0;
}
.row {
  margin-bottom: 28px;
}
.user-row {
  display: flex;
  justify-content: flex-end;
}
.user-bubble {
  max-width: 80%;
  padding: 10px 16px;
  border-radius: 14px 14px 4px 14px;
  background: var(--bubble-user);
  color: var(--text-main);
  font-size: 16px;
  white-space: pre-wrap;
  word-break: break-word;
}
.ai-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ai-tag {
  font-size: 12px;
  font-weight: 600;
  color: var(--primary);
}
.ai-content {
  font-size: 16px;
  color: var(--text-main);
  line-height: 1.75;
  word-break: break-word;
}
.ai-content.error-msg {
  background: rgba(245, 63, 45, 0.05);
  border: 1px solid rgba(245, 63, 45, 0.2);
  border-radius: 10px;
  padding: 10px 14px;
}
.error-actions {
  margin-top: 10px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.err-btn {
  padding: 5px 12px;
  border: 1px solid var(--primary);
  border-radius: 8px;
  background: transparent;
  color: var(--primary);
  font-size: 12.5px;
  cursor: pointer;
  text-decoration: none;
  transition: all 0.15s;
}
.err-btn:hover {
  background: var(--primary-weak);
}
.msg-list-bottom {
  height: 12px;
}

/* 流式阶段提示 */
.typing-inline {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.status-text {
  font-size: 13px;
  color: var(--text-sub);
}
.typing-dots {
  display: inline-flex;
  gap: 4px;
}
.typing-dots span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--primary);
  opacity: 0.4;
  animation: blink 1.2s infinite;
}
.typing-dots span:nth-child(2) { animation-delay: 0.2s; }
.typing-dots span:nth-child(3) { animation-delay: 0.4s; }
@keyframes blink {
  0%, 80%, 100% { opacity: 0.25; transform: translateY(0); }
  40% { opacity: 1; transform: translateY(-2px); }
}

/* markdown 正文 */
.md-body :deep(p) { margin: 6px 0; }
.md-body :deep(h1), .md-body :deep(h2), .md-body :deep(h3), .md-body :deep(h4) {
  margin: 14px 0 8px;
  font-size: 15.5px;
  font-weight: 600;
}
.md-body :deep(ul), .md-body :deep(ol) { margin: 6px 0; padding-left: 22px; }
.md-body :deep(li) { margin: 3px 0; }
.md-body :deep(code) {
  padding: 1px 6px;
  border-radius: 5px;
  background: rgba(21, 93, 202, 0.07);
  font-size: 15px;
  font-family: Consolas, Monaco, monospace;
}
.md-body :deep(pre) {
  margin: 10px 0;
  padding: 12px 14px;
  border-radius: 10px;
  background: #f6f8fa;
  border: 1px solid var(--border);
  overflow-x: auto;
}
.md-body :deep(pre code) {
  padding: 0;
  background: transparent;
  font-size: 15px;
  line-height: 1.6;
}
.md-body :deep(blockquote) {
  margin: 8px 0;
  padding: 6px 12px;
  border-left: 3px solid var(--primary);
  background: var(--primary-weak);
  border-radius: 0 8px 8px 0;
  color: var(--text-sub);
  font-size: 13.5px;
}
.md-body :deep(table) { border-collapse: collapse; margin: 8px 0; font-size: 13.5px; }
.md-body :deep(th), .md-body :deep(td) { border: 1px solid var(--border); padding: 5px 10px; }
.md-body :deep(hr) { border: none; border-top: 1px solid var(--border); margin: 12px 0; }

/* ---------- 四段总结卡片 ---------- */
.summary-toggle {
  margin-top: 10px;
  display: flex;
  gap: 8px;
  align-items: center;
}
.summary-toggle-btn {
  padding: 6px 12px;
  border: 1px dashed var(--border);
  border-radius: 8px;
  background: transparent;
  color: var(--text-sub);
  font-size: 12.5px;
  cursor: pointer;
  transition: all 0.15s;
}
.summary-toggle-btn:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.summary-count {
  color: var(--text-sub);
  font-size: 12px;
}
.export-inline-btn {
  padding: 5px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: transparent;
  color: var(--text-sub);
  font-size: 12.5px;
  cursor: pointer;
  transition: all 0.15s;
}
.export-inline-btn:hover:not(:disabled) {
  color: var(--primary);
  border-color: var(--primary);
}
.export-inline-btn:disabled { opacity: 0.6; cursor: default; }

.summary-cards {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.s-card {
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  overflow: hidden;
}
.s-card-title {
  padding: 9px 14px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-main);
  cursor: pointer;
  user-select: none;
  list-style: none;
  display: flex;
  align-items: center;
  gap: 6px;
  background: var(--primary-weak);
}
.s-card-title::before {
  content: '▸';
  color: var(--primary);
  transition: transform 0.15s;
}
.s-card[open] .s-card-title::before {
  transform: rotate(90deg);
}
.s-card-body {
  padding: 10px 14px;
  font-size: 13.5px;
}
/* 导出工具行 */
.export-row {
  display: flex;
  gap: 8px;
}
.export-btn {
  padding: 6px 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-sub);
  font-size: 12.5px;
  cursor: pointer;
  transition: all 0.15s;
}
.export-btn:hover:not(:disabled) {
  color: var(--primary);
  border-color: var(--primary);
  background: var(--primary-weak);
}
.export-btn:disabled {
  cursor: default;
  opacity: 0.7;
}

/* ---------- 统一入库面板 ---------- */
.kb-panel {
  max-width: 760px;
  margin: 0 auto 20px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  overflow: hidden;
}
.kb-panel-toggle {
  width: 100%;
  padding: 10px 14px;
  border: none;
  background: rgba(21, 93, 202, 0.04);
  color: var(--primary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  transition: background 0.15s;
}
.kb-panel-toggle:hover { background: rgba(21, 93, 202, 0.08); }
.kb-arrow { transition: transform 0.2s; font-size: 11px; }
.kb-arrow.open { transform: rotate(180deg); }
.kb-panel-body { padding: 12px 14px; }
.kb-empty { font-size: 13px; color: var(--text-sub); padding: 8px 0; text-align: center; }
.kb-candidates { display: flex; flex-direction: column; gap: 6px; }
.kb-cand-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  transition: background 0.15s;
}
.kb-cand-item:hover { background: rgba(0, 0, 0, 0.03); }
.kb-cand-item.checked { background: rgba(21, 93, 202, 0.06); }
.kb-cand-item input[type="checkbox"] { width: 15px; height: 15px; accent-color: var(--primary); }
.kb-cand-title { color: var(--text-main); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-cand-sections { color: var(--text-sub); font-size: 12px; }
.kb-panel-actions {
  margin-top: 12px;
  display: flex;
  gap: 8px;
  align-items: center;
}
.kb-select-all {
  padding: 5px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: transparent;
  color: var(--text-sub);
  font-size: 12.5px;
  cursor: pointer;
  transition: all 0.15s;
}
.kb-select-all:hover { color: var(--primary); border-color: var(--primary); }
.kb-batch-btn {
  padding: 6px 16px;
  border: none;
  border-radius: 8px;
  background: var(--primary);
  color: #fff;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.kb-batch-btn:hover:not(:disabled) { filter: brightness(1.08); }
.kb-batch-btn:disabled { opacity: 0.5; cursor: default; }
.kb-msg { font-size: 12.5px; }
.kb-msg.ok { color: #18b26b; }
.kb-msg.error { color: #f53f2d; }

/* ---------- 空状态 ---------- */
.hero {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 0 24px;
  overflow-y: auto;
}
.hero-title {
  margin: 0 0 10px;
  font-size: 28px;
  font-weight: 700;
  color: var(--text-main);
}
.hero-sub {
  margin: 0 0 32px;
  font-size: 13.5px;
  color: var(--text-sub);
  text-align: center;
  max-width: 480px;
}
.quick-list {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
  max-width: 640px;
}
.quick-chip {
  padding: 11px 20px;
  border: 1px solid var(--border);
  border-radius: 20px;
  background: var(--surface);
  color: var(--text-main);
  font-size: 16px;
  cursor: pointer;
  transition: all 0.15s;
}
.quick-chip:hover {
  border-color: var(--primary);
  color: var(--primary);
  background: var(--primary-weak);
}

/* ---------- 底部输入区 ---------- */
.input-area {
  flex: none;
  padding: 8px 20px 12px;
}
/* 挂起提示条 */
.pending-bar {
  max-width: 760px;
  margin: 0 auto 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 9px 14px;
  border-radius: 12px;
  background: var(--primary-weak);
  border: 1px solid rgba(21, 93, 202, 0.18);
  font-size: 13px;
}
.pending-info {
  color: var(--text-main);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pending-actions {
  display: flex;
  gap: 8px;
  flex: none;
}
.pending-btn {
  padding: 5px 12px;
  border: none;
  border-radius: 8px;
  background: var(--primary);
  color: #fff;
  font-size: 12.5px;
  cursor: pointer;
  transition: all 0.15s;
}
.pending-btn:hover { filter: brightness(1.08); }
.pending-btn.ghost {
  background: transparent;
  color: var(--text-sub);
  border: 1px solid var(--border);
}
.pending-btn.ghost:hover { color: var(--text-main); border-color: var(--text-sub); }

.input-box {
  max-width: 760px;
  margin: 0 auto;
  background: var(--surface);
  border-radius: 18px;
  box-shadow: 0 2px 12px rgba(31, 35, 41, 0.06);
  padding: 14px 16px 12px;
}
.input-textarea {
  width: 100%;
  border: none;
  outline: none;
  resize: none;
  font-size: 16px;
  font-family: inherit;
  line-height: 1.6;
  color: var(--text-main);
  background: transparent;
  min-height: 40px;
  max-height: 160px;
}
.input-textarea::placeholder {
  color: var(--text-sub);
}
.input-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
}
.input-tip {
  font-size: 12px;
  color: var(--text-sub);
}
.send-btn {
  width: 34px;
  height: 34px;
  border: none;
  border-radius: 50%;
  background: #e3e5ea;
  color: #ffffff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
  flex: none;
}
.send-btn.enabled {
  background: var(--primary);
  cursor: pointer;
}
/* 回答质量评估卡片 */
.eval-card {
  margin-top: 12px;
  padding: 10px 14px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg-soft, rgba(0, 0, 0, 0.02));
}
.eval-title {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--text-sub);
  margin-bottom: 8px;
}
.eval-metrics {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
}
.eval-metric {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.eval-label {
  color: var(--text-sub);
}
.eval-value {
  font-weight: 700;
  font-size: 14px;
  padding: 1px 10px;
  border-radius: 999px;
}
.eval-value.good {
  color: #16a34a;
  background: rgba(22, 163, 74, 0.1);
}
.eval-value.warn {
  color: #d97706;
  background: rgba(217, 119, 6, 0.12);
}
.eval-value.bad {
  color: #dc2626;
  background: rgba(220, 38, 38, 0.1);
}
/* 引用来源链接区 */
.sources-row {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--border);
  font-size: 12.5px;
  color: var(--text-sub);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
}
.sources-label {
  color: var(--text-sub);
}
.source-link {
  color: var(--primary);
  cursor: pointer;
  text-decoration: none;
}
.source-link:hover {
  text-decoration: underline;
}
.send-btn.enabled:hover {
  filter: brightness(1.08);
}
.send-btn:disabled {
  cursor: default;
}
.status-line {
  max-width: 760px;
  margin: 8px auto 0;
  text-align: center;
  font-size: 11.5px;
  color: var(--text-sub);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
}
</style>
