import './lib/polyfills.js'; // first: older panel browsers
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx';
import './index.css';
import { applyUiScale } from './lib/uiScale.js';

// Scale the 1280 × 800 layout up evenly on larger panels (1920 × 1200 → 1.5×).
applyUiScale();

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
