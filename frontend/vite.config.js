import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const rootDir = path.dirname(fileURLToPath(import.meta.url));
const prototypePath = path.resolve(rootDir, '..', 'esway-erp.html');

function prototypeRoute() {
  return {
    name: 'esway-prototype-route',
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

const devPort = Number(process.env.VITE_DEV_PORT || 5173);
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000';
const previewPort = Number(process.env.VITE_PREVIEW_PORT || 4173);

export default defineConfig({
  plugins: [react(), prototypeRoute()],
  server: {
    port: devPort,
    host: '127.0.0.1',
    proxy: {
      '/api': apiProxyTarget
    }
  },
  preview: { port: previewPort, host: '127.0.0.1' }
});
