import { useEffect, useState } from 'react';

export default function App() {
  const [apiState, setApiState] = useState('checking');

  useEffect(() => {
    fetch('/api/health')
      .then((response) => response.ok ? response.json() : Promise.reject())
      .then(() => setApiState('online'))
      .catch(() => setApiState('offline'));
  }, []);

  return (
    <main className="app-shell" data-api-state={apiState}>
      <iframe
        className="prototype-frame"
        src="/prototype?v=phase1"
        title="eSway ERP 2.0"
        allow="fullscreen"
      />
      <span className="api-indicator" aria-label={`API ${apiState}`} />
    </main>
  );
}
