<script setup lang="ts">
/**
 * 历史会话页（阶段 6）
 * - 会话列表（标题/状态/查询轮次/更新时间）
 * - 打开会话 → 跳转对话页并加载该会话
 * - 删除会话（含消息）
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { deleteSession, listSessions, type SessionItem } from '../api/chat'
import { confirmDanger } from '../utils/dialog'

const router = useRouter()
const sessions = ref<SessionItem[]>([])
const loading = ref(false)

/** 状态中文映射 */
const STATE_LABEL: Record<string, string> = {
  IDLE: '空闲',
  PLANNING: '推理中',
  QUERY_PENDING: '等待回传',
  DONE: '已完成',
}

async function refresh() {
  loading.value = true
  try {
    sessions.value = await listSessions()
  } finally {
    loading.value = false
  }
}

function openSession(s: SessionItem) {
  // 跳转对话页并携带会话 ID（ChatView 识别 query.session 加载）
  router.push({ path: '/', query: { session: s.id } })
}

async function removeSession(s: SessionItem) {
  if (!(await confirmDanger(`确认删除会话「${s.title}」及其全部消息？`))) return
  await deleteSession(s.id)
  await refresh()
}

onMounted(refresh)
</script>

<template>
  <div class="page-scroll">
    <div class="page-inner">
      <div class="head">
        <h2 class="page-title">历史会话</h2>
        <button class="btn ghost" :disabled="loading" @click="refresh">
          {{ loading ? '刷新中…' : '刷新' }}
        </button>
      </div>

      <div class="session-list">
        <div v-if="!sessions.length && !loading" class="empty">暂无会话记录</div>
        <div v-for="s in sessions" :key="s.id" class="session-item" @click="openSession(s)">
          <div class="s-main">
            <span class="s-title">{{ s.title }}</span>
            <span class="s-meta">
              <span class="s-state" :class="{ pending: s.state === 'QUERY_PENDING' }">
                {{ STATE_LABEL[s.state] || s.state }}
              </span>
              <template v-if="s.query_round > 0"> · 已查询 {{ s.query_round }}/5 轮</template>
              · {{ s.updated_at }}
            </span>
          </div>
          <button class="btn ghost danger" @click.stop="removeSession(s)">删除</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page-scroll {
  height: 100%;
  overflow-y: auto;
}
.page-inner {
  max-width: 860px;
  margin: 0 auto;
  padding: 24px 20px 48px;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.page-title {
  margin: 0;
  font-size: 20px;
  color: var(--text-main);
}
.session-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 13px 16px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  cursor: pointer;
  transition: all 0.15s;
}
.session-item:hover {
  border-color: var(--primary);
  background: var(--primary-weak);
}
.s-main {
  min-width: 0;
}
.s-title {
  display: block;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-main);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.s-meta {
  display: block;
  font-size: 12px;
  color: var(--text-sub);
  margin-top: 3px;
}
.s-state {
  color: var(--text-sub);
}
.s-state.pending {
  color: var(--primary);
  font-weight: 500;
}
.btn {
  padding: 6px 14px;
  border-radius: 8px;
  font-size: 12.5px;
  cursor: pointer;
  transition: all 0.15s;
  flex: none;
}
.btn.ghost {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-sub);
}
.btn.ghost:hover {
  color: var(--text-main);
  border-color: var(--text-sub);
}
.btn.ghost.danger:hover {
  color: #b91c1c;
  border-color: #fecaca;
}
.empty {
  padding: 48px 0;
  text-align: center;
  color: var(--text-sub);
  font-size: 13.5px;
}
</style>
