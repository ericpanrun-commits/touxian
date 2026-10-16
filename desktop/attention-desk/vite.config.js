import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

export default defineConfig(({ mode }) => {
  const root = fileURLToPath(new URL('.', import.meta.url));
  const env = { ...loadEnv(mode, root, ''), ...loadEnv(mode, fileURLToPath(new URL('./server', import.meta.url)), ''), ...process.env };
  if (env.VITE_CLOUD_ADMIN_TOKEN) throw new Error('Cloud admin secret must never use a VITE_ variable.');
  const proxy = { '/api': `http://127.0.0.1:${env.ATTENTION_API_PORT || 8765}` };
  if (env.ATTENTION_CLOUD_API_URL) {
    const target = new URL(env.ATTENTION_CLOUD_API_URL);
    if ((target.protocol !== 'https:' && !(target.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(target.hostname))) || target.username || target.password || target.search || target.hash || target.pathname !== '/') throw new Error('Cloud API URL must use an HTTPS origin or an explicit loopback tunnel.');
    if (!env.CLOUD_ADMIN_TOKEN || env.CLOUD_ADMIN_TOKEN.length < 32) throw new Error('The local cloud proxy requires a backend-only admin token.');
    for (const prefix of ['/api/personal/official', '/api/personal/notifications/bark', '/api/personal/system']) {
      proxy[prefix] = { target: target.href, changeOrigin: true, secure: true,
        configure(server) { server.on('proxyReq', request => request.setHeader('Authorization', 'Bearer ' + env.CLOUD_ADMIN_TOKEN)); } };
    }
  }
  // Specific worker routes must precede the legacy /api fallback.
  const orderedProxy = Object.fromEntries(Object.entries(proxy).sort(([a], [b]) => b.length - a.length));
  return {
  plugins: [react()],
  build: {
    outDir: 'dist',
    manifest: true,
    rollupOptions: { input: {
      desktop: fileURLToPath(new URL('./index.html', import.meta.url)),
      mobile: fileURLToPath(new URL('./mobile/index.html', import.meta.url)),
    } },
  },
  server: {
    host: env.ATTENTION_CLOUD_API_URL ? '127.0.0.1' : undefined,
    port: Number(env.ATTENTION_WEB_PORT || 5173),
    proxy: orderedProxy,
  },
  };
});
