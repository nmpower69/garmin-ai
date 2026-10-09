import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// GitHub Pages hosts the site at https://nmpower69.github.io/garmin-ai/.
// The app is published under /garmin-ai/app/, so the production build needs
// that base. DEPLOY_BASE is set by .github/workflows/pages.yml; the default
// keeps `npm run dev` and `npm run preview` working locally.
// In dev, /garmin/* is proxied to the repo-root static server (python -m http.server 8742)
// so garmin/data.json is fetchable without copying files.
// Regex key so /garmin-ai/ (the app) is NOT caught by the /garmin/ data proxy.
const pageProxy = { '^/garmin/': 'http://localhost:8742' }

export default defineConfig({
  base: process.env.DEPLOY_BASE || '/garmin-ai/',
  plugins: [react()],
  server: { proxy: pageProxy },
  preview: { proxy: pageProxy },
})
