import { defineStore } from 'pinia'
import api from '@/api'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('token') || '',
    user: JSON.parse(localStorage.getItem('user') || 'null'),
  }),
  getters: {
    isAuthenticated: (state) => !!state.token,
    roles: (state) => state.user?.roles || [],
    isManager: (state) => (state.user?.roles || []).includes('MANAGER'),
    isFinance: (state) => (state.user?.roles || []).includes('FINANCE'),
    isAdmin: (state) => (state.user?.roles || []).includes('ADMIN'),
    isCFO: (state) => (state.user?.roles || []).includes('CFO'),
  },
  actions: {
    async login(username, password) {
      const res = await api.post('/auth/login', { username, password })
      this.token = res.access_token
      this.user = {
        id: res.user_id,
        username: res.username,
        real_name: res.real_name,
        roles: res.roles,
      }
      localStorage.setItem('token', this.token)
      localStorage.setItem('user', JSON.stringify(this.user))
      return this.user
    },
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem('token')
      localStorage.removeItem('user')
    }
  }
})
