import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const apiProxy = {
  '/api': {
    target: 'http://127.0.0.1:8001',
    changeOrigin: true,
  },
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Proxy API calls to the backend so only this dev server's port needs
    // tunneling (e.g. ngrok) — the browser only ever talks to one origin.
    proxy: apiProxy,
    // Tunnel providers (ngrok, localtunnel, ...) present a Host header Vite
    // doesn't recognize by default and would otherwise reject.
    allowedHosts: true,
  },
  // `vite preview` (serves the production build) has its own separate
  // proxy/allowedHosts config — must mirror `server` above or the built
  // app can't reach the backend / gets rejected through a tunnel.
  preview: {
    proxy: apiProxy,
    allowedHosts: true,
  },
})
