<script setup lang="ts">
/**
 * 文档查看/编辑弹窗（通用）
 * - 知识库文档列表「查看」按钮调用
 * - ChatView 方案引用来源链接点击调用
 * - 支持查看 ↔ 编辑切换；编辑保存后重新切分入库（向量与索引同步重建）
 */
import { ref, watch } from 'vue'
import { getDoc, updateDoc, type DocDetail } from '../api/knowledge'

const props = defineProps<{
  docId: number | null
  visible: boolean
}>()
const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'saved'): void
}>()

// 文档类型（code + 中文标签）
const DOC_TYPES = [
  { code: 'guide', label: '操作指导类' },
  { code: 'bug', label: 'BUG修复类' },
  { code: 'schema', label: '表结构类' },
  { code: 'other', label: '其他' },
] as const

const loading = ref(false)
const doc = ref<DocDetail | null>(null)
const editing = ref(false)
const saving = ref(false)
const errMsg = ref('')

const form = ref({ title: '', text: '', doc_type: 'guide' })

/** 加载文档全文 */
async function load() {
  if (props.docId == null) return
  loading.value = true
  errMsg.value = ''
  editing.value = false
  try {
    doc.value = await getDoc(props.docId)
  } catch (e) {
    errMsg.value = e instanceof Error ? e.message : '加载失败'
    doc.value = null
  } finally {
    loading.value = false
  }
}

function close() {
  emit('update:visible', false)
}

function startEdit() {
  if (!doc.value) return
  form.value = {
    title: doc.value.title,
    text: doc.value.text,
    doc_type: doc.value.doc_type,
  }
  editing.value = true
}

function cancelEdit() {
  editing.value = false
}

/** 保存编辑（重新切分入库） */
async function save() {
  if (props.docId == null) return
  if (!form.value.title.trim() || !form.value.text.trim()) {
    errMsg.value = '标题与内容不能为空'
    return
  }
  saving.value = true
  errMsg.value = ''
  try {
    await updateDoc(props.docId, {
      title: form.value.title.trim(),
      text: form.value.text,
      doc_type: form.value.doc_type,
    })
    editing.value = false
    await load() // 重新加载（块数 / updated_at 刷新）
    emit('saved')
  } catch (e) {
    errMsg.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    saving.value = false
  }
}

// 打开或切换文档时自动加载
watch(
  () => [props.visible, props.docId],
  ([v]) => {
    if (v) load()
  },
)
</script>

<template>
  <transition name="fade">
    <div v-if="visible" class="overlay" @click.self="close">
      <div class="dialog">
        <div class="head">
          <div class="head-left">
            <span class="title">{{ doc ? (editing ? '编辑文档' : doc.title) : '文档详情' }}</span>
            <span v-if="doc && !editing" class="meta">
              {{ doc.chunk_count }} 块 · {{ doc.updated_at || doc.created_at }}
            </span>
          </div>
          <div class="head-right">
            <template v-if="!editing">
              <button class="btn ghost" :disabled="!doc" @click="startEdit">编辑</button>
              <button class="btn ghost" @click="close">关闭</button>
            </template>
            <template v-else>
              <button class="btn primary" :disabled="saving" @click="save">
                {{ saving ? '保存中…' : '保存' }}
              </button>
              <button class="btn ghost" :disabled="saving" @click="cancelEdit">取消</button>
            </template>
          </div>
        </div>

        <div class="body">
          <div v-if="errMsg" class="err">{{ errMsg }}</div>
          <div v-if="loading" class="hint">加载中…</div>
          <template v-else-if="doc">
            <!-- 编辑模式 -->
            <div v-if="editing" class="edit-form">
              <div class="form-row">
                <label>标题</label>
                <input v-model="form.title" class="input" placeholder="文档标题" />
              </div>
              <div class="form-row">
                <label>类型</label>
                <select v-model="form.doc_type" class="input">
                  <option v-for="t in DOC_TYPES" :key="t.code" :value="t.code">{{ t.label }}</option>
                </select>
              </div>
              <div class="form-row">
                <label>正文</label>
                <textarea v-model="form.text" class="input area mono" rows="18" placeholder="文档正文…"></textarea>
              </div>
            </div>
            <!-- 查看模式 -->
            <div v-else class="view">
              <div class="type-line">
                类型：{{ DOC_TYPES.find((t) => t.code === doc!.doc_type)?.label || doc!.doc_type }}
                · 来源：{{ doc!.source || '-' }}
              </div>
              <pre class="doc-text">{{ doc.text }}</pre>
            </div>
          </template>
        </div>
      </div>
    </div>
  </transition>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.42);
  backdrop-filter: blur(3px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2000;
}
.dialog {
  width: min(820px, 92vw);
  max-height: 86vh;
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  overflow: hidden;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}
.head-left {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-main);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.meta {
  font-size: 12px;
  color: var(--text-sub);
}
.head-right {
  display: flex;
  gap: 8px;
  flex: none;
}
.body {
  padding: 14px 16px;
  overflow-y: auto;
  flex: 1;
}
.hint {
  color: var(--text-sub);
  font-size: 13px;
  padding: 20px 0;
  text-align: center;
}
.err {
  color: #b91c1c;
  font-size: 12.5px;
  margin-bottom: 10px;
}
.type-line {
  font-size: 12.5px;
  color: var(--text-sub);
  margin-bottom: 10px;
}
.doc-text {
  margin: 0;
  font-family: Consolas, Monaco, monospace;
  font-size: 13px;
  line-height: 1.65;
  color: var(--text-main);
  white-space: pre-wrap;
  word-break: break-word;
}
.edit-form .form-row {
  margin-bottom: 10px;
}
.form-row label {
  display: block;
  font-size: 12.5px;
  color: var(--text-sub);
  margin-bottom: 4px;
}
.input {
  width: 100%;
  box-sizing: border-box;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
  font-family: inherit;
  color: var(--text-main);
  background: var(--bg);
  outline: none;
}
.input:focus {
  border-color: var(--primary);
}
.input.area {
  resize: vertical;
  min-height: 220px;
}
.input.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12.5px;
}
.btn {
  padding: 7px 14px;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.btn.primary {
  background: var(--primary);
  color: #fff;
}
.btn.primary:hover:not(:disabled) {
  filter: brightness(1.08);
}
.btn.primary:disabled {
  opacity: 0.6;
  cursor: default;
}
.btn.ghost {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-sub);
}
.btn.ghost:hover:not(:disabled) {
  color: var(--text-main);
  border-color: var(--text-sub);
}
.btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.18s;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
