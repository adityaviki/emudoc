import { Component } from 'react'
import type { ReactNode } from 'react'
import { api } from '../api.ts'
import { pageErrorOf } from '../errors.ts'
import { failureWords } from '../failures.ts'
import type { Part } from '../failures.ts'
import { Button } from './ui.tsx'

type ResetKey = string | number | null

interface Props {
  children: ReactNode
  /** What breaks with the error, for the words; the page by default. */
  part?: Part
  /** True for the boundary around everything, whose words fill the window. */
  screen?: boolean
  /**
   * Where the part is (a path, a document's id): once it changes, the part renders afresh. A
   * prop, not a `key`, so that moving on remounts nothing that did not break.
   */
  resetKey?: ResetKey
}

interface State {
  /** The error the part rendered into, until it starts afresh. */
  error: Error | null
  /** The id the app logged the report under, for the member to quote; null until it answers, or if it cannot. */
  reference: string | null
  resetKey: ResetKey | undefined
}

/**
 * Catches an error rendered into anywhere below it, reports it to the app (src/errors.ts,
 * backend/app/tracking.py) and shows, in place of the broken part, what to do: try again or
 * reload, and quote the reference if it happens again. A class, since only a class can catch what
 * its children throw.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, reference: null, resetKey: this.props.resetKey }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error, reference: null }
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    if (props.resetKey === state.resetKey) return null
    return { error: null, reference: null, resetKey: props.resetKey }
  }

  componentDidCatch(error: Error) {
    // An answer that comes once the part started afresh, or broke again, is for nothing shown.
    const answered = (reference: string | null) => this.setState((state) => (state.error === error ? { reference } : null))
    api
      .reportPageError(pageErrorOf(error, window.location.origin, window.location.pathname))
      .then(({ reference }) => answered(reference))
      // Unreported, the part still says what to do; it has no reference to give.
      .catch(() => answered(null))
  }

  render() {
    if (!this.state.error) return this.props.children
    const { reference } = this.state
    const words = failureWords(this.props.part ?? 'page')
    return (
      <div className={`flex items-center justify-center px-6 py-10 ${this.props.screen ? 'h-dvh' : 'h-full min-h-0 flex-1'}`}>
        <div role="alert" className="max-w-sm text-center">
          <p className="font-medium">{words.title}</p>
          <p className="mt-1 text-[13px] text-muted">
            {words.advice}
            {reference && (
              <>
                {' '}
                If it happens again, give the reference <span className="font-mono wrap-break-word select-all">{reference}</span> when you
                report it.
              </>
            )}
          </p>
          <div className="mt-4 flex justify-center gap-2">
            {words.retry && <Button onClick={() => this.setState({ error: null, reference: null })}>Try again</Button>}
            <Button onClick={() => window.location.reload()}>{words.retry ? 'Reload the page' : 'Reload'}</Button>
          </div>
        </div>
      </div>
    )
  }
}
