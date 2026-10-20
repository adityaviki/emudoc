// How each kind of notice is told, in Emudoc and by email, as a table of boxes: a member's own
// choices on the account page, and an organisation's for its members on its page. A box shows
// the choice at once and goes back if it is not saved; keyed by the saved choices, so that a
// change from elsewhere (a notice's link, another tab) shows once the account is read again.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { Me, NoticeChoice, NoticeKind } from '../api.ts'
import { setSignedIn } from '../auth.ts'
import { NOTICE_KINDS } from '../notifications.ts'
import { Notice } from './ui.tsx'

export type ChoiceIn = { kind: NoticeKind; app?: boolean; email?: boolean }

export function NoticeChoices({ saved, change }: { saved: NoticeChoice[]; change: (choice: ChoiceIn) => Promise<Me> }) {
  const queryClient = useQueryClient()
  const [choices, setChoices] = useState(saved)
  const save = useMutation({
    mutationFn: change,
    onSuccess: (account) => setSignedIn(queryClient, account),
    onError: () => setChoices(saved),
  })

  function choose(kind: NoticeKind, way: 'app' | 'email', on: boolean) {
    setChoices((current) => current.map((choice) => (choice.kind === kind ? { ...choice, [way]: on } : choice)))
    save.mutate({ kind, [way]: on })
  }

  return (
    <>
      <table className="w-full text-[13px]">
        <thead>
          <tr className="text-xs text-muted">
            <th className="pb-2 text-left font-normal">When</th>
            <th className="w-20 pb-2 font-normal">In Emudoc</th>
            <th className="w-20 pb-2 font-normal">By email</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line border-t border-line">
          {choices
            .filter((choice) => choice.kind in NOTICE_KINDS)
            .map((choice) => {
              const { label, detail } = NOTICE_KINDS[choice.kind]
              return (
                <tr key={choice.kind}>
                  <td className="py-2.5 pr-4">
                    <p className="font-medium">{label}</p>
                    <p className="mt-0.5 text-xs leading-relaxed text-muted">{detail}</p>
                  </td>
                  {(['app', 'email'] as const).map((way) => (
                    <td key={way} className="text-center">
                      <input
                        type="checkbox"
                        aria-label={`${label}, ${way === 'app' ? 'in Emudoc' : 'by email'}`}
                        className="size-4 accent-accent"
                        checked={choice[way]}
                        disabled={save.isPending}
                        onChange={(event) => choose(choice.kind, way, event.target.checked)}
                      />
                    </td>
                  ))}
                </tr>
              )
            })}
        </tbody>
      </table>
      {save.isError && (
        <div className="mt-2">
          <Notice tone="bad">{save.error.message}</Notice>
        </div>
      )}
    </>
  )
}
