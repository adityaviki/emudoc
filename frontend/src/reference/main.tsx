// The API reference's page (reference.html), a bundle of its own, which the app serves at
// /api-reference to anyone: nothing of it reaches a member's page, which would carry it on
// every visit for the few who write programs.

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '../index.css'
import { ErrorBoundary } from '../components/ErrorBoundary.tsx'
import { Reference } from './Reference.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary screen>
      <Reference />
    </ErrorBoundary>
  </StrictMode>,
)
