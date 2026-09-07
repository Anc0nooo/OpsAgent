import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import App from './App.vue'
import router from './router'
import './style.css'

// 创建应用实例：注册路由 + Element Plus
// （ElMessageBox/ElMessage 等函数式调用依赖此处的全局样式与插件注册，
//   否则确认弹窗会失去遮罩与居中定位，落到页面底部）
const app = createApp(App)
app.use(router)
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')
