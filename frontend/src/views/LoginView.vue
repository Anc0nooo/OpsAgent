<script setup lang="ts">
/**
 * 登录 / 注册页（单页切换，DeepSeek 风格：白底居中卡片、简洁无边框装饰）
 * - 登录成功：存 token → 拉取用户信息 → 跳首页
 * - 注册成功：自动切换到登录（回填用户名）
 */
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { login, register } from '../api/auth'
import { loadUser } from '../store/user'

const router = useRouter()

const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const loading = ref(false)

const isLogin = () => mode.value === 'login'

function toggleMode() {
  mode.value = isLogin() ? 'register' : 'login'
  confirmPassword.value = ''
}

async function submit() {
  const name = username.value.trim()
  const pwd = password.value
  if (!name || !pwd) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  if (!isLogin() && pwd !== confirmPassword.value) {
    ElMessage.warning('两次输入的密码不一致')
    return
  }

  loading.value = true
  try {
    if (isLogin()) {
      await login(name, pwd)
      await loadUser()
      ElMessage.success('登录成功')
    } else {
      await register(name, pwd)
      ElMessage.success('注册成功，请登录')
      // 注册成功自动切回登录并回填用户名
      mode.value = 'login'
      password.value = ''
      confirmPassword.value = ''
      return
    }
    router.push('/')
  } catch (e) {
    const msg = e instanceof Error ? e.message : '操作失败'
    // HTTPException 的 detail 由 axios 包装在 message 里，做一层友好转换
    if (msg.includes('用户名已存在')) ElMessage.error('用户名已存在，请换一个')
    else if (msg.includes('用户名或密码错误')) ElMessage.error('用户名或密码错误')
    else ElMessage.error(msg)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-logo">
        <span class="logo-mark">O</span>
        <span class="logo-text">OpsAgent 运维智能体</span>
      </div>

      <div class="login-tabs">
        <button
          class="tab-btn"
          :class="{ active: mode === 'login' }"
          @click="() => { if (mode !== 'login') toggleMode() }"
        >登录</button>
        <button
          class="tab-btn"
          :class="{ active: mode === 'register' }"
          @click="() => { if (mode !== 'register') toggleMode() }"
        >注册</button>
      </div>

      <form class="login-form" @submit.prevent="submit">
        <input
          v-model="username"
          class="form-input"
          type="text"
          placeholder="用户名"
          autocomplete="username"
          maxlength="50"
        />
        <input
          v-model="password"
          class="form-input"
          type="password"
          placeholder="密码（至少 6 位）"
          :autocomplete="isLogin() ? 'current-password' : 'new-password'"
          maxlength="128"
        />
        <input
          v-if="!isLogin()"
          v-model="confirmPassword"
          class="form-input"
          type="password"
          placeholder="确认密码"
          autocomplete="new-password"
          maxlength="128"
        />
        <button class="submit-btn" type="submit" :disabled="loading">
          {{ loading ? '请稍候…' : (isLogin() ? '登 录' : '注 册') }}
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f7f7f8;
}

.login-card {
  width: 360px;
  padding: 36px 32px 32px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.05);
}

.login-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 24px;
}
.logo-mark {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: var(--primary);
  color: #fff;
  font-size: 18px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}
.logo-text {
  font-size: 17px;
  font-weight: 600;
  color: var(--text-main);
}

/* 登录/注册切换 tab */
.login-tabs {
  display: flex;
  gap: 18px;
  margin-bottom: 20px;
  border-bottom: 1px solid var(--border);
}
.tab-btn {
  padding: 8px 2px;
  border: none;
  background: transparent;
  font-size: 14.5px;
  color: var(--text-sub);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: all 0.15s;
}
.tab-btn:hover {
  color: var(--text-main);
}
.tab-btn.active {
  color: var(--primary);
  font-weight: 600;
  border-bottom-color: var(--primary);
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.form-input {
  box-sizing: border-box;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 14px;
  color: var(--text-main);
  background: #fff;
  outline: none;
  transition: border-color 0.15s;
}
.form-input:focus {
  border-color: var(--primary);
}
.form-input::placeholder {
  color: var(--text-sub);
}

.submit-btn {
  margin-top: 6px;
  padding: 10px 0;
  border: none;
  border-radius: 8px;
  background: var(--primary);
  color: #fff;
  font-size: 14.5px;
  font-weight: 500;
  cursor: pointer;
  transition: filter 0.15s;
}
.submit-btn:hover:not(:disabled) {
  filter: brightness(1.08);
}
.submit-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>
