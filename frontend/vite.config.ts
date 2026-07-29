import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发态把 /api 代理到 FastAPI，避免 CORS 干扰本地调试
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
