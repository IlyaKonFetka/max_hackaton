import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// База — путь репозитория на GitHub Pages: https://ilyakonfetka.github.io/max_hackaton/
// Для сборки внутрь бэкенда (раздаётся с /app/) задаётся VITE_BASE=/app/.
export default defineConfig({
  plugins: [react()],
  base: process.env.VITE_BASE ?? '/max_hackaton/',
  build: { outDir: 'dist', sourcemap: false },
})
