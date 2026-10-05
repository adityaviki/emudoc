import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FolderOpen, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { api } from '../api.ts'
import type { Project } from '../api.ts'
import { Button, EmptyState, IconButton, Loading, Notice } from '../components/ui.tsx'
import { formatDate } from '../format.ts'

export function ProjectsPage() {
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects })
  const [creating, setCreating] = useState(false)

  return (
    <div className="mx-auto max-w-4xl">
      <div className="mb-5 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Projects</h1>
        {!creating && (
          <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setCreating(true)}>
            New project
          </Button>
        )}
      </div>

      {creating && <NewProjectForm onClose={() => setCreating(false)} />}
      {projects.isPending && <Loading />}
      {projects.isError && <Notice tone="bad">Could not load projects: {projects.error.message}</Notice>}
      {projects.data?.length === 0 && !creating && (
        <EmptyState icon={<FolderOpen className="size-8" />} title="No projects yet">
          A project holds a set of documents that are indexed and queried together.
        </EmptyState>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {projects.data?.map((project) => (
          <ProjectCard key={project.id} project={project} />
        ))}
      </div>
    </div>
  )
}

function NewProjectForm({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const create = useMutation({
    mutationFn: api.createProject,
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      navigate(`/projects/${project.id}`)
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (name.trim()) create.mutate({ name, description })
  }

  const field = 'w-full rounded-md border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-accent'
  return (
    <form onSubmit={submit} className="mb-5 space-y-3 rounded-lg border border-line bg-surface p-4">
      <input
        autoFocus
        className={field}
        placeholder="Project name"
        value={name}
        maxLength={200}
        onChange={(event) => setName(event.target.value)}
      />
      <input
        className={field}
        placeholder="What is it for? (optional)"
        value={description}
        maxLength={2000}
        onChange={(event) => setDescription(event.target.value)}
      />
      {create.isError && <Notice tone="bad">{create.error.message}</Notice>}
      <div className="flex justify-end gap-2">
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

function ProjectCard({ project }: { project: Project }) {
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
    <div className="group relative rounded-lg border border-line bg-surface transition hover:border-accent">
      <Link to={`/projects/${project.id}`} className="block p-4">
        <p className="pr-8 font-medium">{project.name}</p>
        {project.description && <p className="mt-0.5 line-clamp-2 text-sm text-muted">{project.description}</p>}
        <p className="mt-3 text-xs text-muted">
          {project.document_count} document{project.document_count === 1 ? '' : 's'} · created{' '}
          {formatDate(project.created_at)}
        </p>
      </Link>
      <IconButton
        label="Delete project"
        danger
        className="absolute top-3 right-3 opacity-0 group-hover:opacity-100 focus:opacity-100"
        onClick={confirmDelete}
        disabled={remove.isPending}
      >
        <Trash2 className="size-4" />
      </IconButton>
    </div>
  )
}
