import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import Vant from 'vant'
import 'vant/lib/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import App from './App.vue'
import router from './router'
import './style.css'

// 创建应用实例：注册路由 + Element Plus + Vant（移动端组件库）
// （ElMessageBox/ElMessage 等函数式调用依赖此处的全局样式与插件注册，
//   否则确认弹窗会失去遮罩与居中定位，落到页面底部）
const app = createApp(App)
app.use(router)
app.use(ElementPlus, { locale: zhCn })
app.use(Vant)
app.mount('#app')

// ============================================================
// 移动端视口高度同步：软键盘弹出时 visualViewport 收缩而 100vh 不变，
// 把可视高度写入 --app-height，移动端布局用它代替 100vh，
// 保证对话页输入框始终在键盘上方、不被遮挡。
// ============================================================
function syncAppHeight() {
  const vv = window.visualViewport
  const h = vv ? vv.height : window.innerHeight
  document.documentElement.style.setProperty('--app-height', `${h}px`)
}
window.addEventListener('resize', syncAppHeight)
window.addEventListener('orientationchange', syncAppHeight)
window.visualViewport?.addEventListener('resize', syncAppHeight)
window.visualViewport?.addEventListener('scroll', syncAppHeight)
syncAppHeight()
