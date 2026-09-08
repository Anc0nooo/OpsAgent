<template>
  <div class="admin-page">
    <div class="admin-header">
      <h2>管理后台</h2>
      <el-button @click="$router.push('/')">返回对话</el-button>
    </div>

    <el-tabs v-model="activeTab" class="admin-tabs">
      <!-- ===== 用户管理 ===== -->
      <el-tab-pane label="用户管理" name="users">
        <div class="toolbar">
          <el-input
            v-model="userSearch"
            placeholder="搜索用户名"
            style="width: 200px"
            clearable
            @keyup.enter="loadUsers(1)"
            @clear="loadUsers(1)"
          />
          <el-button @click="loadUsers(1)">搜索</el-button>
        </div>
        <el-table :data="users" v-loading="userLoading" stripe style="width: 100%">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="username" label="用户名" min-width="120" />
          <el-table-column label="角色" width="100">
            <template #default="{ row }">
              <el-tag :type="row.role === 'ancon' ? 'danger' : 'info'" size="small">
                {{ row.role === 'ancon' ? '管理员' : '普通用户' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="80">
            <template #default="{ row }">
              <el-tag :type="row.status === 1 ? 'success' : 'warning'" size="small">
                {{ row.status === 1 ? '正常' : '禁用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="created_at" label="注册时间" min-width="160" />
          <el-table-column label="操作" width="160" fixed="right">
            <template #default="{ row }">
              <el-button
                size="small"
                :type="row.status === 1 ? 'danger' : 'success'"
                :disabled="row.id === currentUserId"
                @click="toggleStatus(row)"
              >
                {{ row.status === 1 ? '禁用' : '启用' }}
              </el-button>
              <el-dropdown trigger="click" @command="(cmd: string) => handleAction(cmd, row)">
                <el-button
                  size="small"
                  :disabled="row.id === currentUserId"
                >更多<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="role">改角色</el-dropdown-item>
                    <el-dropdown-item command="reset">重置密码</el-dropdown-item>
                    <el-dropdown-item command="delete" divided class="danger-item">删除</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </template>
          </el-table-column>
        </el-table>
        <el-pagination
          v-model:current-page="userPage"
          :page-size="userPageSize"
          :total="userTotal"
          layout="total, prev, pager, next"
          @current-change="loadUsers()"
          style="margin-top: 16px"
        />
      </el-tab-pane>

      <!-- ===== 日志查看 ===== -->
      <el-tab-pane label="日志查看" name="logs">
        <div class="toolbar">
          <el-select v-model="logFilter.user_id" placeholder="全部用户" clearable filterable
                     style="width: 160px" @change="loadLogs(1)">
            <el-option v-for="u in users" :key="u.id" :label="u.username" :value="u.id" />
          </el-select>
          <el-select v-model="logFilter.action" placeholder="全部操作" clearable
                     style="width: 140px" @change="loadLogs(1)">
            <el-option v-for="a in actionOptions" :key="a" :label="a" :value="a" />
          </el-select>
          <el-date-picker
            v-model="logDateRange"
            type="daterange"
            range-separator="-"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            value-format="YYYY-MM-DD"
            style="width: 260px"
            @change="loadLogs(1)"
          />
          <el-button @click="loadLogs(1)">查询</el-button>
          <el-button @click="handleExport" :loading="exporting">导出 CSV</el-button>
        </div>
        <el-table :data="logs" v-loading="logLoading" stripe style="width: 100%">
          <el-table-column prop="created_at" label="时间" width="160" />
          <el-table-column prop="username" label="用户" min-width="100" />
          <el-table-column prop="action" label="操作" width="120">
            <template #default="{ row }">
              <el-tag size="small">{{ row.action }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="detail" label="详情" min-width="250" show-overflow-tooltip />
          <el-table-column prop="ip" label="IP" width="120" />
        </el-table>
        <el-pagination
          v-model:current-page="logPage"
          :page-size="logPageSize"
          :total="logTotal"
          layout="total, prev, pager, next"
          @current-change="loadLogs()"
          style="margin-top: 16px"
        />
      </el-tab-pane>

      <!-- ===== 权限说明 ===== -->
      <el-tab-pane label="权限说明" name="perms">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="管理员（ancon）">
            可管理所有用户（启用/禁用/改角色/重置密码/删除）、查看全部操作日志、导出 CSV；
            首个注册用户自动成为管理员，也可在 .env 中通过 ADMIN_USERNAME 指定。
          </el-descriptions-item>
          <el-descriptions-item label="普通用户（user）">
            可使用对话、知识库、设置等基础功能；数据按账号隔离，看不到他人内容；
            不显示管理后台入口，访问 /admin 路由会被自动跳转回首页。
          </el-descriptions-item>
          <el-descriptions-item label="数据隔离">
            知识库 Chroma collection 按 user_id 隔离；会话/消息/配置均按 user_id 隔离；
            删除用户时级联清理其全部数据（含 Chroma 向量）。
          </el-descriptions-item>
          <el-descriptions-item label="操作日志">
            记录关键操作：登录/注册/对话/上传文档/删除文档/检索/配置保存 等；
            日志含用户名、操作类型、详情摘要、IP 地址、时间，管理员可按条件筛选导出。
          </el-descriptions-item>
        </el-descriptions>
      </el-tab-pane>
    </el-tabs>

    <!-- 改角色弹窗 -->
    <el-dialog v-model="roleDialog.visible" title="修改角色" width="360px">
      <p>用户：{{ roleDialog.user?.username }}</p>
      <el-radio-group v-model="roleDialog.newRole">
        <el-radio value="ancon">管理员</el-radio>
        <el-radio value="user">普通用户</el-radio>
      </el-radio-group>
      <template #footer>
        <el-button @click="roleDialog.visible = false">取消</el-button>
        <el-button type="primary" @click="submitRole">确定</el-button>
      </template>
    </el-dialog>

    <!-- 重置密码弹窗 -->
    <el-dialog v-model="pwdDialog.visible" title="重置密码" width="380px">
      <p>用户：{{ pwdDialog.user?.username }}</p>
      <el-input v-model="pwdDialog.password" type="password" placeholder="新密码（至少 6 位）" show-password />
      <template #footer>
        <el-button @click="pwdDialog.visible = false">取消</el-button>
        <el-button type="primary" @click="submitReset">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown } from '@element-plus/icons-vue'
import {
  deleteUser, exportLogs, listLogs, listUsers, resetPassword,
  updateRole, updateStatus, type AdminUserItem, type LogItem,
} from '../api/admin'

const activeTab = ref('users')

// ===== 用户管理 =====
const users = ref<AdminUserItem[]>([])
const userLoading = ref(false)
const userPage = ref(1)
const userPageSize = 20
const userTotal = ref(0)
const userSearch = ref('')
const currentUserId = ref(0)

async function loadUsers(page?: number) {
  if (page) userPage.value = page
  userLoading.value = true
  try {
    const r = await listUsers({ page: userPage.value, page_size: userPageSize, keyword: userSearch.value })
    users.value = r.items
    userTotal.value = r.total
  } catch (e: any) {
    ElMessage.error(e.message || '加载失败')
  } finally {
    userLoading.value = false
  }
}

async function toggleStatus(row: AdminUserItem) {
  const newStatus = row.status === 1 ? 0 : 1
  try {
    await updateStatus(row.id, newStatus)
    row.status = newStatus
    ElMessage.success(newStatus === 1 ? '已启用' : '已禁用')
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  }
}

const roleDialog = reactive({ visible: false, user: null as AdminUserItem | null, newRole: 'user' })
function openRoleDialog(row: AdminUserItem) {
  roleDialog.user = row
  roleDialog.newRole = row.role
  roleDialog.visible = true
}
async function submitRole() {
  if (!roleDialog.user) return
  try {
    await updateRole(roleDialog.user.id, roleDialog.newRole)
    roleDialog.user.role = roleDialog.newRole
    roleDialog.visible = false
    ElMessage.success('角色已更新')
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  }
}

const pwdDialog = reactive({ visible: false, user: null as AdminUserItem | null, password: '' })
function openResetDialog(row: AdminUserItem) {
  pwdDialog.user = row
  pwdDialog.password = ''
  pwdDialog.visible = true
}
async function submitReset() {
  if (!pwdDialog.user) return
  if (pwdDialog.password.length < 6) {
    ElMessage.warning('密码至少 6 位')
    return
  }
  try {
    await resetPassword(pwdDialog.user.id, pwdDialog.password)
    pwdDialog.visible = false
    ElMessage.success('密码已重置')
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  }
}

async function confirmDelete(row: AdminUserItem) {
  try {
    await ElMessageBox.confirm(
      `确定删除用户「${row.username}」吗？\n将级联删除其配置、会话、消息、知识文档和全部向量数据，此操作不可恢复。`,
      '危险操作',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消', confirmButtonClass: 'el-button--danger' },
    )
    await deleteUser(row.id)
    ElMessage.success('用户已删除')
    await loadUsers()
  } catch (e: any) {
    if (e !== 'cancel' && e?.message) ElMessage.error(e.message)
  }
}

// 操作列下拉菜单分发
function handleAction(command: string, row: AdminUserItem) {
  if (command === 'role') openRoleDialog(row)
  else if (command === 'reset') openResetDialog(row)
  else if (command === 'delete') confirmDelete(row)
}

// ===== 日志查看 =====
const logs = ref<LogItem[]>([])
const logLoading = ref(false)
const logPage = ref(1)
const logPageSize = 20
const logTotal = ref(0)
const logFilter = reactive<{ user_id: number | null; action: string }>({ user_id: null, action: '' })
const logDateRange = ref<[string, string] | null>(null)
const exporting = ref(false)

const actionOptions = [
  'login', 'logout', 'register', 'chat', 'upload_doc', 'delete_doc',
  'retrieve', 'search', 'config_save', 'sql_query',
  'update_status', 'update_role', 'reset_password', 'delete_user',
]

async function loadLogs(page?: number) {
  if (page) logPage.value = page
  logLoading.value = true
  try {
    const r = await listLogs({
      page: logPage.value,
      page_size: logPageSize,
      user_id: logFilter.user_id ?? undefined,
      action: logFilter.action || undefined,
      start_time: logDateRange.value?.[0] || '',
      end_time: logDateRange.value?.[1] || '',
    })
    logs.value = r.items
    logTotal.value = r.total
  } catch (e: any) {
    ElMessage.error(e.message || '加载失败')
  } finally {
    logLoading.value = false
  }
}

async function handleExport() {
  exporting.value = true
  try {
    await exportLogs({
      user_id: logFilter.user_id ?? undefined,
      action: logFilter.action || undefined,
      start_time: logDateRange.value?.[0] || '',
      end_time: logDateRange.value?.[1] || '',
    })
    ElMessage.success('导出成功')
  } catch (e: any) {
    ElMessage.error(e.message || '导出失败')
  } finally {
    exporting.value = false
  }
}

onMounted(async () => {
  // 拿当前用户 ID（禁止操作自己）
  const token = localStorage.getItem('opsagent_token')
  if (token) {
    try {
      const payload = JSON.parse(atob(token.split('.')[1]))
      currentUserId.value = payload.user_id || 0
    } catch { /* ignore */ }
  }
  await loadUsers(1)
  await loadLogs(1)
})
</script>

<style scoped>
.admin-page {
  max-width: 1200px;
  margin: 0 auto;
  padding: 20px;
}
.admin-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.admin-header h2 {
  margin: 0;
  font-size: 20px;
}
.admin-tabs {
  margin-top: 8px;
}
.toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
  align-items: center;
}
/* 操作列下拉项：删除红色 */
:deep(.danger-item) {
  color: var(--el-color-danger);
}
:deep(.danger-item:hover) {
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}
</style>
