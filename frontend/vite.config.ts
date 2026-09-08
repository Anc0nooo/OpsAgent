import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import pxToViewport from 'postcss-px-to-viewport-8-plugin'

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
  css: {
    postcss: {
      plugins: [
        // vw 自适应：设计稿宽度 375（Vant 官方基准）
        // 关键策略：只转换 Vant 组件的 px；
        //   - 项目自身 src 的 px 一律排除 → 桌面端布局像素级不变
        //   - node_modules 中除 vant 外（如 element-plus）也排除 → PC 组件尺寸不受影响
        // 项目自身的移动端样式写在 @media (max-width: 768px) 中，直接用 px（触控尺寸固定值更稳）
        pxToViewport({
          unitToConvert: 'px',
          viewportWidth: 375,
          unitPrecision: 5,
          propList: ['*'],
          viewportUnit: 'vw',
          fontViewportUnit: 'vw',
          selectorBlackList: ['.ignore-vw'],
          minPixelValue: 1,
          mediaQuery: false,
          replace: true,
          exclude: [
            /[\\/]src[\\/]/,                     // 排除项目源码（PC px 原样保留）
            /node_modules[\\/](?!vant)/,         // 排除 node_modules 中除 vant 外的一切
          ],
          landscape: false,
        }),
      ],
    },
  },
})
