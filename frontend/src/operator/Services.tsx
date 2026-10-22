// How the models and services stand: the models the settings name, whether they can be used,
// and what the worker's watch sees wrong now, asked afresh (backend/app/watch.py), with since
// when it has been mailing each; and the kill switches, a model provider switched off in every
// process or on again without a restart (backend/app/switches.py).

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { Confirm } from '../components/Confirm.tsx'
import { Button, Chip, Loading, Notice, Row, Section } from '../components/ui.tsx'
import { formatDateTime } from '../format.ts'
import { operatorApi } from './api.ts'
import type { Provider } from './api.ts'
import { Cell, Heading, Table } from './Table.tsx'

export function ServicesPage() {
  const queryClient = useQueryClient()
  const services = useQuery({ queryKey: ['operator', 'services'], queryFn: operatorApi.services })
  const toggle = useMutation({
    mutationFn: ({ name, off }: { name: string; off: boolean }) => operatorApi.switchProvider(name, off),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['operator', 'services'] }),
  })
  // The provider to switch off once the dialog's question is agreed; switching one on asks nothing.
  const [asking, setAsking] = useState<Provider | null>(null)
  const flip = (provider: Provider) => (provider.off ? toggle.mutate({ name: provider.name, off: false }) : setAsking(provider))
  return (
    <>
      <Heading title="Services" command="database">
        The models, the embeddings endpoint, the scanner, the storage and the database, as the worker's watch sees them now.
      </Heading>
      {services.isPending ? (
        <Loading />
      ) : services.isError ? (
        <Notice tone="bad">{services.error.message}</Notice>
      ) : (
        <div className="space-y-4">
          <Button icon={<RefreshCw className="size-3.5" />} onClick={() => services.refetch()} disabled={services.isFetching}>
            Look again
          </Button>
          <Section title="What the watch sees" description="Each is mailed to ALERT_EMAIL when it begins and when it ends.">
            {services.data.concerns.length === 0 ? (
              <p className="text-[13px] text-good">Nothing is wrong.</p>
            ) : (
              <ul className="space-y-3">
                {services.data.concerns.map((concern) => (
                  <li key={concern.key} className="text-[13px]">
                    <p className="font-medium">
                      {concern.title[0].toUpperCase() + concern.title.slice(1)}{' '}
                      <span className="font-normal text-muted">
                        {concern.raised_at ? `since ${formatDateTime(concern.raised_at)}` : 'not mailed yet'}
                      </span>
                    </p>
                    <p className="mt-0.5 leading-relaxed text-muted">{concern.message}</p>
                  </li>
                ))}
              </ul>
            )}
            {services.data.unchecked.length > 0 && (
              <Notice>The watch could not look at {services.data.unchecked.join(', ')}: the server's log says why.</Notice>
            )}
          </Section>
          <Section
            title="Providers"
            description="A provider switched off here stops in every process within seconds, with no restart; one DISABLED_PROVIDERS names stays off until the setting changes and the app restarts."
          >
            {toggle.isError && (
              <div className="mb-3">
                <Notice tone="bad">{toggle.error.message}</Notice>
              </div>
            )}
            <Table columns={[{ label: 'Provider' }, { label: 'Its models' }, { label: 'State' }, { label: 'Switch', unseen: true }]}>
              {services.data.providers.map((provider) => (
                <tr key={provider.name}>
                  <Cell className="font-mono text-xs">{provider.name}</Cell>
                  <Cell className="font-mono text-xs">{provider.variables.join(', ') || 'none any more'}</Cell>
                  <Cell>{switchWords(provider)}</Cell>
                  <Cell>
                    {!provider.by_setting && (
                      <Button variant={provider.off ? 'secondary' : 'danger'} onClick={() => flip(provider)} disabled={toggle.isPending}>
                        {provider.off ? `Switch ${provider.name} on` : `Switch ${provider.name} off`}
                      </Button>
                    )}
                  </Cell>
                </tr>
              ))}
            </Table>
          </Section>
          <Section title="Models" description="A fallback is asked when its primary cannot answer, its provider switched off included.">
            {services.data.model_problem && (
              <div className="mb-3">
                <Notice tone="bad">{services.data.model_problem}</Notice>
              </div>
            )}
            <Table columns={[{ label: 'Setting' }, { label: 'Model' }, { label: 'Work' }]}>
              {services.data.models.map((model) => (
                <tr key={model.variable}>
                  <Cell className="font-mono text-xs">{model.variable}</Cell>
                  <Cell className="font-mono text-xs">
                    {model.name || '—'} {model.disabled && <Chip>switched off</Chip>}
                  </Cell>
                  <Cell>{model.role === 'agent' ? 'answers' : 'indexing'}{model.fallback && ', as the fallback'}</Cell>
                </tr>
              ))}
            </Table>
          </Section>
          <Section title="The rest">
            <dl className="divide-y divide-line">
              <Row label="Embeddings">
                <span className="font-mono text-xs">
                  {services.data.embedding_model} at {services.data.embedding_address}
                </span>
              </Row>
              <Row label="Scanner">{services.data.scanner ? 'ClamAV scans every upload' : 'none: uploads are not scanned'}</Row>
              <Row label="Storage">{services.data.storage_origin ?? 'a directory'}</Row>
              <Row label="Database">
                Postgres {services.data.database.version} with pgvector {services.data.database.vector ?? 'not installed'}, as{' '}
                {services.data.database.role}
              </Row>
              <Row label="Role's timeouts">
                {Object.entries(services.data.database.timeouts)
                  .map(([name, value]) => `${name} ${value === '0' ? 'none' : value}`)
                  .join(', ')}
              </Row>
            </dl>
          </Section>
        </div>
      )}
      {asking && (
        <Confirm
          title={`Switch ${asking.name} off?`}
          body="No process of the deployment asks its models from a few seconds on, until it is switched on again here; a fallback of another provider answers in their place."
          action="Switch it off"
          onConfirm={() => toggle.mutate({ name: asking.name, off: true })}
          onClose={() => setAsking(null)}
        />
      )}
    </>
  )
}

/** Whether a provider is on, and what holds it off: the setting, or an operator, by whom and since when. */
function switchWords(provider: Provider): string {
  if (provider.by_setting) return 'off by DISABLED_PROVIDERS'
  if (!provider.off) return 'on'
  const since = provider.switched_at ? ` since ${formatDateTime(provider.switched_at)}` : ''
  return `switched off${since}${provider.switched_by ? ` by ${provider.switched_by}` : ''}`
}
