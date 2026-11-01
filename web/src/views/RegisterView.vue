<script setup lang="ts">
import { reactive, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'

import { ApiError } from '@/api/http'
import { register } from '@/api/auth'

const router = useRouter()

const formRef = ref<FormInstance>()
const submitting = ref(false)
const errorText = ref('')

const form = reactive({
  org_name: '',
  admin_name: '',
  admin_phone: '',
  admin_password: '',
  confirm_password: '',
})

function validatePassword(_rule: unknown, value: string, callback: (error?: Error) => void): void {
  if (!value) return callback()
  if (value.length < 8) return callback(new Error('密码至少 8 位'))
  if (!/[a-zA-Z]/.test(value) || !/\d/.test(value)) {
    return callback(new Error('密码需同时包含字母和数字'))
  }
  callback()
}

function validateConfirm(_rule: unknown, value: string, callback: (error?: Error) => void): void {
  if (!value) return callback()
  if (value !== form.admin_password) return callback(new Error('两次输入的密码不一致'))
  callback()
}

const rules: FormRules<typeof form> = {
  org_name: [
    { required: true, message: '请输入机构名称', trigger: 'blur' },
    { max: 128, message: '机构名称过长', trigger: 'blur' },
  ],
  admin_name: [{ required: true, message: '请输入管理员姓名', trigger: 'blur' }],
  admin_phone: [
    { required: true, message: '请输入手机号', trigger: 'blur' },
    { pattern: /^1\d{10}$/, message: '请输入 11 位手机号', trigger: 'blur' },
  ],
  admin_password: [
    { required: true, message: '请设置密码', trigger: 'blur' },
    { validator: validatePassword, trigger: 'blur' },
  ],
  confirm_password: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    { validator: validateConfirm, trigger: 'blur' },
  ],
}

async function handleSubmit(): Promise<void> {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  errorText.value = ''

  try {
    await register({
      org_name: form.org_name.trim(),
      admin_name: form.admin_name.trim(),
      admin_phone: form.admin_phone,
      admin_password: form.admin_password,
    })
    await router.replace({ name: 'login', query: { registered: '1', phone: form.admin_phone } })
  } catch (error) {
    // 409 同名机构等错误内联展示，不裸 toast（沿用 030 原则）
    errorText.value = error instanceof ApiError ? error.message : '注册失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="register">
    <div class="register__card">
      <div class="register__head">
        <div class="register__mark">托</div>
        <h1 class="register__title">创建机构账号</h1>
        <p class="register__subtitle">免费试用 14 天 · 无需审核，注册即用</p>
      </div>

      <div class="register__badge">
        <span class="register__badge-item">✓ 14 天完整功能试用</span>
        <span class="register__badge-item">✓ 开通即建好管理员</span>
      </div>

      <el-alert
        v-if="errorText"
        :title="errorText"
        type="error"
        show-icon
        :closable="false"
        class="register__error"
      />

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        size="large"
        @submit.prevent="handleSubmit"
      >
        <el-form-item label="机构名称" prop="org_name">
          <el-input v-model="form.org_name" placeholder="例如：阳光托管中心" maxlength="128" />
        </el-form-item>
        <el-form-item label="管理员姓名" prop="admin_name">
          <el-input v-model="form.admin_name" placeholder="您的姓名" maxlength="64" />
        </el-form-item>
        <el-form-item label="手机号" prop="admin_phone">
          <el-input
            v-model="form.admin_phone"
            placeholder="用于登录"
            maxlength="11"
            autocomplete="username"
          />
        </el-form-item>
        <el-form-item label="设置密码" prop="admin_password">
          <el-input
            v-model="form.admin_password"
            type="password"
            placeholder="至少 8 位，含字母和数字"
            show-password
            autocomplete="new-password"
          />
        </el-form-item>
        <el-form-item label="确认密码" prop="confirm_password">
          <el-input
            v-model="form.confirm_password"
            type="password"
            placeholder="再次输入密码"
            show-password
            autocomplete="new-password"
            @keyup.enter="handleSubmit"
          />
        </el-form-item>
        <el-button
          type="primary"
          class="register__submit"
          size="large"
          :loading="submitting"
          @click="handleSubmit"
        >
          注册并开始试用
        </el-button>
      </el-form>

      <div class="register__foot">
        已有账号？
        <RouterLink class="register__link" :to="{ name: 'login' }">直接登录</RouterLink>
      </div>
    </div>
  </div>
</template>

<style scoped>
.register {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100%;
  padding: 24px;
  background:
    radial-gradient(1200px 600px at 15% -10%, #dbe9ff 0%, rgba(219, 233, 255, 0) 60%),
    radial-gradient(900px 500px at 110% 110%, #e4ecff 0%, rgba(228, 236, 255, 0) 55%),
    linear-gradient(135deg, #eef4ff 0%, #f7f9fc 55%, #eaf1fb 100%);
}

.register__card {
  position: relative;
  width: 100%;
  max-width: 440px;
  padding: 40px 36px 28px;
  background-color: #ffffff;
  border-radius: 16px;
  box-shadow: 0 22px 56px rgba(31, 45, 61, 0.12);
  overflow: hidden;
}

.register__card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 4px;
  background: linear-gradient(90deg, #409eff 0%, #79bbff 55%, #a0cfff 100%);
}

.register__head {
  margin-bottom: 16px;
  text-align: center;
}

.register__mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 46px;
  height: 46px;
  margin-bottom: 14px;
  border-radius: 13px;
  background: linear-gradient(135deg, #409eff 0%, #66b1ff 100%);
  color: #ffffff;
  font-size: 21px;
  font-weight: 600;
  box-shadow: 0 6px 16px rgba(64, 158, 255, 0.3);
}

.register__title {
  margin: 0;
  font-size: 22px;
  font-weight: 600;
}

.register__subtitle {
  margin: 8px 0 0;
  font-size: 13px;
  color: #909399;
}

.register__badge {
  display: flex;
  justify-content: center;
  gap: 16px;
  margin-bottom: 20px;
  padding: 10px 12px;
  border-radius: 10px;
  background-color: #f5f9ff;
}

.register__badge-item {
  font-size: 12px;
  color: #4a79b8;
}

.register__error {
  margin-bottom: 16px;
}

.register__submit {
  width: 100%;
  margin-top: 4px;
}

.register__foot {
  margin-top: 18px;
  text-align: center;
  font-size: 13px;
  color: #909399;
}

.register__link {
  color: #409eff;
  font-weight: 500;
}

.register__link:hover {
  text-decoration: underline;
}
</style>
