<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'

import { ApiError } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const formRef = ref<FormInstance>()
const submitting = ref(false)
const errorText = ref('')

const form = reactive({
  phone: '',
  password: '',
})

const rules: FormRules<typeof form> = {
  phone: [
    { required: true, message: '请输入手机号', trigger: 'blur' },
    { pattern: /^1\d{10}$/, message: '请输入 11 位手机号', trigger: 'blur' },
  ],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

/** 只接受站内相对路径，防止 ?redirect=//evil.com 这类开放重定向 */
function resolveRedirect(): string {
  const raw = route.query.redirect
  const target = typeof raw === 'string' ? raw : ''
  if (target.startsWith('/') && !target.startsWith('//')) {
    return target
  }
  return '/dashboard'
}

async function handleSubmit(): Promise<void> {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  errorText.value = ''

  try {
    await auth.signIn({ phone: form.phone, password: form.password })
    ElMessage.success('登录成功')
    await router.replace(resolveRedirect())
  } catch (error) {
    // 401 AUTH_FAILED 等错误：内联展示后端 message，不额外弹层
    errorText.value = error instanceof ApiError ? error.message : '登录失败，请稍后重试'
    form.password = ''
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login">
    <div class="login__card">
      <div class="login__head">
        <div class="login__mark">托</div>
        <h1 class="login__title">托管机构管理系统</h1>
        <p class="login__subtitle">员工登录 · 教务 / 机构管理员 / 老师</p>
      </div>

      <el-alert
        v-if="errorText"
        :title="errorText"
        type="error"
        show-icon
        :closable="false"
        class="login__error"
      />

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        size="large"
        @submit.prevent="handleSubmit"
      >
        <el-form-item label="手机号" prop="phone">
          <el-input
            v-model="form.phone"
            placeholder="请输入手机号"
            maxlength="11"
            autocomplete="username"
          />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="请输入密码"
            show-password
            autocomplete="current-password"
            @keyup.enter="handleSubmit"
          />
        </el-form-item>
        <el-button
          type="primary"
          class="login__submit"
          size="large"
          :loading="submitting"
          @click="handleSubmit"
        >
          登录
        </el-button>
      </el-form>
    </div>
  </div>
</template>

<style scoped>
.login {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100%;
  padding: 24px;
  background: linear-gradient(135deg, #eef4ff 0%, #f7f9fc 55%, #eaf1fb 100%);
}

.login__card {
  width: 100%;
  max-width: 400px;
  padding: 36px 32px 32px;
  background-color: #ffffff;
  border-radius: 14px;
  box-shadow: 0 18px 48px rgba(31, 45, 61, 0.1);
}

.login__head {
  margin-bottom: 24px;
  text-align: center;
}

.login__mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  margin-bottom: 12px;
  border-radius: 12px;
  background-color: #409eff;
  color: #ffffff;
  font-size: 20px;
  font-weight: 600;
}

.login__title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}

.login__subtitle {
  margin: 8px 0 0;
  font-size: 13px;
  color: #909399;
}

.login__error {
  margin-bottom: 16px;
}

.login__submit {
  width: 100%;
  margin-top: 4px;
}
</style>
