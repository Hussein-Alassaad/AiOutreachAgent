import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  // Tailwind v4 runs as a Vite plugin — no tailwind.config.js or postcss.config.js
  // needed. Theme customisation lives in src/index.css via @theme.
  plugins: [react(), tailwindcss()],
  css: {
    // Empty inline PostCSS config. Without this, Vite walks UP the folder tree
    // looking for a postcss.config.js and finds an unrelated Tailwind v3 project
    // sitting on the Desktop, which breaks the build. Declaring it here stops the
    // search and keeps this app self-contained wherever the folder lives.
    postcss: {},
  },
  server: {
    // Exposes the dev server on the local network so the dashboard can be opened
    // on an iPhone during development, not just on the laptop.
    host: true,
    port: 5173,
  },
})
