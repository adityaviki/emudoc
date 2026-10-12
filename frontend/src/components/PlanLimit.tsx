// What the app shows when the organisation's plan refused something (a 402 from the API): the
// server's sentence, which names the limit and says to upgrade, and which plan the organisation
// is on. Plans are changed by the Emudoc team until self-serve billing exists.

import { X } from 'lucide-react'
import { PLAN_LABELS } from '../api.ts'
import { useMe } from '../auth.ts'
import { IconButton, Notice } from './ui.tsx'

export function PlanLimitNotice({ message, onDismiss }: { message: string; onDismiss?: () => void }) {
  const me = useMe()
  return (
    <Notice tone="info">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-ink">{message}</p>
          <p className="mt-1">
            Your organisation is on the {PLAN_LABELS[me.organization.plan]} plan. Contact the Emudoc team to
            change it.
          </p>
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
