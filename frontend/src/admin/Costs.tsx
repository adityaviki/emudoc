// The `costs` command's report for the workspace: what its model use cost over a span of days
// against DAILY_SPEND_CAP, and the cost by kind of work and model. The usage page shows the same
// cost day by day and month by month.

import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'
import { admin } from '../api.ts'
import { Loading, Notice, Row, Section, Segmented } from '../components/ui.tsx'
import { formatDate } from '../format.ts'
import { Cell, Heading, Table } from './Table.tsx'
import { known, tokensOf, topDay } from './words.ts'

const SPANS = ['7', '30', '90'] as const

export function CostsView() {
  const [search, setSearch] = useSearchParams()
  const days = SPANS.find((span) => span === search.get('days')) ?? '30'
  const costs = useQuery({ queryKey: ['admin', 'costs', days], queryFn: () => admin.costs(Number(days)) })
  return (
    <>
      <Heading title="Costs" command={`costs --days ${days}`}>
        What the workspace's model use cost, against DAILY_SPEND_CAP, in UTC days. The{' '}
        <Link to="/organization/usage" className="text-accent underline underline-offset-2">
          usage page
        </Link>{' '}
        shows it day by day and month by month.
      </Heading>
      <div className="mb-4 flex">
        <Segmented
          label="Span"
          value={days}
          onChange={(value) => setSearch({ days: value })}
          options={SPANS.map((span) => ({ value: span, label: `${span} days` }))}
        />
      </div>
      {costs.isPending ? (
        <Loading />
      ) : costs.isError ? (
        <Notice tone="bad">{costs.error.message}</Notice>
      ) : (
        <div className="space-y-4">
          <Section title={`From ${formatDate(costs.data.since)} to ${formatDate(costs.data.until)}`}>
            <dl className="divide-y divide-line">
              <Row label="Cost">{known(costs.data.cost.cost_micros, tokensOf(costs.data.cost), costs.data.currency)}</Row>
              <Row label="Costliest day">{topDay(costs.data.cost, costs.data.currency)}</Row>
              <Row label="Days at the cap">
                {costs.data.cost.days_at_limit ?? 'no cap: DAILY_SPEND_CAP is not set'}
              </Row>
              <Row label="Answers">{costs.data.cost.answers.toLocaleString('en')}</Row>
              <Row label="Model calls">{costs.data.cost.calls.toLocaleString('en')}</Row>
              <Row label="Pages indexed">{costs.data.cost.pages.toLocaleString('en')}</Row>
            </dl>
          </Section>
          {costs.data.models.length > 0 ? (
            <Section title="By kind of work and model" description="A model without a price in MODEL_PRICES counts its tokens, and no cost.">
              <Table
                columns={[
                  { label: 'Kind' },
                  { label: 'Model' },
                  { label: 'Cost', figure: true },
                  { label: 'Calls', figure: true },
                  { label: 'Tokens in', figure: true },
                  { label: 'Tokens out', figure: true },
                  { label: 'Unpriced tokens', figure: true },
                ]}
              >
                {costs.data.models.map((spent) => (
                  <tr key={`${spent.kind} ${spent.model}`}>
                    <Cell>{spent.kind}</Cell>
                    <Cell className="font-mono text-xs">{spent.model}</Cell>
                    <Cell figure>{known(spent.cost_micros, tokensOf(spent), costs.data.currency)}</Cell>
                    <Cell figure>{spent.calls.toLocaleString('en')}</Cell>
                    <Cell figure>{spent.input_tokens.toLocaleString('en')}</Cell>
                    <Cell figure>{spent.output_tokens.toLocaleString('en')}</Cell>
                    <Cell figure>{spent.unpriced_tokens ? spent.unpriced_tokens.toLocaleString('en') : '—'}</Cell>
                  </tr>
                ))}
              </Table>
            </Section>
          ) : (
            <Notice tone="info">No model was asked anything in these days.</Notice>
          )}
        </div>
      )}
    </>
  )
}
