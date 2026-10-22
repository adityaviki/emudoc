// The operator's area's page (operator.html), a bundle of its own, which the app sends to an
// operator alone (backend/app/operators.py): nothing of it reaches a member's browser.

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import '../index.css'
import { ApiError } from '../api.ts'
import { ErrorBoundary } from '../components/ErrorBoundary.tsx'
import { OperatorApp } from './OperatorApp.tsx'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // One retry for a network or server failure; a refusal will not change.
      retry: (failures, error) => failures < 1 && !(error instanceof ApiError && error.status < 500),
      refetchOnWindowFocus: false,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary screen>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <OperatorApp />
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
)
