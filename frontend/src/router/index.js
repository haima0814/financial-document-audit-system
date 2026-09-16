import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

import Login from '@/views/Login.vue'
import Layout from '@/views/Layout.vue'
import Dashboard from '@/views/Dashboard.vue'
import DocumentList from '@/views/DocumentList.vue'
import DocumentCreate from '@/views/DocumentCreate.vue'
import DocumentDetail from '@/views/DocumentDetail.vue'
import AuditReportView from '@/views/AuditReportView.vue'
import ApprovalCenter from '@/views/ApprovalCenter.vue'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: Login
  },
  {
    path: '/',
    component: Layout,
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: Dashboard
      },
      {
        path: 'documents',
        name: 'DocumentList',
        component: DocumentList
      },
      {
        path: 'documents/create',
        name: 'DocumentCreate',
        component: DocumentCreate
      },
      {
        path: 'documents/:id',
        name: 'DocumentDetail',
        component: DocumentDetail
      },
      {
        path: 'audits',
        name: 'AuditReportDefault',
        redirect: () => {
          const lastId = localStorage.getItem('last_viewed_audit_id') || '2'
          return `/audits/${lastId}`
        }
      },
      {
        path: 'audits/:documentId',
        name: 'AuditReportView',
        component: AuditReportView
      },
      {
        path: 'approvals',
        name: 'ApprovalCenter',
        component: ApprovalCenter
      }
    ]
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// 路由守卫拦截
router.beforeEach((to, from, next) => {
  const token = localStorage.getItem('token')
  if (to.name !== 'Login' && !token) {
    next({ name: 'Login' })
  } else if (to.name === 'Login' && token) {
    next({ path: '/documents' })
  } else {
    next()
  }
})

export default router
