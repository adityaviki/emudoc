// What the app shows when the organisation's plan refused something (a 402 from the API): the
// server's sentence, which names the limit, and beneath it the way past it for whoever reads it
// (`offerFor` in billing.ts): a pack for the month bought on the spot, the plans on the
// organisation page, Manage billing for a subscription's plan or seats, or whom to ask. Stripe's
// pages are Stripe's: the browser goes to the address the server gives, and comes back to the
// organisation page, which finishes the purchase.

import { useMutation } from '@tanstack/react-query'
import { CreditCard, X } from 'lucide-react'
import { Link } from 'react-router'
import { organizations } from '../api.ts'
import type { PlanLimit, PlanRefusal } from '../api.ts'
import { useMe } from '../auth.ts'
import { offerFor, thisMonth } from '../billing.ts'
import { Button, IconButton, Notice } from './ui.tsx'

export function PlanLimitNotice({ refusal, onDismiss }: { refusal: PlanRefusal; onDismiss?: () => void }) {
  return (
    <Notice tone="info">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-ink">{refusal.message}</p>
          <PlanOffer limit={refusal.limit} />
        </div>
        {onDismiss && (
          <IconButton label="Dismiss" onClick={onDismiss} className="p-0.5!">
            <X className="size-3.5" />
          </IconButton>
        )}
      </div>
    </Notice>
  )
}

/** The way past `limit` for the member reading it, beneath a sentence that named the limit. */
export function PlanOffer({ limit }: { limit: PlanLimit | null }) {
  const me = useMe()
  const offer = offerFor(limit, me.organization, me.role)
  const away = ({ url }: { url: string }) => window.location.assign(url)
  const buyPack = useMutation({ mutationFn: organizations.buyPack, onSuccess: away })
  const manage = useMutation({ mutationFn: organizations.billingPortal, onSuccess: away })
  // Stripe's page is loading once an address came back: nothing is offered twice meanwhile.
  const leaving = [buyPack, manage].some((step) => step.isPending || step.isSuccess)
  const error = buyPack.error ?? manage.error

  return (
    <>
      <p className="mt-1">{offer.text}</p>
      {(offer.packs.length > 0 || offer.plans || offer.manage) && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {offer.packs.map((pack) => (
            <Button key={`${pack.unit}:${pack.quantity}`} disabled={leaving} onClick={() => buyPack.mutate(pack)}>
              Buy {pack.quantity.toLocaleString()} {pack.unit} for {thisMonth()}
            </Button>
          ))}
          {offer.plans && (
            <Link to="/organization" className="text-[13px] font-medium text-accent hover:underline">
              See the plans
            </Link>
          )}
          {offer.manage && (
            <Button icon={<CreditCard className="size-4" />} disabled={leaving} onClick={() => manage.mutate()}>
              Manage billing
            </Button>
          )}
        </div>
      )}
      {error && <p className="mt-1 text-bad">{error.message}</p>}
    </>
  )
}
