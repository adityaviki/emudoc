import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, FolderOpen, Lock, Plus, RotateCcw, Sparkles, Trash2, Users } from 'lucide-react'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { api, isPlanLimit, mayChange } from '../api.ts'
import type { PlanRefusal, Project, TrashedProject } from '../api.ts'
import { ME_KEY, useMe } from '../auth.ts'
import { Confirm } from '../components/Confirm.tsx'
import { PlanLimitNotice } from '../components/PlanLimit.tsx'
import { Button, Checkbox, EmptyState, FIELD, IconButton, Loading, Notice } from '../components/ui.tsx'
import { DUPLICATES_KEY, duplicatesWords } from '../details.ts'
import { formatDate } from '../format.ts'
import { EXPORT_QUERY } from '../exports.ts'
import { describeTrashedProject, projectPurgeQuestion, TRASHED_PROJECTS_KEY } from '../trash.ts'
import { ProjectAccessForm } from './ProjectAccessForm.tsx'
import { ProjectExports } from './ProjectExports.tsx'

export function ProjectsPage() {
  const me = useMe()
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects })
  const [creating, setCreating] = useState(false)
  // The project whose access the form above the projects shows, by id.
  const [managing, setManaging] = useState<number | null>(null)
  // The project whose exports the panel above them shows: in the address, which the notice that
  // an export is ready leads to (EXPORT_QUERY in backend/app/export.py).
  const [params, setParams] = useSearchParams()
  const exporting = Number(params.get(EXPORT_QUERY)) || null
  // A viewer reads the projects and asks about them; the page offers no change the server refuses.
  // Whoever may change a project chooses who sees it, as the server lets them.
  const changes = mayChange(me.role)
  const managed = projects.data?.find((project) => project.id === managing)
  const exported = changes ? projects.data?.find((project) => project.id === exporting) : undefined
  const create = () => {
    setManaging(null)
    setParams({}, { replace: true })
    setCreating(true)
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-6 py-10">
        <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 id={PROJECTS_HEADING} tabIndex={-1} className="text-2xl font-semibold tracking-tight">
              Projects
            </h1>
            <p className="mt-1 text-[13px] text-muted">
              A project holds a set of documents that are indexed and queried together.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {changes && !creating && (
              <Button variant="primary" icon={<Plus className="size-4" />} onClick={create}>
                New project
              </Button>
            )}
          </div>
        </div>

        {creating && <NewProjectForm onClose={() => setCreating(false)} />}
        {managed && <ProjectAccessForm key={managed.id} project={managed} me={me} onClose={() => setManaging(null)} />}
        {exported && <ProjectExports key={exported.id} project={exported} onClose={() => setParams({}, { replace: true })} />}
        {projects.isPending && <Loading />}
        {projects.isError && <Notice tone="bad">Could not load projects: {projects.error.message}</Notice>}
        {projects.data?.length === 0 && !creating && (
          <div className="rounded-xl border border-dashed border-line">
            <EmptyState icon={<FolderOpen />} title="No projects yet">
              {changes ? (
                <>
                  Create one to start uploading documents and asking questions about them.
                  <div className="mt-4">
                    <Button variant="primary" icon={<Plus className="size-4" />} onClick={create}>
                      Create a project
                    </Button>
                  </div>
                </>
              ) : (
                'Your role in this organisation is viewer: once a member makes a project, it shows here for you to read and ask about.'
              )}
            </EmptyState>
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.data?.map((project) => (
            <ProjectCard
              key={project.id}
              project={project}
              changes={changes}
              onManage={() => {
                setCreating(false)
                setParams({}, { replace: true })
                setManaging(project.id)
              }}
              onExport={() => {
                setCreating(false)
                setManaging(null)
                setParams({ [EXPORT_QUERY]: String(project.id) }, { replace: true })
              }}
            />
          ))}
        </div>
        <Duplicates />
        {changes && <ProjectTrash />}
      </div>
    </div>
  )
}

/** The way to the files held in more than one of the projects, while there are some. */
function Duplicates() {
  const summary = useQuery({ queryKey: [...DUPLICATES_KEY, 'summary'], queryFn: () => api.duplicates(null, 1) })
  if (!summary.data?.files) return null
  return (
    <p className="mt-6 text-[13px] text-muted">
      {duplicatesWords(summary.data)}{' '}
      <Link to="/duplicates" className="font-medium text-accent hover:underline">
        See the duplicates
      </Link>
    </p>
  )
}

/**
 * The organisation's projects in the trash, each with everything it held, until a member
 * restores it, as the plan has room for, or deletes it for good, or the days it is kept pass.
 */
const PROJECTS_HEADING = 'projects-heading'

function ProjectTrash() {
  const queryClient = useQueryClient()
  const trash = useQuery({ queryKey: TRASHED_PROJECTS_KEY, queryFn: api.trashedProjects })
  const [refused, setRefused] = useState<PlanRefusal | null>(null)
  const settle = () => {
    for (const queryKey of [TRASHED_PROJECTS_KEY, ['projects'], ME_KEY]) void queryClient.invalidateQueries({ queryKey: [...queryKey] })
  }
  const options = {
    onMutate: () => {
      setRefused(null)
      // The project's row, and its button with the focus, go: the keyboard goes on from the
      // trash's heading while it holds others, else from the page's, since the section goes too.
      const left = (trash.data?.projects.length ?? 0) > 1
      document.getElementById(left ? 'project-trash' : PROJECTS_HEADING)?.focus()
    },
    onError: (error: Error) => setRefused({ message: error.message, limit: isPlanLimit(error) ? error.limit : null }),
    onSettled: settle,
  }
  const restore = useMutation({ mutationFn: (project: TrashedProject) => api.restore('project', project.id), ...options })
  const purge = useMutation({ mutationFn: (project: TrashedProject) => api.purge('project', project.id), ...options })
  const [asking, setAsking] = useState<TrashedProject | null>(null)
  if (!trash.data?.projects.length) return null

  return (
    <section aria-labelledby="project-trash" className="mt-10">
      <h2 id="project-trash" tabIndex={-1} className="font-semibold">
        Trash
      </h2>
      <p className="mt-0.5 mb-3 text-[13px] text-muted">
        A deleted project waits here with everything in it for {trash.data.days} days, then is deleted for good.
      </p>
      {refused && (
        <div className="mb-3">
          {refused.limit ? <PlanLimitNotice refusal={refused} onDismiss={() => setRefused(null)} /> : <Notice tone="bad">{refused.message}</Notice>}
        </div>
      )}
      <ul className="divide-y divide-line rounded-xl border border-line bg-surface">
        {trash.data.projects.map((project) => (
          <li key={project.id} className="flex items-center gap-3 px-4 py-2.5">
            <div className="min-w-0 flex-1" title={`Deleted for good on ${formatDate(project.purged_at)}`}>
              <p className="truncate text-[13px] font-medium">{project.name}</p>
              <p className="truncate text-xs text-muted">{describeTrashedProject(project, formatDate(project.deleted_at))}</p>
            </div>
            <Button icon={<RotateCcw className="size-4" />} disabled={restore.isPending} onClick={() => restore.mutate(project)}>
              Restore
            </Button>
            <IconButton
              label={`Delete ${project.name} for good`}
              danger
              disabled={purge.isPending}
              onClick={() => setAsking(project)}
            >
              <Trash2 className="size-4" />
            </IconButton>
          </li>
        ))}
      </ul>
      {asking && <Confirm {...projectPurgeQuestion(asking)} onConfirm={() => purge.mutate(asking)} onClose={() => setAsking(null)} />}
    </section>
  )
}

function NewProjectForm({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [secret, setSecret] = useState(false)
  const create = useMutation({
    mutationFn: api.createProject,
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      navigate(`/projects/${project.id}`)
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (name.trim()) create.mutate({ name, description, visibility: secret ? 'private' : 'organization' })
  }

  const label = 'mb-1 block text-xs font-medium text-muted'
  return (
    <form onSubmit={submit} className="mb-6 rounded-xl border border-line bg-surface p-5 shadow-xs">
      <h2 className="mb-4 font-semibold">New project</h2>
      <div className="space-y-3">
        <div>
          <label htmlFor="project-name" className={label}>
            Name
          </label>
          <input
            id="project-name"
            autoFocus
            className={FIELD}
            placeholder="e.g. Supplier contracts"
            value={name}
            maxLength={200}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <div>
          <label htmlFor="project-description" className={label}>
            Description <span className="font-normal">(optional)</span>
          </label>
          <input
            id="project-description"
            className={FIELD}
            placeholder="What is it for?"
            value={description}
            maxLength={2000}
            onChange={(event) => setDescription(event.target.value)}
          />
        </div>
        <Checkbox
          label="Private: only you, and the owners and admins, see it until you choose others"
          checked={secret}
          onChange={(event) => setSecret(event.target.checked)}
        />
      </div>
      {create.isError && (
        <div className="mt-3">
          {isPlanLimit(create.error) ? (
            <PlanLimitNotice refusal={create.error} />
          ) : (
            <Notice tone="bad">{create.error.message}</Notice>
          )}
        </div>
      )}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={!name.trim() || create.isPending}>
          Create project
        </Button>
      </div>
    </form>
  )
}

function ProjectCard({ project, changes, onManage, onExport }: { project: Project; changes: boolean; onManage: () => void; onExport: () => void }) {
  const queryClient = useQueryClient()
  // Into the trash below the projects, from which it is restored as it was.
  const remove = useMutation({
    mutationFn: () => api.deleteProject(project.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['projects'] })
      void queryClient.invalidateQueries({ queryKey: TRASHED_PROJECTS_KEY })
      // What the organisation holds past its plan, which a deletion may bring it within.
      void queryClient.invalidateQueries({ queryKey: ME_KEY })
    },
  })

  return (
    <div className="group relative rounded-xl border border-line bg-surface shadow-xs transition hover:border-ink/15 hover:shadow-sm">
      <Link to={`/projects/${project.id}`} className="flex h-full flex-col rounded-xl p-5">
        <div className="mb-4 flex size-9 items-center justify-center rounded-lg bg-accent-soft text-accent">
          <FolderOpen className="size-4.5" />
        </div>
        <p className="truncate pr-8 font-semibold" title={project.name}>
          {project.name}
        </p>
        {project.description ? (
          <p className="mt-1 line-clamp-2 text-[13px] leading-relaxed text-muted">{project.description}</p>
        ) : (
          <p className="mt-1 text-[13px] text-muted italic">No description</p>
        )}
        <p className="mt-auto pt-5 text-xs text-muted tabular-nums">
          {project.document_count} document{project.document_count === 1 ? '' : 's'}
          <span className="mx-1.5 text-line">·</span>
          {formatDate(project.created_at)}
          {project.visibility === 'private' && (
            <>
              <span className="mx-1.5 text-line">·</span>
              <span className="inline-flex items-center gap-1 align-bottom">
                <Lock className="size-3" aria-hidden />
                Private
              </span>
            </>
          )}
          {project.sample && (
            <>
              <span className="mx-1.5 text-line">·</span>
              <span className="inline-flex items-center gap-1 align-bottom" title="Emudoc’s own documents, which count against nothing on your plan">
                <Sparkles className="size-3" aria-hidden />
                Sample
              </span>
            </>
          )}
        </p>
      </Link>
      {changes && (
        <div className="absolute top-3 right-3 flex gap-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100">
          <IconButton label="Who sees it" onClick={onManage}>
            <Users className="size-4" />
          </IconButton>
          <IconButton label="Export" onClick={onExport}>
            <Download className="size-4" />
          </IconButton>
          <IconButton label="Delete project" danger onClick={() => remove.mutate()} disabled={remove.isPending}>
            <Trash2 className="size-4" />
          </IconButton>
        </div>
      )}
    </div>
  )
}
