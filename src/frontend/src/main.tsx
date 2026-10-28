import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { Theme } from '@astryxdesign/core/theme';
import '@astryxdesign/core/reset.css';
import '@astryxdesign/core/astryx.css';
import { valuationTheme } from './themes/valuation-theme';
import App from './App';

const rootElement = document.getElementById('root');

if (!rootElement) {
  throw new Error('Missing root element.');
}

// Mount the app with the shared theme so every component uses the same tokens.
createRoot(rootElement).render(
  <StrictMode>
    <Theme theme={valuationTheme} mode="light">
      <App />
    </Theme>
  </StrictMode>,
);
