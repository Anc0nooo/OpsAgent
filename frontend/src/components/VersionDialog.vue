<template>
  <!-- ============ PC：Element Plus 居中弹窗 ============ -->
  <el-dialog
    v-if="!isMobile"
    :model-value="visible"
    title="版本更新"
    width="480px"
    :close-on-click-modal="false"
    @close="onDismiss"
  >
    <div class="vd-head">
      <span class="vd-badge">{{ info.version || 'v1.0' }}</span>
      <span v-if="info.updated_at" class="vd-time">{{ info.updated_at.slice(0, 10) }}</span>
    </div>
    <div class="vd-body" :class="{ expanded: detailOpen }">
      <!-- eslint-disable-next-line vue/no-v-html -->
      <div class="md-body" v-html="renderedChangelog" />
    </div>
    <template #footer>
      <el-button @click="toggleDetail">{{ detailOpen ? '收起' : '查看详情' }}</el-button>
      <el-button type="primary" @click="onConfirm">知道了</el-button>
    </template>
  </el-dialog>

  <!-- ============ 移动端：Vant 居中弹层 ============ -->
  <van-popup
    v-else
    :show="visible"
    position="center"
    round
    :close-on-click-overlay="false"
    class="vd-popup"
    :style="{ width: 'calc(100vw - 40px)', maxWidth: '380px' }"
    @close="onDismiss"
  >
    <div class="vd-m">
      <div class="vd-m-title">版本更新</div>
      <div class="vd-head">
        <span class="vd-badge">{{ info.version || 'v1.0' }}</span>
        <span v-if="info.updated_at" class="vd-time">{{ info.updated_at.slice(0, 10) }}</span>
      </div>
      <div class="vd-body" :class="{ expanded: detailOpen }">
        <!-- eslint-disable-next-line vue/no-v-html -->
        <div class="md-body" v-html="renderedChangelog" />
      </div>
      <div class="vd-m-actions">
        <van-button block plain @click="toggleDetail">{{ detailOpen ? '收起' : '查看详情' }}</van-button>
        <van-button block type="primary" @click="onConfirm">知道了</van-button>
      </div>
    </div>
  </van-popup>
</template>

<script setup lang="ts">
/**
 * 版本更新弹窗
 * - PC：el-dialog 居中；移动端：van-popup position=center 圆角卡片
 * - 更新日志为 Markdown，marked 渲染为富文本
 * - 「知道了」：记录当前版本并关闭（confirm 事件，父组件写 localStorage）
 * - 「查看详情」：展开/收起完整日志（默认限高滚动）
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { marked } from 'marked'
import type { VersionInfo } from '../api/version'

const props = defineProps<{
  visible: boolean
  info: VersionInfo
}>()
const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'confirm'): void
}>()

const isMobile = ref(false)
function checkMobile() {
  isMobile.value = window.innerWidth < 768
}
onMounted(() => {
  checkMobile()
  window.addEventListener('resize', checkMobile)
})
onBeforeUnmount(() => window.removeEventListener('resize', checkMobile))

/** 详情展开状态（弹窗每次打开重置为收起） */
const detailOpen = ref(false)
watch(() => props.visible, (v) => {
  if (v) detailOpen.value = false
})

const renderedChangelog = computed(() => {
  const md = props.info.changelog?.trim()
  if (!md) return '<p style="color:#8a8f99">暂无更新日志</p>'
  return marked.parse(md, { async: false }) as string
})

function toggleDetail() {
  detailOpen.value = !detailOpen.value
}

function onConfirm() {
  emit('confirm')
  emit('update:visible', false)
}

function onDismiss() {
  emit('update:visible', false)
}
</script>

<style scoped>
.vd-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.vd-badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 12px;
  border-radius: 999px;
  background: var(--primary-weak, #ecf0ff);
  color: var(--primary, #4d6bfe);
  font-size: 14px;
  font-weight: 700;
}
.vd-time {
  font-size: 12px;
  color: var(--text-sub, #8a8f99);
}

/* 日志区：默认限高滚动，点「查看详情」展开 */
.vd-body {
  max-height: 240px;
  overflow-y: auto;
  padding-right: 4px;
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-main, #1f2329);
  transition: max-height 0.25s;
}
.vd-body.expanded {
  max-height: 60vh;
}
.vd-body :deep(h2) { font-size: 16px; margin: 4px 0 8px; }
.vd-body :deep(ul) { margin: 6px 0; padding-left: 20px; }
.vd-body :deep(li) { margin: 4px 0; }
.vd-body :deep(code) {
  background: rgba(0, 0, 0, 0.05);
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 13px;
}
.vd-body :deep(pre) {
  background: #f5f6f8;
  padding: 10px;
  border-radius: 8px;
  overflow-x: auto;
}

/* 移动端卡片 */
.vd-m {
  padding: 20px 18px calc(16px + env(safe-area-inset-bottom, 0px));
}
.vd-m-title {
  font-size: 17px;
  font-weight: 700;
  text-align: center;
  margin-bottom: 14px;
  color: var(--text-main, #1f2329);
}
.vd-m-actions {
  display: flex;
  gap: 10px;
  margin-top: 16px;
}
.vd-m-actions .van-button {
  min-height: 44px;
}
</style>
