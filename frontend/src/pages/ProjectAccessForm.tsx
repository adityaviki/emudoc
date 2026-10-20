// Who sees a project: everyone in the organisation, or the members chosen, besides its owners
// and admins, who see every project (backend/app/access.py). Offered to whoever may change the
// project; what it lists and saves is projectAccess.ts.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ROLE_LABELS, api, isPlanLimit, organizations } from '../api.ts'
import type { Me, Member, Project, ProjectVisibility } from '../api.ts'
import { PlanLimitNotice } from '../components/PlanLimit.tsx'
import { Button, Checkbox, Chip, Loading, Notice, Radio } from '../components/ui.tsx'
import { accessRows, chosenIds } from '../projectAccess.ts'

export function ProjectAccessForm({ project, me, onClose }: { project: Project; me: Me; onClose: () => void }) {
  const access = useQuery({ queryKey: ['project', project.id, 'access'], queryFn: () => api.projectAccess(project.id) })
  const members = useQuery({ queryKey: ['organization', 'members'], queryFn: organizations.members })
  const box = useRef<HTMLElement>(null)

  // The form opens above the projects, which may be far below where the card was. A block, since
  // the browser's scrollIntoView may return a promise, which React would take for a cleanup.
  useEffect(() => {
    void box.current?.scrollIntoView({ block: 'nearest' })
  }, [])

  return (
    <section ref={box} className="mb-6 rounded-xl border border-line bg-surface p-5 shadow-xs">
      <h2 className="font-semibold">Who sees {project.name}</h2>
      <p className="mt-0.5 mb-4 text-[13px] text-muted">
        The owners and admins of {me.organization.name} see every project; anyone not chosen for a private one finds no trace of it.
      </p>
      {(access.isPending || members.isPending) && <Loading />}
      {access.isError && <Notice tone="bad">{access.error.message}</Notice>}
      {members.isError && <Notice tone="bad">{members.error.message}</Notice>}
      {access.data && members.data && (
        <Choice project={project} me={me} visibility={access.data.visibility} chosen={access.data.user_ids} members={members.data} onClose={onClose} />
      )}
    </section>
  )
}

interface ChoiceProps {
  project: Project
  me: Me
  visibility: ProjectVisibility
  chosen: number[]
  members: Member[]
  onClose: () => void
}

/** The choice itself, once the project's access and the organisation's members are known. */
function Choice({ project, me, visibility: saved, chosen: savedChosen, members, onClose }: ChoiceProps) {
  const queryClient = useQueryClient()
  const [visibility, setVisibility] = useState(saved)
  const [chosen, setChosen] = useState(() => new Set(savedChosen))
  const save = useMutation({
    mutationFn: () =>
      api.setProjectAccess(project.id, {
        visibility,
        user_ids: visibility === 'private' ? chosenIds(chosen, me) : [],
      }),
    onSuccess: (access) => {
      queryClient.setQueryData(['project', project.id, 'access'], access)
      void queryClient.invalidateQueries({ queryKey: ['projects'] })
      void queryClient.invalidateQueries({ queryKey: ['project', project.id], exact: true })
      onClose()
    },
  })

  function toggle(id: number, on: boolean) {
    setChosen((before) => {
      const after = new Set(before)
      if (on) after.add(id)
      else after.delete(id)
      return after
    })
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    save.mutate()
  }

  const name = `visibility-${project.id}`
  return (
    <form onSubmit={submit} className="space-y-4">
      <fieldset className="space-y-3">
        <legend className="sr-only">Who sees the project</legend>
        <Radio
          name={name}
          label={`Everyone in ${me.organization.name}`}
          hint="Every member finds it; viewers read it and ask about it."
          checked={visibility === 'organization'}
          onChange={() => setVisibility('organization')}
        />
        <Radio
          name={name}
          label="Only the members chosen"
          hint="Besides the owners and admins. A viewer chosen reads it and asks about it."
          checked={visibility === 'private'}
          onChange={() => setVisibility('private')}
        />
      </fieldset>
      {visibility === 'private' && (
        <ul className="divide-y divide-line border-y border-line">
          {accessRows(members, chosen, me).map(({ member, always, chosen: isChosen, held }) => {
            const label = (
              <span className="flex flex-wrap items-center gap-x-2">
                <span className="font-medium">{member.name || member.email}</span>
                {member.user_id === me.id && <Chip tone="accent">You</Chip>}
                <Chip>{ROLE_LABELS[member.role]}</Chip>
                {member.name && <span className="text-xs text-muted">{member.email}</span>}
              </span>
            )
            return (
              <li key={member.user_id} className="py-2">
                {always ? (
                  <div className="flex items-center gap-2.5 text-[13px]">
                    <span className="size-4 shrink-0" />
                    <span className="min-w-0 flex-1">{label}</span>
                    <span className="text-xs text-muted">Sees every project</span>
                  </div>
                ) : (
                  <Checkbox
                    label={label}
                    checked={isChosen}
                    disabled={held}
                    title={held ? 'You stay among its members: you would not see it otherwise.' : undefined}
                    onChange={(event) => toggle(member.user_id, event.target.checked)}
                  />
                )}
              </li>
            )
          })}
        </ul>
      )}
      {save.isError &&
        (isPlanLimit(save.error) ? <PlanLimitNotice refusal={save.error} /> : <Notice tone="bad">{save.error.message}</Notice>)}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={save.isPending}>
          Save
        </Button>
      </div>
    </form>
  )
}
