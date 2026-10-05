import { useOutletContext } from 'react-router'
import type { Project } from './api.ts'

/** The project of the current route, provided by ProjectLayout. */
export function useProject(): Project {
  return useOutletContext<Project>()
}
