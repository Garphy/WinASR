import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5273,
    proxy: {
      '/api': {
        target: 'http://localhost:8200',
        changeOrigin: true,
      },
    },
  },
})
