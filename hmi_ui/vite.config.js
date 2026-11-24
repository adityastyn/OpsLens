import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // Production build for older Chromium in HMI panels (EcoStruxure Operator Terminal Expert Web Browser).
  build: { target: ['chrome80', 'safari13.1'] },
  server: {
    port: 5173,
    host: true,
    // Panels on the plant network may open the HMI by the PC's name
    // (http://desktop-50b9sok:5173) instead of its IP; Vite 5.4.12+ blocks unknown
    // host names by default, which shows a blank 'Blocked request' page.
    allowedHosts: true,
    proxy: {
      '/api': {
        // 127.0.0.1, not localhost: Node 18+ may resolve localhost to ::1 while the backend listens on IPv4 only.
        target: 'http://127.0.0.1:8001',
        changeOrigin: true
      }
    }
  }
});
