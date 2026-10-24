import { Check, Copy, Download } from 'lucide-react'
import { useEffect, useState } from 'react'
import { ThemeToggle } from '../components/ThemeToggle.tsx'
import { Button, Loading, Notice } from '../components/ui.tsx'
import { CONTRACT, baseOf, curlOf, events, fieldsOf, groups, parameterField, refName, resolved, schemas, shared, spans, typeOf } from './contract.ts'
import type { Answer, Field, OpenApi, Operation, Schema, Words } from './contract.ts'

const UNLOADED = "The API's contract could not be loaded. Reload the page to try again."
const IN = { path: 'Path parameters', query: 'Query parameters', header: 'Headers' }
// A method's colour among the semantic tokens, each readable on the sunken background.
const METHOD_TONES: Record<string, string> = {
  GET: 'text-good',
  POST: 'text-accent',
  PUT: 'text-warn',
  PATCH: 'text-warn',
  DELETE: 'text-bad',
}

/**
 * The versioned API's reference at /api-reference, a page of its own (reference.html), which
 * anyone may read: drawn from the contract the API serves (/api/v1/openapi.json, the published
 * file with this deployment's address), so it says what the deployment answers. The routes by
 * group, the webhooks' events and the schemas, each with an anchor, and each route with a request
 * to try at this deployment's address.
 */
export function Reference() {
  const [contract, setContract] = useState<OpenApi | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let current = true
    fetch(CONTRACT)
      .then((response) => {
        if (!response.ok) throw new Error(UNLOADED)
        return response.json() as Promise<OpenApi>
      })
      .then(
        (loaded) => current && setContract(loaded),
        () => current && setFailed(true),
      )
    return () => {
      current = false
    }
  }, [])

  // A link to an operation or a schema opens the page there, once it is drawn.
  useEffect(() => {
    if (!contract || !window.location.hash) return
    document.getElementById(decodeURIComponent(window.location.hash.slice(1)))?.scrollIntoView()
  }, [contract])

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-2.5">
          <a href="/" className="flex items-center gap-2 rounded-md font-semibold tracking-tight">
            <img src="/favicon.svg" alt="" className="size-6" />
            Emudoc
          </a>
          <span className="text-[13px] text-muted">API reference</span>
          <div className="ml-auto flex items-center gap-2">
            <a href="/help/organisations" className="text-[13px] text-muted hover:text-ink hover:underline">
              Help with API keys
            </a>
            <ThemeToggle />
          </div>
        </div>
      </header>
      {contract ? (
        <Contract contract={contract} />
      ) : (
        <main className="mx-auto w-full max-w-6xl px-4 py-6">
          <h1 className="sr-only">API reference</h1>
          {failed ? <Notice tone="bad">{UNLOADED}</Notice> : <Loading />}
        </main>
      )}
    </div>
  )
}

function Contract({ contract }: { contract: OpenApi }) {
  const listed = groups(contract)
  const posted = events(contract)
  const hooks = contract.tags?.find((tag) => tag.name === 'webhooks')
  const base = baseOf(contract, window.location.origin)
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-8 px-4 py-6 lg:flex-row">
      <nav aria-label="Contents" className="shrink-0 text-[13px] lg:sticky lg:top-0 lg:max-h-dvh lg:w-56 lg:self-start lg:overflow-y-auto lg:py-2">
        <ul className="space-y-2">
          <li>
            <a href="#introduction" className="inline-block py-1 font-medium hover:underline">
              Introduction
            </a>
          </li>
          {listed.map((group) => (
            <li key={group.name}>
              <a href={`#group-${group.name}`} className="inline-block py-1 font-medium hover:underline">
                {group.title}
              </a>
              <ul className="mt-0.5 hidden border-l border-line pl-3 lg:block">
                {group.operations.map((operation) => (
                  <li key={operation.id}>
                    <a href={`#${operation.id}`} className="block py-1 text-muted hover:text-ink hover:underline">
                      {operation.summary}
                    </a>
                  </li>
                ))}
              </ul>
            </li>
          ))}
          {posted.length > 0 && (
            <li>
              <a href="#webhooks" className="inline-block py-1 font-medium hover:underline">
                {hooks?.['x-displayName'] ?? 'Webhooks'}
              </a>
              <ul className="mt-0.5 hidden border-l border-line pl-3 lg:block">
                {posted.map((event) => (
                  <li key={event.id}>
                    <a href={`#${event.id}`} className="block py-1 text-muted hover:text-ink hover:underline">
                      <code className="font-mono text-xs">{event.path}</code>
                    </a>
                  </li>
                ))}
              </ul>
            </li>
          )}
          <li>
            <a href="#schemas" className="inline-block py-1 font-medium hover:underline">
              Schemas
            </a>
          </li>
        </ul>
      </nav>
      <main className="min-w-0 flex-1 space-y-12">
        <Introduction contract={contract} base={base} />
        {listed.map((group) => (
          <section key={group.name} aria-labelledby={`group-${group.name}`} className="space-y-4">
            <div>
              <h2 id={`group-${group.name}`} className="scroll-mt-4 text-lg font-semibold tracking-tight">
                {group.title}
              </h2>
              {group.description && (
                <p className="mt-1 text-[13px] leading-relaxed text-muted">
                  <Text text={group.description} />
                </p>
              )}
            </div>
            {group.operations.map((operation) => (
              <OperationView key={operation.id} contract={contract} operation={operation} base={base} />
            ))}
          </section>
        ))}
        {posted.length > 0 && (
          <section aria-labelledby="webhooks" className="space-y-4">
            <div>
              <h2 id="webhooks" className="scroll-mt-4 text-lg font-semibold tracking-tight">
                {hooks?.['x-displayName'] ?? 'Webhooks'}
              </h2>
              {hooks?.description && (
                <p className="mt-1 text-[13px] leading-relaxed text-muted">
                  <Text text={hooks.description} />
                </p>
              )}
            </div>
            {posted.map((event) => (
              <EventView key={event.id} contract={contract} event={event} />
            ))}
          </section>
        )}
        <section aria-labelledby="schemas" className="space-y-4">
          <div>
            <h2 id="schemas" className="scroll-mt-4 text-lg font-semibold tracking-tight">
              Schemas
            </h2>
            <p className="mt-1 text-[13px] leading-relaxed text-muted">
              What the requests send and the answers and events hold. A field that is not required may be left out of a
              request; an answer holds it as null.
            </p>
          </div>
          {schemas(contract).map(([name, schema]) => (
            <SchemaView key={name} contract={contract} name={name} schema={schema} />
          ))}
        </section>
      </main>
    </div>
  )
}

function Introduction({ contract, base }: { contract: OpenApi; base: string }) {
  const keys = Object.values(contract.components?.securitySchemes ?? {})
  const refusals = shared(contract)
  return (
    <section aria-labelledby="introduction" className="space-y-4">
      <div>
        <h1 id="introduction" className="scroll-mt-4 text-xl font-semibold tracking-tight">
          {contract.info.title} <span className="font-normal text-muted">version {contract.info.version}</span>
        </h1>
        {contract.info.description && (
          <p className="mt-2 max-w-3xl text-sm leading-relaxed">
            <Text text={contract.info.description} />
          </p>
        )}
      </div>
      <dl className="grid max-w-3xl gap-x-6 gap-y-2 rounded-xl border border-line bg-surface p-5 text-[13px] shadow-xs sm:grid-cols-[auto_1fr]">
        <dt className="text-muted">Address</dt>
        <dd>
          <code className="font-mono text-xs [overflow-wrap:anywhere]">{base}</code>
        </dd>
        <dt className="text-muted">Keys</dt>
        <dd className="leading-relaxed">
          {keys.map((scheme, index) => (
            <span key={index} className="block">
              <Text text={scheme.description ?? ''} />
            </span>
          ))}
        </dd>
        <dt className="text-muted">Contract</dt>
        <dd className="flex flex-wrap items-center gap-x-3 gap-y-1 leading-relaxed">
          <span>OpenAPI {contract.openapi}, which tools that write a client for a program read.</span>
          <a href={CONTRACT} download className="inline-flex items-center gap-1 font-medium text-accent hover:underline">
            <Download className="size-3.5" />
            openapi.json
          </a>
        </dd>
      </dl>
      {refusals.length > 0 && (
        <div className="max-w-3xl">
          <h2 className="font-semibold">Refusals every request may meet</h2>
          <Answers contract={contract} answers={refusals} />
        </div>
      )}
    </section>
  )
}

function OperationView({ contract, operation, base }: { contract: OpenApi; operation: Operation; base: string }) {
  return (
    <article id={operation.id} aria-labelledby={`${operation.id}-title`} className="scroll-mt-4 space-y-4 rounded-xl border border-line bg-surface p-5 shadow-xs">
      <div>
        <h3 id={`${operation.id}-title`} className="font-semibold">
          {operation.summary}
        </h3>
        <p className="mt-1 flex flex-wrap items-baseline gap-2">
          <Method method={operation.method} />
          <code className="font-mono text-[13px] [overflow-wrap:anywhere]">{operation.path}</code>
        </p>
        {operation.description && (
          <p className="mt-2 text-[13px] leading-relaxed">
            <Text text={operation.description} />
          </p>
        )}
      </div>
      <Parameters operation={operation} />
      {operation.body && <BodyView contract={contract} title={`Body, as ${operation.body.media}`} schema={operation.body.schema} />}
      <div>
        <h4 className="text-[13px] font-semibold">Answers</h4>
        <Answers contract={contract} answers={operation.answers} />
      </div>
      <Example text={curlOf(contract, operation, base)} />
    </article>
  )
}

function EventView({ contract, event }: { contract: OpenApi; event: Operation }) {
  return (
    <article id={event.id} aria-labelledby={`${event.id}-title`} className="scroll-mt-4 space-y-4 rounded-xl border border-line bg-surface p-5 shadow-xs">
      <div>
        <h3 id={`${event.id}-title`} className="font-semibold">
          {event.summary}
        </h3>
        <p className="mt-1 flex flex-wrap items-baseline gap-2">
          <Method method={event.method} />
          <span className="text-[13px] text-muted">to the webhook's address, as</span>
          <code className="font-mono text-[13px]">{event.path}</code>
        </p>
        {event.description && (
          <p className="mt-2 text-[13px] leading-relaxed">
            <Text text={event.description} />
          </p>
        )}
      </div>
      <Parameters operation={event} />
      {event.body && <BodyView contract={contract} title={`Body, as ${event.body.media}`} schema={event.body.schema} />}
      <div>
        <h4 className="text-[13px] font-semibold">What the program answers</h4>
        <Answers contract={contract} answers={event.answers} />
      </div>
    </article>
  )
}

function Parameters({ operation }: { operation: Operation }) {
  return (Object.keys(IN) as (keyof typeof IN)[]).map((where) => {
    const given = operation.parameters.filter((parameter) => parameter.in === where)
    if (given.length === 0) return null
    return (
      <div key={where}>
        <h4 className="text-[13px] font-semibold">{IN[where]}</h4>
        <Fields fields={given.map(parameterField)} />
      </div>
    )
  })
}

/** A body's schema: its fields, under the schema's name where it has one. */
function BodyView({ contract, title, schema }: { contract: OpenApi; title: string; schema: Schema }) {
  const name = refName(schema)
  return (
    <div>
      <h4 className="text-[13px] font-semibold">
        {title}
        {name && (
          <>
            {': '}
            <SchemaLink name={name} />
          </>
        )}
      </h4>
      <Fields fields={fieldsOf(contract, schema)} />
    </div>
  )
}

function Answers({ contract, answers }: { contract: OpenApi; answers: Answer[] }) {
  return (
    <ul className="mt-1.5 divide-y divide-line rounded-lg border border-line text-[13px]">
      {answers.map((answer) => (
        <li key={answer.status} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 px-3 py-2">
          <code className={`font-mono font-semibold ${answer.status.startsWith('2') ? 'text-good' : 'text-ink'}`}>{answer.status}</code>
          <span className="min-w-0 flex-1 leading-relaxed">
            <Text text={answer.description} />
          </span>
          {(answer.schema || answer.media) && <Type words={answerWords(contract, answer)} />}
        </li>
      ))}
    </ul>
  )
}

/** An answer's body in words: its schema, or its media type when it names no schema, as a file's or a stream's. */
function answerWords(contract: OpenApi, answer: Answer): Words {
  if (answer.schema && Object.keys(resolved(contract, answer.schema)).length) return typeOf(answer.schema)
  return [answer.media ?? 'any']
}

function SchemaView({ contract, name, schema }: { contract: OpenApi; name: string; schema: Schema }) {
  const fields = fieldsOf(contract, schema)
  return (
    <section id={`schema-${name}`} aria-labelledby={`schema-${name}-title`} className="scroll-mt-4 rounded-xl border border-line bg-surface p-5 shadow-xs">
      <h3 id={`schema-${name}-title`} className="font-mono text-sm font-semibold">
        {name}
      </h3>
      {schema.description && (
        <p className="mt-1 text-[13px] leading-relaxed">
          <Text text={schema.description} />
        </p>
      )}
      <div className="mt-3">
        {fields.length ? (
          <Fields fields={fields} />
        ) : (
          <p className="text-[13px] text-muted">
            <Type words={typeOf(schema)} />
          </p>
        )}
      </div>
    </section>
  )
}

function Fields({ fields }: { fields: Field[] }) {
  return (
    <ul className="mt-1.5 divide-y divide-line rounded-lg border border-line text-[13px]">
      {fields.map((field) => (
        <li key={field.name} className="px-3 py-2">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <code className="font-mono font-medium [overflow-wrap:anywhere]">{field.name}</code>
            <Type words={field.type} />
            {field.required && <span className="text-xs font-medium text-warn">required</span>}
          </div>
          {field.description && (
            <p className="mt-0.5 leading-relaxed text-muted">
              <Text text={field.description} />
            </p>
          )}
          {field.values.length > 0 && (
            <p className="mt-0.5 text-muted">
              One of{' '}
              {field.values.map((value, index) => (
                <span key={index}>
                  {index > 0 && ', '}
                  <code className="font-mono text-xs text-ink">{JSON.stringify(value)}</code>
                </span>
              ))}
            </p>
          )}
          {field.limits.length > 0 && <p className="mt-0.5 text-muted">{field.limits.join(', ')}</p>}
        </li>
      ))}
    </ul>
  )
}

function Type({ words }: { words: Words }) {
  return (
    <span className="font-mono text-xs text-muted">
      {words.map((word, index) => (typeof word === 'string' ? <span key={index}>{word}</span> : <SchemaLink key={index} name={word.schema} />))}
    </span>
  )
}

function SchemaLink({ name }: { name: string }) {
  return (
    <a href={`#schema-${name}`} className="font-mono text-accent hover:underline">
      {name}
    </a>
  )
}

function Method({ method }: { method: string }) {
  return <span className={`rounded bg-sunken px-1.5 py-0.5 font-mono text-[11px] font-semibold ${METHOD_TONES[method] ?? 'text-ink'}`}>{method}</span>
}

/** A text of the contract, its code set apart. */
function Text({ text }: { text: string }) {
  return spans(text).map((part, index) =>
    part.code ? (
      <code key={index} className="rounded border border-line bg-sunken px-1 font-mono text-[0.9em] text-ink">
        {part.text}
      </code>
    ) : (
      <span key={index}>{part.text}</span>
    ),
  )
}

/** A request to try, which wraps rather than scrolls, and copies whole. */
function Example({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
    } catch {
      // The clipboard can be unavailable; the request is on screen to copy by hand.
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-[13px] font-semibold">Example</h4>
        <Button variant="ghost" icon={copied ? <Check className="size-4" /> : <Copy className="size-4" />} onClick={copy}>
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
      <pre className="mt-1.5 rounded-lg border border-line bg-sunken px-3 py-2 font-mono text-xs leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere]">
        <code>{text}</code>
      </pre>
    </div>
  )
}
