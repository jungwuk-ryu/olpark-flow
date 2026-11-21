import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import App from './App';

const el = document.getElementById('root');
if (!el) throw new Error('#root 요소가 없습니다');

createRoot(el).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
