import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// GitHub Pages hosts the site at https://nmpower69.github.io/garmin-ai/
// so the build base must be /garmin-ai/.
// In dev, /garmin/* is proxied to the repo-root static server (python -m http.server 8742)
// so garmin/data.json is fetchable without copying files.
// Regex key so /garmin-ai/ (the app) is NOT caught by the /garmin/ data proxy.
const pageProxy = { '^/garmin/': 'http://localhost:8742' }

export default defineConfig({
  base: '/garmin-ai/',
  plugins: [react()],
  server: { proxy: pageProxy },
  preview: { proxy: pageProxy },
})
