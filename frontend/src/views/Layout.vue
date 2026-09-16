<template>
  <el-container class="layout-container">
    <!-- 侧边导航栏 -->
    <el-aside width="240px" class="layout-aside">
      <div class="aside-brand">
        <span class="logo-emoji">🛡️</span>
        <span class="system-name">财务智能风控</span>
      </div>

      <el-menu
        :default-active="activeMenu"
        class="aside-menu"
        router
        background-color="#0f172a"
        text-color="#94a3b8"
        active-text-color="#ffffff"
      >
        <el-menu-item index="/dashboard">
          <el-icon><TrendCharts /></el-icon>
          <span>风控态势大盘</span>
        </el-menu-item>

        <el-menu-item index="/documents">
          <el-icon><Files /></el-icon>
          <span>单据工作台</span>
        </el-menu-item>

        <el-menu-item index="/documents/create">
          <el-icon><DocumentAdd /></el-icon>
          <span>新建财务单据</span>
        </el-menu-item>

        <el-menu-item index="/audits">
          <el-icon><DataAnalysis /></el-icon>
          <span>智能风控体检</span>
        </el-menu-item>

        <el-menu-item index="/approvals">
          <el-icon><Stamp /></el-icon>
          <span>审批待办中心</span>
        </el-menu-item>
      </el-menu>

      <div class="aside-footer">
        <div class="arch-tag">
          <el-icon><Cpu /></el-icon>
          <span>多智能体混合编排 V1.0</span>
        </div>
      </div>
    </el-aside>

    <!-- 主体区域 -->
    <el-container>
      <!-- 顶栏 Header -->
      <el-header class="layout-header">
        <div class="header-left">
          <span class="breadcrumb-text">{{ currentRouteTitle }}</span>
        </div>
        <div class="header-right">
          <el-tag :type="getRoleTagType" effect="dark" size="small" class="role-badge">
            {{ user?.roles?.[0] || 'EMPLOYEE' }}
          </el-tag>
          <span class="user-greeting">欢迎，<strong>{{ user?.real_name || '用户' }}</strong></span>
          <el-button size="small" type="danger" link @click="handleLogout">
            <el-icon><SwitchButton /></el-icon> 退出
          </el-button>
        </div>
      </el-header>

      <!-- 视图内容 -->
      <el-main class="layout-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { TrendCharts, Files, DocumentAdd, Stamp, Cpu, SwitchButton, DataAnalysis } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const user = computed(() => authStore.user)
const activeMenu = computed(() => {
  if (route.path.startsWith('/audits')) return '/audits'
  return route.path
})

const currentRouteTitle = computed(() => {
  if (route.path.startsWith('/dashboard')) return '企业财务风控态势驾驶舱 (AI 多智能体全景监控)'
  if (route.path.startsWith('/documents/create')) return '新建财务单据 (含算术硬平账校验)'
  if (route.path.startsWith('/audits')) return '综合风控体检报告与证据链锚点'
  if (route.path.startsWith('/documents/')) return '财务单据详情与发票视觉原件'
  if (route.path.startsWith('/approvals')) return '审批待办中心 (双轨状态机流转)'
  return '财务单据工作台'
})

const getRoleTagType = computed(() => {
  const r = user.value?.roles?.[0]
  if (r === 'ADMIN') return 'danger'
  if (r === 'CFO') return 'warning'
  if (r === 'FINANCE') return 'success'
  return 'primary'
})

const handleLogout = () => {
  authStore.logout()
  router.push('/login')
}
</script>

<style scoped>
.layout-container {
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  background: #f8fafc;
}

.layout-aside {
  background: #0f172a;
  display: flex;
  flex-direction: column;
  border-right: 1px solid #1e293b;
}

.aside-brand {
  height: 60px;
  display: flex;
  align-items: center;
  padding: 0 20px;
  gap: 10px;
  border-bottom: 1px solid #1e293b;
}

.logo-emoji {
  font-size: 24px;
}

.system-name {
  font-size: 16px;
  font-weight: 700;
  color: #ffffff;
  letter-spacing: 1px;
}

.aside-menu {
  flex: 1;
  border: none;
}

.aside-footer {
  padding: 16px;
  border-top: 1px solid #1e293b;
}

.arch-tag {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #64748b;
}

.layout-header {
  height: 60px;
  background: #ffffff;
  border-bottom: 1px solid #e2e8f0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 24px;
}

.breadcrumb-text {
  font-size: 15px;
  font-weight: 600;
  color: #1e293b;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 14px;
  font-size: 13px;
}

.role-badge {
  font-weight: 600;
}

.user-greeting {
  color: #475569;
}

.layout-main {
  padding: 20px;
  overflow-y: auto;
  overflow-x: auto;
  min-width: 0;
  background: #f1f5f9;
}
</style>
