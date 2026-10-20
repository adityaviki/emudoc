import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FolderOpen, Lock, Plus, Trash2, Users } from 'lucide-react'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { api, isPlanLimit, mayChange } from '../api.ts'
import type { Project } from '../api.ts'
import { useMe } from '../auth.ts'
import { PlanLimitNotice } from '../components/PlanLimit.tsx'
import { Button, Checkbox, EmptyState, FIELD, IconButton, Loading, Notice } from '../components/ui.tsx'
import { formatDate } from '../format.ts'
import { ProjectAccessForm } from './ProjectAccessForm.tsx'

export function ProjectsPage() {
  const me = useMe()
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects })
  const [creating, setCreating] = useState(false)
  // The project whose access the form above the projects shows, by id.
  const [managing, setManaging] = useState<number | null>(null)
  // A viewer reads the projects and asks about them; the page offers no change the server refuses.
  // Whoever may change a project chooses who sees it, as the server lets them.
  const changes = mayChange(me.role)
  const managed = projects.data?.find((project) => project.id === managing)

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-6 py-10">
        <div className="mb-6 flex items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
            <p className="mt-1 text-[13px] text-muted">
              A project holds a set of documents that are indexed and queried together.
            </p>
          </div>
          {changes && !creating && (
            <Button
              variant="primary"
              icon={<Plus className="size-4" />}
              onClick={() => {
                setManaging(null)
                setCreating(true)
              }}
            >
              New project
            </Button>
          )}
        </div>

        {creating && <NewProjectForm onClose={() => setCreating(false)} />}
        {managed && <ProjectAccessForm key={managed.id} project={managed} me={me} onClose={() => setManaging(null)} />}
        {projects.isPending && <Loading />}
        {projects.isError && <Notice tone="bad">Could not load projects: {projects.error.message}</Notice>}
        {projects.data?.length === 0 && !creating && (
          <div className="rounded-xl border border-dashed border-line">
            <EmptyState icon={<FolderOpen />} title="No projects yet">
              {changes
                ? 'Create one to start uploading documents and asking questions about them.'
                : 'Your role in this organisation is viewer: once a member makes a project, it shows here for you to read and ask about.'}
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
                setManaging(project.id)
              }}
            />
          ))}
        </div>
      </div>
    </div>
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
            <PlanLimitNotice message={create.error.message} />
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

function ProjectCard({ project, changes, onManage }: { project: Project; changes: boolean; onManage: () => void }) {
  const queryClient = useQueryClient()
  const remove = useMutation({
    mutationFn: () => api.deleteProject(project.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['projects'] }),
  })

  function confirmDelete() {
    const count = project.document_count
    const warning = count ? ` and its ${count} document${count === 1 ? '' : 's'}` : ''
    if (window.confirm(`Delete "${project.name}"${warning}? This cannot be undone.`)) remove.mutate()
  }

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
          <p className="mt-1 text-[13px] text-muted/60">No description</p>
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
        </p>
      </Link>
      {changes && (
        <div className="absolute top-3 right-3 flex gap-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100">
          <IconButton label="Who sees it" onClick={onManage}>
            <Users className="size-4" />
          </IconButton>
          <IconButton label="Delete project" danger onClick={confirmDelete} disabled={remove.isPending}>
            <Trash2 className="size-4" />
          </IconButton>
        </div>
      )}
    </div>
  )
}
