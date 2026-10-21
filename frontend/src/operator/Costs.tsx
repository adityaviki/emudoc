// The `costs` command's report: every organisation that used anything, the costliest first,
// against its plan's daily limit; theirs together against DAILY_SPEND_CAP; and the cost by kind
// of work and model.

import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'
import { PLAN_LABELS } from '../api.ts'
import { Loading, Notice, Section, Segmented } from '../components/ui.tsx'
import { formatDate } from '../format.ts'
import { operatorApi } from './api.ts'
import type { Cost } from './api.ts'
import { Cell, Heading, Table } from './Table.tsx'
import { count, known, tokensOf, topDay } from './words.ts'

const SPANS = ['7', '30', '90'] as const

function CostCells({ cost, currency }: { cost: Cost; currency: string }) {
  return (
    <>
      <Cell figure>{known(cost.cost_micros, tokensOf(cost), currency)}</Cell>
      <Cell figure>{topDay(cost, currency)}</Cell>
      <Cell figure>{cost.days_at_limit ?? '—'}</Cell>
      <Cell figure>{cost.answers.toLocaleString('en')}</Cell>
      <Cell figure>{cost.calls.toLocaleString('en')}</Cell>
      <Cell figure>{cost.pages.toLocaleString('en')}</Cell>
    </>
  )
}

export function CostsPage() {
  const [search, setSearch] = useSearchParams()
  const days = SPANS.find((span) => span === search.get('days')) ?? '30'
  const costs = useQuery({ queryKey: ['operator', 'costs', days], queryFn: () => operatorApi.costs(Number(days)) })
  return (
    <>
      <Heading title="Costs" command={`costs --days ${days}`}>
        What each organisation's model use cost the platform, the costliest first, against its plan's daily limit, in UTC days.
      </Heading>
      <div className="mb-4 flex">
        <Segmented
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
          <p className="text-[13px] text-muted">
            From {formatDate(costs.data.since)} to {formatDate(costs.data.until)}.
            {costs.data.idle > 0 && ` ${count(costs.data.idle, 'other organisation')} used nothing.`}
          </p>
          <Table
            columns={[
              { label: 'Organisation' },
              { label: 'Plan' },
              { label: 'Cost', figure: true },
              { label: 'Costliest day', figure: true },
              { label: 'Days at limit', figure: true },
              { label: 'Answers', figure: true },
              { label: 'Calls', figure: true },
              { label: 'Pages', figure: true },
            ]}
          >
            {costs.data.organizations.map((cost) => (
              <tr key={cost.org_id}>
                <Cell>
                  <Link to={`/operator/organizations/${cost.org_id}`} className="font-medium text-accent hover:underline">
                    {cost.name}
                  </Link>
                  <span className="ml-2 text-muted">{cost.slug}</span>
                </Cell>
                <Cell>{PLAN_LABELS[cost.plan]}</Cell>
                <CostCells cost={cost} currency={costs.data.currency} />
              </tr>
            ))}
            <tr className="bg-sunken/50 font-medium">
              <Cell>The platform</Cell>
              <Cell className="text-muted">DAILY_SPEND_CAP</Cell>
              <CostCells cost={costs.data.platform} currency={costs.data.currency} />
            </tr>
          </Table>
          {costs.data.models.length > 0 && (
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
          )}
          {costs.data.organizations.length === 0 && <Notice tone="info">No organisation used anything in these days.</Notice>}
          <p className="text-xs text-muted">A plan without a daily limit has no days at it, shown as —.</p>
        </div>
      )}
    </>
  )
}
