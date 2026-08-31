import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { isRemovedDemoEndpoint, securityHeaders } from './security/headers.js'

export default defineConfig({
    plugins: [react(), tailwindcss(), {
      name: 'retire-demo-auth',
      configureServer(server) {
        server.middlewares.use((request, response, next) => {
          const pathname = new URL(request.url ?? '/', 'http://localhost').pathname
          if (!isRemovedDemoEndpoint(pathname)) return next()
          response.writeHead(404, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
          response.end(JSON.stringify({ message: 'Demo authentication is no longer available.' }))
        })
      },
    }],
    server: {
      port: 5173,
      proxy: {
        '/api': 'http://localhost:3000',
      },
    },
    preview: { headers: securityHeaders },
})
