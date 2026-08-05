import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  base: '/app/',
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5174,
    proxy: {
      '/api': { target: 'https://howlscan.org', changeOrigin: true },
      '/classic': { target: 'https://howlscan.org', changeOrigin: true },
      '/assets': { target: 'https://howlscan.org', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
})
