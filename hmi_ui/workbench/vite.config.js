import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // Production build for older Chromium in HMI panels (EcoStruxure Operator Terminal Expert Web Browser).
  build: { target: ['chrome80', 'safari13.1'] },
  // Share the HMI's public files (the OpsLens tab icon).
  publicDir: '../public',
  server: {
    port: 5174,
    host: true,
    // Panels on the plant network may open the HMI by the PC's name
    // (http://desktop-50b9sok:5173) instead of its IP; Vite 5.4.12+ blocks unknown
    // host names by default, which shows a blank 'Blocked request' page.
    allowedHosts: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001', // IPv4: Node may resolve localhost to ::1
        changeOrigin: true,
      },
    },
  },
});
