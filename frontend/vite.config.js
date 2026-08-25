import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const rootDir = path.dirname(fileURLToPath(import.meta.url));
const prototypePath = path.resolve(rootDir, '..', 'eRoyal-ai-ERP.html');

function prototypeRoute() {
  return {
    name: 'eroyal-prototype-route',
    configureServer(server) {
      server.middlewares.use('/prototype', (_req, res) => {
        res.statusCode = 200;
        res.setHeader('Content-Type', 'text/html; charset=utf-8');
        res.setHeader('Cache-Control', 'no-store');
        fs.createReadStream(prototypePath).pipe(res);
      });
    },
    configurePreviewServer(server) {
      server.middlewares.use('/prototype', (_req, res) => {
        res.statusCode = 200;
        res.setHeader('Content-Type', 'text/html; charset=utf-8');
        res.setHeader('Cache-Control', 'no-store');
        fs.createReadStream(prototypePath).pipe(res);
      });
    }
  };
}

export default defineConfig({
  plugins: [react(), prototypeRoute()],
  server: {
    port: 5173,
    host: '127.0.0.1',
    proxy: {
      '/api': 'http://127.0.0.1:8000'
    }
  },
  preview: { port: 4173, host: '127.0.0.1' }
});
