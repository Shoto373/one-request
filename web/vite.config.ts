import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// dev: /api и /health → app на :8080. `npm run dev:mock` — без бэкенда (src/api/mock.ts).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8080',
      '/health': 'http://localhost:8080',
    },
  },
})
