import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In dev, /api is forwarded to the FastAPI server. In production set VITE_API_URL to the API's public URL.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: process.env.API_TARGET || 'http://localhost:8000', rewrite: (p) => p.replace(/^\/api/, '') } },
  },
})
