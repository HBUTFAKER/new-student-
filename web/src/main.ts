import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIcons from '@element-plus/icons-vue'

import 'element-plus/dist/index.css'
import '@/styles/index.css'

import App from './App.vue'
import { onSessionExpired } from '@/api/http'
import router from '@/router'
import { useAuthStore } from '@/stores/auth'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)
app.use(ElementPlus, { locale: zhCn })

for (const [name, component] of Object.entries(ElementPlusIcons)) {
  app.component(name, component)
}

// refresh token 也失效时：清登录态并回登录页，同时保留当前地址便于登录后回跳
onSessionExpired(() => {
  const auth = useAuthStore(pinia)
  auth.signOut()
  const current = router.currentRoute.value
  if (current.name === 'login') return
  void router.replace({ name: 'login', query: { redirect: current.fullPath } })
})

app.mount('#app')
