import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // 将 /api 请求代理到本地 FastAPI 后端，避免跨域
    // 注意：SSE 流式响应需关闭缓冲（http-proxy 默认对流式透传，显式声明更稳）
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
