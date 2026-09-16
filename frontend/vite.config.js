import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  },
  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true
      },
      '/ws': {
        target: 'ws://127.0.0.1:8001',
        ws: true
      },
      '/uploads': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true
      }
    }
  }
})
