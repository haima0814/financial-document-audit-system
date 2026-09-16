<template>
  <div class="login-page">
    <div class="login-card">
      <div class="brand-section">
        <div class="brand-logo">🛡️</div>
        <h2>财务单据智能风险审核系统</h2>
        <p class="brand-desc">多智能体协同 · 确定性规则核算 · 证据链可解释风控</p>
      </div>

      <el-form :model="form" class="login-form" @submit.prevent="handleLogin">
        <el-form-item label="登录账号">
          <el-input v-model="form.username" placeholder="请输入用户名" prefix-icon="User" />
        </el-form-item>
        <el-form-item label="登录密码">
          <el-input v-model="form.password" type="password" placeholder="请输入密码" prefix-icon="Lock" show-password />
        </el-form-item>

        <el-button type="primary" class="submit-btn" :loading="loading" @click="handleLogin">
          登 录 系 统
        </el-button>
      </el-form>

      <!-- 快速身份切换面板 (演示面试利器) -->
      <div class="quick-roles-panel">
        <div class="quick-title">⚡ 面试演示角色一键切换：</div>
        <div class="roles-grid">
          <el-button size="small" @click="quickLogin('emp', '123456')">经办人 (小赵)</el-button>
          <el-button size="small" @click="quickLogin('manager', '123456')">主管初审 (张经理)</el-button>
          <el-button size="small" @click="quickLogin('finance', '123456')">财务复核 (李财务)</el-button>
          <el-button size="small" @click="quickLogin('cfo', '123456')">总监特批 (王CFO)</el-button>
          <el-button size="small" @click="quickLogin('admin', '123456')">系统管理员</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { ElMessage } from 'element-plus'

const router = useRouter()
const authStore = useAuthStore()

const form = reactive({
  username: 'emp',
  password: 'pwd'
})
const loading = ref(false)

const handleLogin = async () => {
  if (!form.username || !form.password) {
    ElMessage.warning('请填写用户名与密码')
    return
  }
  loading.value = true
  try {
    await authStore.login(form.username, form.password)
    ElMessage.success(`欢迎回来，${authStore.user.real_name}！`)
    router.push('/documents')
  } catch (err) {
    // 错误在 interceptor 中处理
  } finally {
    loading.value = false
  }
}

const quickLogin = (user, pwd) => {
  form.username = user
  form.password = pwd
  handleLogin()
}
</script>

<style scoped>
.login-page {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0f172a 100%);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}

.login-card {
  width: 440px;
  padding: 40px;
  background: #ffffff;
  border-radius: 12px;
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.2), 0 10px 10px -5px rgba(0, 0, 0, 0.1);
}

.brand-section {
  text-align: center;
  margin-bottom: 28px;
}

.brand-logo {
  font-size: 44px;
  margin-bottom: 8px;
}

.brand-section h2 {
  font-size: 20px;
  font-weight: 700;
  color: #0f172a;
  margin: 0 0 6px 0;
}

.brand-desc {
  font-size: 12px;
  color: #64748b;
  margin: 0;
}

.login-form {
  margin-bottom: 24px;
}

.submit-btn {
  width: 100%;
  padding: 12px 0;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 2px;
  margin-top: 10px;
}

.quick-roles-panel {
  padding-top: 18px;
  border-top: 1px dashed #e2e8f0;
}

.quick-title {
  font-size: 12px;
  color: #64748b;
  margin-bottom: 10px;
  font-weight: 600;
}

.roles-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.roles-grid .el-button {
  margin: 0;
  font-size: 11px;
}
</style>
