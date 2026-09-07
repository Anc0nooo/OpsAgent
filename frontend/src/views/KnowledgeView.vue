<script setup lang="ts">
/**
 * 知识库管理页（阶段 6 · 三栏布局版）
 * 左栏：文档列表（按类型分组，可折叠，内部滚动）
 * 中栏：上传文档（tab 切换：上传文件 / 粘贴文本 / 导入表结构）
 * 右栏：检索测试（输入行 + 结果区，结果多时内部滚动）
 * 整体高度 = 视口 - 顶部导航，不出现页面级滚动；< 1200px 降为上下布局
 */
import { computed, onMounted, ref } from 'vue'
import DocViewDialog from '../components/DocViewDialog.vue'
import {
  addText,
  changeDocType,
  deleteDoc,
  importChat,
  importTableSchema,
  listDocs,
  parseChat,
  reindex,
  searchDocs,
  uploadDoc,
  type ChatParseBody,
  type ChatPreview,
  type DocInfo,
  type SearchHit,
} from '../api/knowledge'
import { confirmDanger } from '../utils/dialog'

// ---------------- 文档列表 ----------------
const docs = ref<DocInfo[]>([])
const loadingDocs = ref(false)

async function refreshDocs() {
  loadingDocs.value = true
  try {
    docs.value = await listDocs()
  } catch (e) {
    toast(e instanceof Error ? e.message : '获取文档列表失败', true)
  } finally {
    loadingDocs.value = false
  }
}

async function removeDoc(d: DocInfo) {
  if (!(await confirmDanger(`确认删除文档「${d.title}」？其向量与索引将同步清理。`))) return
  try {
    await deleteDoc(d.id)
    toast('删除成功')
    await refreshDocs()
  } catch (e) {
    toast(e instanceof Error ? e.message : '删除失败', true)
  }
}

// ---------------- 重建索引（按新分块策略重新切分 + 重新向量化） ----------------
const reindexing = ref(false)
async function rebuildIndex() {
  if (reindexing.value) return
  if (!(await confirmDanger(
    '将按当前分块策略（更大块 + 重叠）对全部文档重新切分并重新向量化。文档内容保留不丢失，过程可能耗时较长，确认重建？',
  ))) return
  reindexing.value = true
  try {
    const r = await reindex()
    toast(`重建完成：${r.docs} 篇文档，${r.chunks} 个块`)
    await refreshDocs()
  } catch (e) {
    toast(e instanceof Error ? e.message : '重建索引失败', true)
  } finally {
    reindexing.value = false
  }
}

// ---------------- 文档查看/编辑弹窗 ----------------
const viewerDocId = ref<number | null>(null)
const viewerVisible = ref(false)
function viewDoc(d: DocInfo) {
  viewerDocId.value = d.id
  viewerVisible.value = true
}
function onSaved() {
  toast('文档已保存，索引已重建')
  refreshDocs()
}

// ---------------- 提示条 ----------------
const toastMsg = ref<{ text: string; error: boolean } | null>(null)
let toastTimer: ReturnType<typeof setTimeout> | undefined
function toast(text: string, error = false) {
  toastMsg.value = { text, error }
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toastMsg.value = null), 3000)
}

// ---------------- 文档类型（4 类，code + 中文 + 颜色）----------------
const DOC_TYPES = [
  { code: 'guide', label: '操作指导类', color: 'blue' },
  { code: 'bug', label: 'BUG修复类', color: 'orange' },
  { code: 'schema', label: '表结构类', color: 'green' },
  { code: 'other', label: '其他', color: 'gray' },
] as const

/** code → 中文标签（列表/检索结果展示用） */
const DOC_TYPE_LABEL: Record<string, string> = Object.fromEntries(
  DOC_TYPES.map((t) => [t.code, t.label]),
)
/** code → 颜色 class（标签样式用） */
const DOC_TYPE_COLOR: Record<string, string> = Object.fromEntries(
  DOC_TYPES.map((t) => [t.code, t.color]),
)

// ---------------- 上传 tab 切换 ----------------
const activeTab = ref<'file' | 'paste' | 'schema' | 'chat'>('file')

// ---------------- 分组折叠 ----------------
const collapsedGroups = ref<Record<string, boolean>>({})
function toggleGroup(code: string) {
  collapsedGroups.value = { ...collapsedGroups.value, [code]: !collapsedGroups.value[code] }
}
function isCollapsed(code: string) {
  return !!collapsedGroups.value[code]
}

// ---------------- 上传文档 ----------------
const uploadType = ref('guide')
const uploadTitle = ref('')
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

async function onFilePicked(ev: Event) {
  const files = (ev.target as HTMLInputElement).files
  if (!files || !files.length) return
  uploading.value = true
  try {
    for (const f of Array.from(files)) {
      await uploadDoc(f, uploadType.value, uploadTitle.value || undefined)
      toast(`「${f.name}」入库成功`)
    }
    uploadTitle.value = ''
    await refreshDocs()
  } catch (e) {
    toast(e instanceof Error ? e.message : '上传失败', true)
  } finally {
    uploading.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

// ---------------- 粘贴文本入库 ----------------
const pasteTitle = ref('')
const pasteType = ref('guide')
const pasteText = ref('')
const pasting = ref(false)

async function submitPaste() {
  if (!pasteTitle.value.trim() || !pasteText.value.trim()) {
    toast('请填写标题与内容', true)
    return
  }
  pasting.value = true
  try {
    await addText(pasteTitle.value.trim(), pasteText.value, pasteType.value)
    toast('入库成功')
    pasteTitle.value = ''
    pasteText.value = ''
    await refreshDocs()
  } catch (e) {
    toast(e instanceof Error ? e.message : '入库失败', true)
  } finally {
    pasting.value = false
  }
}

// ---------------- 表结构导入 ----------------
const schemaTable = ref('')
const schemaText = ref('')
const schemaLoading = ref(false)

async function submitSchema() {
  if (!schemaText.value.trim()) {
    toast('请粘贴 DDL 或 DESC 输出', true)
    return
  }
  schemaLoading.value = true
  try {
    const r = await importTableSchema(schemaText.value, schemaTable.value.trim())
    toast(`成功导入 ${r.count} 张表结构`)
    schemaText.value = ''
    schemaTable.value = ''
    await refreshDocs()
  } catch (e) {
    toast(e instanceof Error ? e.message : '导入失败', true)
  } finally {
    schemaLoading.value = false
  }
}

// ---------------- 导入聊天记录 ----------------
const chatSource = ref<'auto' | 'wechat' | 'feishu'>('auto')
const chatText = ref('')
const chatSplit = ref<'merge' | 'day' | 'person'>('merge')
const chatFilterSystem = ref(true)
const chatFilterMedia = ref(true)
const chatPreview = ref<ChatPreview | null>(null)
const chatParsing = ref(false)
const chatImporting = ref(false)
const chatFileInput = ref<HTMLInputElement | null>(null)

/** 读取上传的 txt 文件内容填入文本框 */
async function onChatFilePicked(ev: Event) {
  const files = (ev.target as HTMLInputElement).files
  if (!files || !files.length) return
  const f = files[0]
  if (!f.name.toLowerCase().endsWith('.txt')) {
    toast('仅支持 .txt 文件', true)
    return
  }
  chatText.value = await f.text()
  chatPreview.value = null
  if (chatFileInput.value) chatFileInput.value.value = ''
}

/** 构造聊天记录解析请求体 */
function chatBody(): ChatParseBody {
  return {
    source: chatSource.value,
    text: chatText.value,
    split_mode: chatSplit.value,
    filter_system: chatFilterSystem.value,
    filter_media: chatFilterMedia.value,
  }
}

/** 解析预览（不入库） */
async function runChatParse() {
  if (!chatText.value.trim()) {
    toast('请粘贴或上传聊天记录文本', true)
    return
  }
  chatParsing.value = true
  try {
    chatPreview.value = await parseChat(chatBody())
  } catch (e) {
    chatPreview.value = null
    toast(e instanceof Error ? e.message : '解析失败', true)
  } finally {
    chatParsing.value = false
  }
}

/** 确认导入知识库 */
async function confirmChatImport() {
  if (!chatPreview.value) return
  chatImporting.value = true
  try {
    const r = await importChat(chatBody())
    toast(`成功导入 ${r.count} 个文档`)
    resetChat()
    await refreshDocs()
  } catch (e) {
    toast(e instanceof Error ? e.message : '导入失败', true)
  } finally {
    chatImporting.value = false
  }
}

/** 重置聊天导入表单 */
function resetChat() {
  chatText.value = ''
  chatPreview.value = null
  chatSource.value = 'auto'
  chatSplit.value = 'merge'
  chatFilterSystem.value = true
  chatFilterMedia.value = true
}

// ---------------- 检索测试 ----------------
const searchQuery = ref('')
const searchType = ref('')
const searching = ref(false)
const hits = ref<SearchHit[] | null>(null)

async function runSearch() {
  if (!searchQuery.value.trim()) return
  searching.value = true
  hits.value = null
  try {
    hits.value = await searchDocs(searchQuery.value.trim(), searchType.value || undefined)
  } catch (e) {
    toast(e instanceof Error ? e.message : '检索失败', true)
  } finally {
    searching.value = false
  }
}

// 按分类分组（计算属性，docs 变化自动更新）
const docsByType = computed(() => (code: string) => docs.value.filter((d) => d.doc_type === code))
const countByType = computed(() => (code: string) => docs.value.filter((d) => d.doc_type === code).length)

// 列表内直接切换文档类型（轻量，不重新切分）
async function changeType(d: DocInfo, ev: Event) {
  const newType = (ev.target as HTMLSelectElement).value
  if (newType === d.doc_type) return
  try {
    await changeDocType(d.id, newType)
    toast('类型已更新')
    await refreshDocs()
  } catch (e) {
    toast(e instanceof Error ? e.message : '类型更新失败', true)
    await refreshDocs() // 失败回滚 select
  }
}

// 检索示例问题（点击填入并自动检索）
const searchExamples = [
  'ORA-01555 怎么处理',
  '输血执行率怎么统计',
  'C盘满了怎么清理',
  '护理评估单上传失败怎么排查',
]
function fillExample(q: string) {
  searchQuery.value = q
  runSearch()
}

onMounted(refreshDocs)
</script>

<template>
  <div class="kb-page">
    <!-- 顶部提示条 -->
    <div v-if="toastMsg" class="kb-toast" :class="{ error: toastMsg.error }">{{ toastMsg.text }}</div>

    <!-- 三栏布局 -->
    <div class="kb-cols">
      <!-- 左栏：文档列表（按类型分组，可折叠，内部滚动） -->
      <section class="kb-col kb-left">
        <div class="kb-col-head">
          <h3>文档列表</h3>
          <div class="head-actions">
            <button class="btn ghost sm" :disabled="reindexing" title="按当前分块策略重新切分并重新向量化全部文档" @click="rebuildIndex">
              {{ reindexing ? '重建中…' : '重建索引' }}
            </button>
            <button class="btn ghost sm" :disabled="loadingDocs" @click="refreshDocs">
              {{ loadingDocs ? '刷新中…' : '刷新' }}
            </button>
          </div>
        </div>
        <div class="kb-col-body">
          <div v-if="!docs.length && !loadingDocs" class="empty">暂无文档，可从中栏入库</div>
          <div v-else class="doc-groups">
            <div v-for="t in DOC_TYPES" :key="t.code" class="doc-group">
              <div class="group-head" @click="toggleGroup(t.code)">
                <span class="caret">{{ isCollapsed(t.code) ? '▸' : '▾' }}</span>
                <span class="type-tag" :class="'tag-' + t.color">{{ t.label }}</span>
                <span class="group-count">{{ countByType(t.code) }} 篇</span>
              </div>
              <div v-if="!isCollapsed(t.code) && docsByType(t.code).length" class="doc-list">
                <div v-for="d in docsByType(t.code)" :key="d.id" class="doc-item">
                  <div class="doc-main">
                    <span class="doc-title" :title="d.title">{{ d.title }}</span>
                    <span class="doc-meta">{{ d.chunk_count }} 块 · {{ d.created_at }}</span>
                  </div>
                  <div class="doc-actions">
                    <select class="type-select" :value="d.doc_type" title="切换类型" @change="changeType(d, $event)">
                      <option v-for="tt in DOC_TYPES" :key="tt.code" :value="tt.code">{{ tt.label }}</option>
                    </select>
                    <button class="btn ghost sm" @click="viewDoc(d)">查看</button>
                    <button class="btn ghost sm danger" @click="removeDoc(d)">删除</button>
                  </div>
                </div>
              </div>
              <div v-else-if="!isCollapsed(t.code)" class="empty-mini">该分类暂无文档</div>
            </div>
          </div>
        </div>
      </section>

      <!-- 中栏：上传文档（tab 切换三功能，内部滚动） -->
      <section class="kb-col kb-mid">
        <div class="kb-col-head"><h3>上传文档</h3></div>
        <div class="kb-col-body">
          <div class="tab-bar">
            <button class="tab-btn" :class="{ active: activeTab === 'file' }" @click="activeTab = 'file'">上传文件</button>
            <button class="tab-btn" :class="{ active: activeTab === 'paste' }" @click="activeTab = 'paste'">粘贴文本</button>
            <button class="tab-btn" :class="{ active: activeTab === 'schema' }" @click="activeTab = 'schema'">导入表结构</button>
            <button class="tab-btn" :class="{ active: activeTab === 'chat' }" @click="activeTab = 'chat'">导入聊天记录</button>
          </div>

          <div v-show="activeTab === 'file'" class="tab-pane">
            <div class="form-row">
              <label>类型</label>
              <select v-model="uploadType" class="input">
                <option v-for="t in DOC_TYPES" :key="t.code" :value="t.code">{{ t.label }}</option>
              </select>
            </div>
            <div class="form-row">
              <label>标题（可选，默认文件名）</label>
              <input v-model="uploadTitle" class="input" placeholder="自定义标题" />
            </div>
            <div class="form-row">
              <label>文件（txt / md / sql / pdf / docx，可多选）</label>
              <input ref="fileInput" type="file" class="file-input" multiple
                     accept=".txt,.md,.sql,.pdf,.docx" :disabled="uploading" @change="onFilePicked" />
            </div>
            <div v-if="uploading" class="hint">解析入库中，请稍候…</div>
          </div>

          <div v-show="activeTab === 'paste'" class="tab-pane">
            <div class="form-row">
              <label>标题</label>
              <input v-model="pasteTitle" class="input" placeholder="如：ORA-01653 处理手册" />
            </div>
            <div class="form-row">
              <label>类型</label>
              <select v-model="pasteType" class="input">
                <option v-for="t in DOC_TYPES" :key="t.code" :value="t.code">{{ t.label }}</option>
              </select>
            </div>
            <div class="form-row">
              <label>内容</label>
              <textarea v-model="pasteText" class="input area" rows="5" placeholder="粘贴文档内容…" />
            </div>
            <button class="btn primary" :disabled="pasting" @click="submitPaste">
              {{ pasting ? '入库中…' : '文本入库' }}
            </button>
          </div>

          <div v-show="activeTab === 'schema'" class="tab-pane">
            <div class="form-row">
              <label>表名（DESC 输出必填，DDL 可不填）</label>
              <input v-model="schemaTable" class="input" placeholder="如 LIS_LIFEALERT" />
            </div>
            <div class="form-row">
              <label>DDL 或 DESC 输出（支持多表）</label>
              <textarea v-model="schemaText" class="input area mono" rows="6"
                        placeholder="CREATE TABLE ... 或 NAME TYPE Nullable 风格的 DESC 输出" />
            </div>
            <button class="btn primary" :disabled="schemaLoading" @click="submitSchema">
              {{ schemaLoading ? '导入中…' : '导入表结构' }}
            </button>
          </div>

          <div v-show="activeTab === 'chat'" class="tab-pane">
            <div class="form-row">
              <label>来源</label>
              <div class="radio-group">
                <label class="radio-item"><input type="radio" v-model="chatSource" value="auto" /> 自动识别</label>
                <label class="radio-item"><input type="radio" v-model="chatSource" value="wechat" /> 微信</label>
                <label class="radio-item"><input type="radio" v-model="chatSource" value="feishu" /> 飞书</label>
              </div>
            </div>
            <div class="form-row">
              <label>聊天记录文件（.txt，微信/飞书导出）</label>
              <input ref="chatFileInput" type="file" class="file-input" accept=".txt" @change="onChatFilePicked" />
            </div>
            <div class="form-row">
              <label>或粘贴聊天记录文本</label>
              <textarea v-model="chatText" class="input area" rows="5"
                        placeholder="粘贴微信或飞书导出的聊天记录文本..." />
            </div>
            <div class="form-row">
              <label>拆分方式</label>
              <select v-model="chatSplit" class="input">
                <option value="merge">合并为一个文档</option>
                <option value="day">按天拆分</option>
                <option value="person">按人拆分</option>
              </select>
            </div>
            <div class="form-row">
              <label class="checkbox-row">
                <input type="checkbox" v-model="chatFilterSystem" /> 过滤系统消息（撤回/加入群聊等）
              </label>
            </div>
            <div class="form-row">
              <label class="checkbox-row">
                <input type="checkbox" v-model="chatFilterMedia" /> 过滤表情/图片/语音占位符
              </label>
            </div>
            <button class="btn" :disabled="chatParsing" @click="runChatParse">
              {{ chatParsing ? '解析中…' : '解析预览' }}
            </button>

            <div v-if="chatPreview" class="chat-preview">
              <div class="preview-stats">
                <span>消息数：<b>{{ chatPreview.count }}</b></span>
                <span>参与人：<b>{{ chatPreview.participants.length }}</b> 人</span>
                <span>识别格式：{{ chatPreview.format }}</span>
              </div>
              <div class="preview-range">时间范围：{{ chatPreview.time_range }}</div>
              <div class="preview-list">
                <div v-for="(m, i) in chatPreview.preview" :key="i" class="preview-msg">
                  <span class="preview-time">{{ m.time }}</span>
                  <span class="preview-sender">{{ m.sender }}:</span>
                  <span class="preview-content">{{ m.content }}</span>
                </div>
              </div>
            </div>

            <button class="btn primary" :disabled="!chatPreview || chatImporting" @click="confirmChatImport">
              {{ chatImporting ? '导入中…' : '确认导入' }}
            </button>
          </div>
        </div>
      </section>

      <!-- 右栏：检索测试（输入行 + 结果区，结果多时内部滚动） -->
      <section class="kb-col kb-right">
        <div class="kb-col-head"><h3>检索测试</h3></div>
        <div class="search-row">
          <input v-model="searchQuery" class="input grow" placeholder="输入测试问题，如：ORA-01555 怎么处理"
                 @keydown.enter="runSearch" />
          <select v-model="searchType" class="input">
            <option value="">全部</option>
            <option v-for="t in DOC_TYPES" :key="t.code" :value="t.code">{{ t.label }}</option>
          </select>
          <button class="btn primary" :disabled="searching" @click="runSearch">
            {{ searching ? '检索中…' : '检索' }}
          </button>
        </div>
        <div class="kb-col-body">
          <div v-if="hits === null" class="search-empty">
            <svg class="search-icon" viewBox="0 0 24 24" width="80" height="80" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <p class="search-empty-text">输入问题后点击检索，结果将显示在这里</p>
            <div class="search-examples">
              <button v-for="q in searchExamples" :key="q" class="example-chip" @click="fillExample(q)">{{ q }}</button>
            </div>
          </div>
          <div v-else-if="!hits.length" class="empty">无命中结果</div>
          <div v-else class="hit-list">
            <div v-for="(h, i) in hits" :key="i" class="hit-item">
              <div class="hit-head">
                <span class="hit-idx">{{ i + 1 }}</span>
                <span class="hit-title">{{ h.doc_title }}｜{{ h.section }}</span>
                <span class="hit-meta">
                  <span class="type-tag" :class="'tag-' + (DOC_TYPE_COLOR[h.doc_type] || 'gray')">
                    {{ DOC_TYPE_LABEL[h.doc_type] || h.doc_type }}
                  </span>
                  · 相关度 {{ h.score }}
                </span>
              </div>
              <div class="hit-text">{{ h.text.slice(0, 200) }}{{ h.text.length > 200 ? '…' : '' }}</div>
            </div>
          </div>
        </div>
      </section>
    </div>

    <!-- 文档查看/编辑弹窗 -->
    <DocViewDialog v-model:visible="viewerVisible" :doc-id="viewerDocId" @saved="onSaved" />
  </div>
</template>

<style scoped>
/* 三栏页面：整体填满视口，不出现页面级滚动 */
.kb-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-sizing: border-box;
  padding: 14px 16px;
}
/* 顶部提示条 */
.kb-toast {
  flex: none;
  margin-bottom: 12px;
  padding: 10px 16px;
  border-radius: 10px;
  background: var(--primary-weak);
  color: var(--text-main);
  font-size: 14px;
  border: 1px solid rgba(21, 93, 202, 0.18);
}
.kb-toast.error {
  background: #fef2f2;
  border-color: #fecaca;
  color: #b91c1c;
}
/* 三栏 grid：间距 20px */
.kb-cols {
  flex: 1;
  display: grid;
  grid-template-columns: 35fr 32fr 33fr;
  grid-template-areas: "left mid right";
  gap: 20px;
  min-height: 0;
  overflow: hidden;
}
.kb-col {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  overflow: hidden;
  min-width: 0;
  min-height: 0;
}
.kb-left { grid-area: left; }
.kb-mid { grid-area: mid; }
.kb-right { grid-area: right; }
.kb-col-head {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
}
.kb-col-head h3 {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  color: var(--text-main);
}
.kb-col-head .head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.kb-col-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px 16px;
  min-height: 0;
}
/* 文档分组（可折叠） */
.doc-groups { display: flex; flex-direction: column; gap: 12px; }
.doc-group {
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg);
  overflow: hidden;
}
.group-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  cursor: pointer;
  user-select: none;
  border-bottom: 1px solid var(--border);
}
.group-head:hover { background: var(--surface); }
.caret { font-size: 14px; color: var(--text-sub); flex: none; width: 16px; }
.group-count { margin-left: auto; font-size: 13px; color: var(--text-sub); }
.doc-list { display: flex; flex-direction: column; gap: 8px; padding: 10px; }
.doc-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: 64px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
}
.doc-main { min-width: 0; flex: 1; }
.doc-title {
  display: block;
  font-size: 16px;
  color: var(--text-main);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.doc-meta { display: block; font-size: 13px; color: var(--text-sub); margin-top: 4px; }
.doc-actions { display: flex; gap: 8px; flex: none; align-items: center; }
.type-select {
  padding: 5px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
  color: var(--text-main);
  background: var(--surface);
  outline: none;
  cursor: pointer;
}
.type-select:focus { border-color: var(--primary); }
.empty-mini { padding: 10px 0; text-align: center; color: var(--text-sub); font-size: 13px; }
/* 类型标签 */
.type-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 13px;
  font-weight: 500;
  line-height: 1.6;
}
.tag-blue { background: #dbeafe; color: #1e40af; border: 1px solid #93c5fd; }
.tag-orange { background: #ffedd5; color: #9a3412; border: 1px solid #fdba74; }
.tag-green { background: #dcfce7; color: #166534; border: 1px solid #86efac; }
.tag-gray { background: #f3f4f6; color: #4b5563; border: 1px solid #d1d5db; }
/* Tab 切换（上传三功能，字号 17px） */
.tab-bar {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 16px;
  flex: none;
}
.tab-btn {
  padding: 10px 16px;
  border: none;
  background: transparent;
  font-size: 17px;
  color: var(--text-sub);
  cursor: pointer;
  border-bottom: 2px solid transparent;
}
.tab-btn.active {
  color: var(--primary);
  border-bottom-color: var(--primary);
  font-weight: 600;
}
.tab-pane { display: flex; flex-direction: column; gap: 6px; }
/* 表单（间距 24px，输入框 40px 高） */
.form-row { margin-bottom: 16px; }
.form-row label { display: block; font-size: 14px; color: var(--text-sub); margin-bottom: 6px; }
.input {
  width: 100%;
  box-sizing: border-box;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 16px;
  font-family: inherit;
  color: var(--text-main);
  background: var(--bg);
  outline: none;
  min-height: 40px;
}
.input:focus { border-color: var(--primary); }
.input.area { resize: vertical; min-height: 120px; }
.input.mono { font-family: Consolas, Monaco, monospace; font-size: 15px; }
.input.grow { flex: 1; width: auto; }
.file-input { font-size: 13px; color: var(--text-sub); padding: 10px; border: 1px dashed var(--border); border-radius: 8px; background: var(--bg); width: 100%; box-sizing: border-box; }
.hint { font-size: 14px; color: var(--primary); }

/* 导入聊天记录 tab 样式 */
.radio-group {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.radio-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 14px;
  color: var(--text-main);
  cursor: pointer;
}
.radio-item input[type="radio"] {
  accent-color: var(--primary);
  cursor: pointer;
}
.checkbox-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: var(--text-main);
  cursor: pointer;
}
.checkbox-row input[type="checkbox"] {
  accent-color: var(--primary);
  cursor: pointer;
}
.chat-preview {
  margin: 8px 0;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
}
.preview-stats {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  font-size: 13px;
  color: var(--text-main);
  margin-bottom: 6px;
}
.preview-stats b {
  color: var(--primary);
  font-weight: 600;
}
.preview-range {
  font-size: 13px;
  color: var(--text-sub);
  margin-bottom: 8px;
}
.preview-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 180px;
  overflow-y: auto;
}
.preview-msg {
  font-size: 13px;
  line-height: 1.5;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.preview-time {
  color: var(--text-sub);
  flex: none;
}
.preview-sender {
  color: var(--primary);
  font-weight: 500;
  flex: none;
}
.preview-content {
  color: var(--text-main);
  flex: 1;
  min-width: 0;
  word-break: break-word;
}
/* 按钮（48px 高，全宽） */
.btn {
  padding: 12px 16px;
  border: none;
  border-radius: 8px;
  font-size: 16px;
  cursor: pointer;
  transition: all 0.15s;
  flex: none;
}
.btn.primary { background: var(--primary); color: #fff; }
.btn.primary:hover:not(:disabled) { filter: brightness(1.08); }
.btn.primary:disabled { opacity: 0.6; cursor: default; }
.btn.ghost { background: transparent; border: 1px solid var(--border); color: var(--text-sub); }
.btn.ghost:hover:not(:disabled) { color: var(--text-main); border-color: var(--text-sub); }
.btn.ghost.danger:hover { color: #b91c1c; border-color: #fecaca; }
.btn.sm { padding: 6px 12px; font-size: 14px; min-height: 32px; }
.tab-pane .btn.primary { width: 100%; min-height: 48px; }
/* 检索 */
.search-row {
  display: flex;
  gap: 8px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border);
  flex: none;
  background: var(--bg);
}
.search-row .input { min-height: 40px; }
.search-row .btn.primary { min-height: 48px; padding: 0 18px; }
.hit-list { display: flex; flex-direction: column; gap: 16px; }
.hit-item { border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; min-height: 80px; }
.hit-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.hit-idx {
  flex: none;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--primary-weak);
  color: var(--primary);
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.hit-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-main);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hit-meta { margin-left: auto; font-size: 14px; color: var(--text-sub); flex: none; }
.hit-text { font-size: 15px; color: var(--text-sub); line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.empty { padding: 24px 0; text-align: center; color: var(--text-sub); font-size: 14px; }
/* 检索空状态：大图标 + 提示 + 示例问题 */
.search-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 20px;
  text-align: center;
}
.search-icon { color: #d3d6dd; margin-bottom: 16px; }
.search-empty-text { font-size: 16px; color: var(--text-sub); margin: 0 0 20px; }
.search-examples { display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; max-width: 360px; }
.example-chip {
  padding: 8px 14px;
  border: 1px solid var(--border);
  border-radius: 18px;
  background: var(--surface);
  color: var(--text-main);
  font-size: 14px;
  cursor: pointer;
  transition: all 0.15s;
}
.example-chip:hover { border-color: var(--primary); color: var(--primary); background: var(--primary-weak); }

/* 响应式：< 1200px 上下布局 */
@media (max-width: 1199px) {
  .kb-page { overflow-y: auto; }
  .kb-cols {
    grid-template-columns: 1fr 1fr;
    grid-template-areas: "left left" "mid right";
    overflow: visible;
  }
  .kb-left { max-height: 360px; }
}
@media (max-width: 767px) {
  .kb-cols {
    grid-template-columns: 1fr;
    grid-template-areas: "left" "mid" "right";
  }
  .kb-left { max-height: 300px; }
}
</style>
