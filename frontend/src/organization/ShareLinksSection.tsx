// The organisation page's share links, for its owners and admins while links are rolled out to it
// (backend/app/share_links.py): whether its members may share documents by link at all, which off stops every link at
// once, and every link that has not expired, by what it shows, each revoked from its row.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { organizations } from '../api.ts'
import type { Me } from '../api.ts'
import { setSignedIn } from '../auth.ts'
import { Checkbox, Notice, Section } from '../components/ui.tsx'
import { ORGANIZATION_LINKS_KEY, linkAbout, linkKey } from '../shareLinks.ts'
import { LinkRow } from '../workspace/ShareLinks.tsx'

export function ShareLinksSection({ me }: { me: Me }) {
  const queryClient = useQueryClient()
  const organization = me.organization
  const links = useQuery({ queryKey: ORGANIZATION_LINKS_KEY, queryFn: organizations.shareLinks })
  const save = useMutation({
    mutationFn: (on: boolean) => organizations.change({ share_links: on }),
    onSuccess: (account) => setSignedIn(queryClient, account),
  })
  const checked = save.isPending ? save.variables : organization.share_links

  return (
    <Section
      title="Share links"
      description="Links that show one document, or one answer, to someone outside the workspace, with no account, for a few days."
    >
      <div className="max-w-md space-y-2">
        <Checkbox
          label={
            <>
              <span className="font-medium">Let members share documents and answers by link</span>
              <span className="block text-muted">
                Off, every link stops at once; on again, those that have not run out work again.
              </span>
            </>
          }
          checked={checked}
          disabled={save.isPending}
          onChange={(event) => save.mutate(event.target.checked)}
        />
        {save.isError && <Notice tone="bad">{save.error.message}</Notice>}
      </div>
      <h3 className="mt-5 text-[13px] font-medium">Links running</h3>
      {links.isError ? (
        <Notice tone="bad">{links.error.message}</Notice>
      ) : links.data && links.data.length > 0 ? (
        <ul className="mt-1 divide-y divide-line border-t border-line">
          {links.data.map((link) => (
            <LinkRow
              key={link.id}
              link={link}
              about={linkAbout(link)}
              onRevoked={() => {
                void queryClient.invalidateQueries({ queryKey: ORGANIZATION_LINKS_KEY })
                void queryClient.invalidateQueries({ queryKey: linkKey(link) })
              }}
            />
          ))}
        </ul>
      ) : (
        links.data && <p className="mt-1 text-[13px] text-muted">No share link of the workspace is running.</p>
      )}
    </Section>
  )
}
