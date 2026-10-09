import React from 'react';
import { createRoot } from 'react-dom/client';
import CollectorApp from './CollectorApp';
import '../styles.css';
import '../personal/personal.css';
import './mobile.css';

createRoot(document.getElementById('root')).render(<CollectorApp />);
