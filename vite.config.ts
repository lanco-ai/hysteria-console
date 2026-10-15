import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  root: 'frontend',
  base: '/static/shop/',
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    manifest: 'manifest.json',
  },
});
