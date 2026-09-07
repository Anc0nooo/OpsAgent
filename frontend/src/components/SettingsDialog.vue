<script setup lang="ts">
/**
 * 设置弹窗（单一阿里百炼模型配置）
 * - 百炼 API Key（密文圆点 + 眼睛切换；已配置填占位符，聚焦清空输入新值）
 * - base_url / 对话模型名后端固定，弹窗不暴露
 * - 测试连接 + 保存（写当前用户 user_configs 表，按账号隔离）
 */
import { computed, onMounted, ref, watch } from 'vue'
import {
  getSettingsStatus,
  saveConfig,
  testConfig,
  type SettingsStatus,
} from '../api/settings'

const props = defineProps<{
  visible: boolean
  firstTime?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'saved'): void
}>()

const status = ref<SettingsStatus | null>(null)
const dashscopeKey = ref('')
/** 当前显示的是否为占位符（已配置但未输入新 key 时 true；聚焦/输入后转 false） */
const isKeyPlaceholder = ref(false)
/** 后端返回的 key 短掩码（如 sk-****Ch8M），用于输入框下方提示 */
const keyMasked = ref('')

const loading = ref(false)
const testMsg = ref<{ ok: boolean; text: string } | null>(null)
const saveMsg = ref<{ ok: boolean; text: string } | null>(null)

/** 测试可用：非占位状态 + 填了 Key（占位符时禁用测试，避免用假 key） */
const canTest = computed(
  () => !isKeyPlaceholder.value && !!dashscopeKey.value.trim(),
)
/** 保存可用：输入框有值（含占位符；占位符时保存为空操作不覆盖） */
const canSave = computed(
  () => !!dashscopeKey.value.trim(),
)

async function refreshStatus() {
  try {
    const s = await getSettingsStatus()
    status.value = s
    keyMasked.value = s.api_key_masked || ''
    if (s.has_api_key) {
      // 已配置：填入固定占位符（type=password 显示为圆点），不暴露真实 key
      dashscopeKey.value = '********************************'
      isKeyPlaceholder.value = true
    } else {
      dashscopeKey.value = ''
      isKeyPlaceholder.value = false
    }
  } catch { /* 后端未启动时静默 */ }
}

/** 输入框聚焦：若当前是占位符，清空供用户输入新 key */
function onKeyFocus() {
  if (isKeyPlaceholder.value) {
    dashscopeKey.value = ''
    isKeyPlaceholder.value = false
  }
}

async function doTest() {
  if (!canTest.value) return
  loading.value = true
  testMsg.value = null
  try {
    const r = await testConfig({
      dashscope_api_key: dashscopeKey.value.trim(),
    })
    testMsg.value = { ok: r.ok, text: r.message }
  } catch (e) {
    testMsg.value = { ok: false, text: e instanceof Error ? e.message : '测试失败' }
  } finally {
    loading.value = false
  }
}

async function doSave() {
  if (!canSave.value) return
  loading.value = true
  saveMsg.value = null
  try {
    const key = dashscopeKey.value.trim()
    // 占位状态（未改 key）或清空未输入：不传 api_key，后端不覆盖
    const sendKey = isKeyPlaceholder.value ? undefined : (key || undefined)
    const r = await saveConfig({
      dashscope_api_key: sendKey,
    })
    saveMsg.value = { ok: r.ok, text: r.message || (r.ok ? '保存成功' : '保存失败') }
    if (r.ok) {
      await refreshStatus()
      emit('saved')
    }
  } catch (e) {
    saveMsg.value = { ok: false, text: e instanceof Error ? e.message : '保存异常' }
  } finally {
    loading.value = false
  }
}

function close() {
  emit('update:visible', false)
}

onMounted(refreshStatus)

// 弹窗每次打开都重新拉取最新配置（.env 可能被外部修改）
watch(() => props.visible, (v) => { if (v) refreshStatus() })
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="visible" class="dialog-backdrop" @click.self="close">
        <div class="dialog-card" :class="{ 'first-time': firstTime }">
          <div class="dialog-header">
            <h3 class="dialog-title">
              {{ firstTime ? '首次使用 · 配置百炼模型' : '设置 · 模型配置' }}
            </h3>
            <button v-if="!firstTime" class="close-btn" @click="close">×</button>
          </div>

          <div class="dialog-body">
            <p v-if="firstTime" class="hint-text">
              填写阿里百炼 API Key 即可启用对话、向量与重排能力。如未持有 Key，可前往
              <a href="https://dashscope.aliyun.com/" target="_blank" rel="noopener">百炼控制台 ↗</a> 申请。
            </p>
            <p v-else class="hint-text small">
              配置按账号保存在数据库，不同账号各自独立、互不影响。
            </p>

            <!-- 百炼 API Key（密文 + 眼睛切换；已配置时填占位符圆点，聚焦清空输入新值） -->
            <div class="field">
              <div class="field-label-row">
                <label class="field-label required">阿里百炼 API Key</label>
                <a class="ext-link" href="https://dashscope.aliyun.com/" target="_blank" rel="noopener">获取 API Key →</a>
              </div>
              <el-input
                v-model="dashscopeKey"
                type="password"
                show-password
                class="field-el-input"
                placeholder="请输入 API Key"
                autocomplete="off"
                @focus="onKeyFocus"
              />
              <div v-if="keyMasked" class="field-hint">已配置（{{ keyMasked }}），输入新值将覆盖</div>
            </div>

            <!-- 测试连接 -->
            <div class="field">
              <button class="test-btn" :disabled="!canTest || loading" @click="doTest">
                {{ loading ? '测试中…' : '测试连接' }}
              </button>
              <div v-if="testMsg" class="msg" :class="testMsg.ok ? 'msg-ok' : 'msg-err'">{{ testMsg.text }}</div>
            </div>

            <!-- 向量/重排提示 -->
            <div class="embed-section">
              <div class="section-title">向量 / 重排（与对话共用百炼）</div>
              <p class="hint-text small">
                向量模型（text-embedding-v3）与重排模型（gte-rerank-v2）固定阿里百炼，与对话共用此 Key，无需单独配置。
              </p>
            </div>

            <!-- 当前模型信息 -->
            <div v-if="status" class="model-info">
              <div class="info-row"><span>当前对话</span><span>{{ status.chat_model }}</span></div>
              <div class="info-row"><span>向量模型</span><span>{{ status.embed_model }}</span></div>
              <div class="info-row"><span>重排模型</span><span>{{ status.rerank_model }}</span></div>
            </div>

            <div v-if="saveMsg" class="msg" :class="saveMsg.ok ? 'msg-ok' : 'msg-err'">{{ saveMsg.text }}</div>
          </div>

          <div class="dialog-footer">
            <button v-if="firstTime" class="btn ghost" @click="close">稍后配置</button>
            <button class="btn primary" :disabled="!canSave || loading" @click="doSave">
              {{ loading ? '保存中…' : '保存并应用' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.dialog-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(31, 35, 41, 0.45);
  backdrop-filter: blur(4px);
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
}
.dialog-card {
  width: 520px;
  max-width: 92vw;
  max-height: 88vh;
  overflow-y: auto;
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 20px 60px rgba(31, 35, 41, 0.18);
  display: flex;
  flex-direction: column;
}
.dialog-card.first-time { border-top: 3px solid var(--primary, #155dca); }
.dialog-header {
  padding: 20px 24px 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.dialog-title { margin: 0; font-size: 16px; font-weight: 600; color: var(--text-main, #1f2329); }
.close-btn {
  border: none; background: transparent; font-size: 22px; color: var(--text-sub, #86909c);
  cursor: pointer; width: 28px; height: 28px; border-radius: 6px; line-height: 1;
}
.close-btn:hover { background: rgba(0,0,0,0.06); color: var(--text-main, #1f2329); }
.dialog-body { padding: 16px 24px; flex: 1; }
.dialog-footer {
  padding: 14px 24px 20px;
  display: flex; justify-content: flex-end; gap: 10px;
  border-top: 1px solid var(--border, #e5e6eb);
}

.hint-text { font-size: 13px; color: var(--text-sub, #86909c); line-height: 1.6; margin: 0 0 16px; }
.hint-text.small { font-size: 12px; margin: 4px 0 10px; }
.hint-text a { color: var(--primary, #155dca); text-decoration: underline; }

.field { margin-bottom: 14px; }
.field-label {
  display: block; font-size: 12.5px; font-weight: 500; color: var(--text-sub, #86909c); margin-bottom: 6px;
}
.field-label-row {
  display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px;
}
.field-label-row .field-label { margin-bottom: 0; }
.ext-link {
  font-size: 12px; color: var(--primary, #155dca); text-decoration: none; white-space: nowrap;
}
.ext-link:hover { text-decoration: underline; }
.field-input {
  width: 100%; box-sizing: border-box; padding: 9px 12px;
  border: 1px solid var(--border, #e5e6eb); border-radius: 8px;
  font-size: 13.5px; color: var(--text-main, #1f2329); background: var(--surface, #fff);
  outline: none; transition: border-color 0.15s;
}
.field-input:focus { border-color: var(--primary, #155dca); }
.field-input.mono { font-family: Consolas, Monaco, monospace; font-size: 12.5px; }

/* el-input（API Key 密文输入）与周围原生 input 视觉统一 */
.field-el-input { width: 100%; }
.field-el-input :deep(.el-input__wrapper) {
  border-radius: 8px;
  padding: 9px 12px;
  background: var(--surface, #fff);
  box-shadow: 0 0 0 1px var(--border, #e5e6eb) inset;
  transition: box-shadow 0.15s;
}
.field-el-input :deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px var(--primary, #155dca) inset;
}
.field-el-input :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px var(--primary, #155dca) inset;
}
.field-el-input :deep(.el-input__inner) {
  height: 38px;
  font-size: 13.5px;
  color: var(--text-main, #1f2329);
}
.field-el-input :deep(.el-input__suffix) { color: var(--text-sub, #86909c); }
.field-hint { margin-top: 4px; font-size: 12px; color: var(--text-sub, #86909c); }

.test-btn {
  padding: 8px 16px; border: 1px solid var(--border, #e5e6eb); border-radius: 8px;
  background: transparent; color: var(--text-sub, #86909c); font-size: 13px; cursor: pointer;
  transition: all 0.15s;
}
.test-btn:hover:not(:disabled) { border-color: var(--primary, #155dca); color: var(--primary, #155dca); }
.test-btn:disabled { opacity: 0.5; cursor: default; }

.embed-section {
  margin: 14px 0; padding: 12px 14px;
  background: rgba(21, 93, 202, 0.04); border-radius: 10px; border: 1px solid rgba(21, 93, 202, 0.1);
}
.section-title { font-size: 13px; font-weight: 600; color: var(--text-main, #1f2329); margin-bottom: 4px; }

.msg { font-size: 12.5px; margin-top: 8px; padding: 7px 10px; border-radius: 6px; }
.msg-ok { background: rgba(24, 178, 107, 0.08); color: #18b26b; }
.msg-err { background: rgba(245, 63, 45, 0.08); color: #f53f2d; }

.model-info {
  margin-top: 14px; padding: 10px 14px;
  background: rgba(21, 93, 202, 0.04); border-radius: 8px;
}
.info-row {
  display: flex; justify-content: space-between; font-size: 12.5px;
  color: var(--text-sub, #86909c); line-height: 1.8;
}
.info-row span:last-child { color: var(--text-main, #1f2329); font-family: Consolas, Monaco, monospace; font-size: 12px; }

.btn {
  padding: 8px 20px; border-radius: 8px; font-size: 13.5px; cursor: pointer;
  transition: all 0.15s; border: 1px solid transparent;
}
.btn.primary { background: var(--primary, #155dca); color: #fff; }
.btn.primary:hover:not(:disabled) { filter: brightness(1.08); }
.btn.primary:disabled { opacity: 0.5; cursor: default; }
.btn.ghost { background: transparent; color: var(--text-sub, #86909c); border-color: var(--border, #e5e6eb); }
.btn.ghost:hover { color: var(--text-main, #1f2329); }

.fade-enter-active, .fade-leave-active { transition: opacity 0.18s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
