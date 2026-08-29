import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const demoCredentials = JSON.stringify({
    email: env.DEMO_EMAIL ?? 'owner@demo.com',
    password: env.DEMO_PASSWORD ?? 'SecurePass12345',
  })

  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      proxy: {
        '/api/v1/auth/demo': {
          target: 'http://localhost:3000',
          changeOrigin: true,
          rewrite: () => '/api/v1/auth/login',
          configure(proxy) {
            proxy.on('proxyReq', (proxyRequest, request) => {
              if (request.method !== 'POST') return
              proxyRequest.setHeader('Content-Type', 'application/json')
              proxyRequest.setHeader('Content-Length', Buffer.byteLength(demoCredentials))
              proxyRequest.write(demoCredentials)
            })
          },
        },
        '/api': 'http://localhost:3000',
      },
    },
  }
})
