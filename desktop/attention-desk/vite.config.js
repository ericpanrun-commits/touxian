import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

export default defineConfig({
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
    port: Number(process.env.ATTENTION_WEB_PORT || 5173),
    proxy: {
      '/api': `http://127.0.0.1:${process.env.ATTENTION_API_PORT || 8765}`,
    },
  },
});
