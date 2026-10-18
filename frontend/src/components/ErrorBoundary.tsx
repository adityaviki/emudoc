import { Component } from 'react'
import type { ReactNode } from 'react'
import { api } from '../api.ts'
import { pageErrorOf } from '../errors.ts'
import { Button } from './ui.tsx'

interface State {
  failed: boolean
  /** The id the app logged the report under, for the member to quote; null until it answers, or if it cannot. */
  reference: string | null
}

/**
 * Catches an error the page renders into anywhere below it, reports it to the app (src/errors.ts,
 * backend/app/tracking.py) and shows, in place of the broken page, what to do: reload, and quote
 * the reference if it happens again. A class, since only a class can catch what its children throw.
 */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { failed: false, reference: null }

  static getDerivedStateFromError(): Partial<State> {
    return { failed: true }
  }

  componentDidCatch(error: Error) {
    api
      .reportPageError(pageErrorOf(error, window.location.origin, window.location.pathname))
      .then(({ reference }) => this.setState({ reference }))
      // Unreported, the page still says to reload; it has no reference to give.
      .catch(() => this.setState({ reference: null }))
  }

  render() {
    if (!this.state.failed) return this.props.children
    const { reference } = this.state
    return (
      <div className="flex h-dvh items-center justify-center px-6">
        <div className="max-w-sm text-center">
          <p className="font-medium">This page ran into an error.</p>
          <p className="mt-1 text-[13px] text-muted">
            Reload it to go on.
            {reference && (
              <>
                {' '}
                If it happens again, give the reference <span className="font-mono wrap-break-word select-all">{reference}</span> when you
                report it.
              </>
            )}
          </p>
          <Button className="mt-4" onClick={() => window.location.reload()}>
            Reload
          </Button>
        </div>
      </div>
    )
  }
}
