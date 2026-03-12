import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // Ensure assets resolve correctly when loaded from Electron (file://)
  base: './',
})
