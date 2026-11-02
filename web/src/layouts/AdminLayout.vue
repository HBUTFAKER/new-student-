<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance } from 'element-plus'

import { deactivateMyAccount } from '@/api/staff'
import { ApiError } from '@/api/http'
import { getMySubscription } from '@/api/subscription'
import { menuForRole } from '@/config/menu'
import { useAuthStore } from '@/stores/auth'
import { ROLE_LABELS } from '@/types/auth'
import type { SubscriptionStatus } from '@/types/subscription'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const menuItems = computed(() => menuForRole(auth.role))
const openedMenus = computed(() =>
  menuItems.value.filter((item) => item.children?.length).map((item) => item.path),
)
const roleLabel = computed(() => (auth.role ? ROLE_LABELS[auth.role] : ''))
const pageTitle = computed(() => (typeof route.meta.title === 'string' ? route.meta.title : ''))

// ---------------------------------------------------------------- 订阅到期横幅（7.2）

const subscription = ref<SubscriptionStatus | null>(null)

const banner = computed<{ type: 'warning' | 'error' | 'info'; text: string } | null>(() => {
  const sub = subscription.value
  if (!sub) return null
  if (sub.expired) {
    return { type: 'error', text: '服务已到期，当前为只读模式，请联系平台续费' }
  }
  const days = sub.days_left
  if (days <= 3) {
    return {
      type: 'error',
      text: `${sub.plan_name} 距到期还有 ${days} 天，请尽快续费，到期后系统将转为只读`,
    }
  }
  if (days <= 7) {
    return {
      type: 'warning',
      text: `${sub.plan_name} 距到期还有 ${days} 天，到期后系统将转为只读`,
    }
  }
  // 047：只要订阅有到期日就常显，>7 天用次要色不制造焦虑。
  return { type: 'info', text: `${sub.plan_name} 距到期还有 ${days} 天` }
})

onMounted(async () => {
  try {
    subscription.value = await getMySubscription()
  } catch {
    // 拉取失败静默：到期 402 由 http 拦截器统一提示，这里不做横幅
  }
})

async function handleSignOut(): Promise<void> {
  try {
    await ElMessageBox.confirm('退出后需要重新登录，确认退出？', '退出登录', {
      confirmButtonText: '退出登录',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  auth.signOut()
  await router.replace({ name: 'login' })
}

// ---------------------------------------------------------------- 自助注销

const deactivateVisible = ref(false)
const deactivateForm = reactive({ password: '' })
const deactivating = ref(false)
const deactivateRef = ref<FormInstance>()

function handleCommand(command: string): void {
  if (command === 'deactivate') {
    deactivateForm.password = ''
    deactivateVisible.value = true
  } else if (command === 'signout') {
    void handleSignOut()
  }
}

async function submitDeactivate(): Promise<void> {
  if (!deactivateRef.value) return
  const valid = await deactivateRef.value.validate().catch(() => false)
  if (!valid) return

  deactivating.value = true
  try {
    await deactivateMyAccount(deactivateForm.password)
    deactivateVisible.value = false
    ElMessage.success('账号已注销')
    auth.signOut()
    await router.replace({ name: 'login' })
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '注销失败，请稍后重试')
  } finally {
    deactivating.value = false
  }
}
</script>

<template>
  <el-container class="layout">
    <el-aside width="220px" class="layout__aside">
      <div class="layout__brand">
        <span class="layout__brand-mark">托</span>
        <span class="layout__brand-text">机构管理系统</span>
      </div>
      <el-menu
        :default-active="route.path"
        :default-openeds="openedMenus"
        router
        class="layout__menu"
      >
        <template v-for="item in menuItems" :key="item.path">
          <el-sub-menu v-if="item.children && item.children.length > 0" :index="item.path">
            <template #title>
              <el-icon><component :is="item.icon" /></el-icon>
              <span>{{ item.title }}</span>
            </template>
            <el-menu-item v-for="child in item.children" :key="child.path" :index="child.path">
              <el-icon><component :is="child.icon" /></el-icon>
              <span>{{ child.title }}</span>
            </el-menu-item>
          </el-sub-menu>
          <el-menu-item v-else :index="item.path">
            <el-icon><component :is="item.icon" /></el-icon>
            <span>{{ item.title }}</span>
          </el-menu-item>
        </template>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="layout__header">
        <div class="layout__title">{{ pageTitle }}</div>
        <div class="layout__user">
          <el-tag size="small" type="info" effect="plain">{{ roleLabel }}</el-tag>
          <el-dropdown trigger="click" @command="handleCommand">
            <span class="layout__account layout__account--btn">
              {{ auth.account }}
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="deactivate">注销我的账号</el-dropdown-item>
                <el-dropdown-item command="signout" divided>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-alert
        v-if="banner"
        :title="banner.text"
        :type="banner.type"
        :closable="false"
        show-icon
        class="layout__banner"
      />

      <el-main class="layout__main">
        <RouterView />
      </el-main>

      <!-- 注销我的账号 -->
      <el-dialog v-model="deactivateVisible" title="注销我的账号" width="440px">
        <el-alert
          title="注销后账号立即停用，无法登录；历史记录保留；同手机号可由管理员重新启用。"
          type="warning"
          :closable="false"
          show-icon
          class="layout__deactivate-alert"
        />
        <el-form ref="deactivateRef" :model="deactivateForm" label-width="80px" class="layout__deactivate-form">
          <el-form-item
            label="当前密码"
            prop="password"
            :rules="[{ required: true, message: '请输入当前密码', trigger: 'blur' }]"
          >
            <el-input
              v-model="deactivateForm.password"
              type="password"
              show-password
              placeholder="验证身份后注销"
            />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="deactivateVisible = false">取消</el-button>
          <el-button type="danger" :loading="deactivating" @click="submitDeactivate">
            确认注销
          </el-button>
        </template>
      </el-dialog>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout {
  height: 100%;
}

.layout__aside {
  display: flex;
  flex-direction: column;
  background-color: var(--el-bg-color);
  border-right: 1px solid var(--el-border-color-light);
}

.layout__brand {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 60px;
  padding: 0 20px;
  border-bottom: 1px solid var(--el-border-color-light);
}

.layout__brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background-color: var(--el-color-primary);
  color: var(--el-bg-color);
  font-size: 15px;
  font-weight: 600;
}

.layout__brand-text {
  font-size: 15px;
  font-weight: 600;
}

.layout__menu {
  flex: 1;
  border-right: none;
}

.layout__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 60px;
  background-color: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-light);
}

.layout__title {
  font-size: 16px;
  font-weight: 600;
}

.layout__user {
  display: flex;
  align-items: center;
  gap: 12px;
}

.layout__account {
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.layout__account--btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  color: var(--el-text-color-primary);
  outline: none;
}

.layout__account--btn:hover {
  color: var(--el-color-primary);
}

.layout__deactivate-alert {
  margin-bottom: 16px;
}

.layout__deactivate-form {
  margin-top: 4px;
}

.layout__main {
  padding: 20px;
  background-color: var(--el-fill-color-light);
}

.layout__banner {
  border-radius: 0;
}
</style>
