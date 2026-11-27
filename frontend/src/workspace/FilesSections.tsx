import { Folders } from 'lucide-react'
import type { ReactNode } from 'react'
import { useEffect, useId, useRef } from 'react'
import { ErrorBoundary } from '../components/ErrorBoundary.tsx'
import { SectionBar } from '../components/ui.tsx'
import { PROJECT_FILES_TOGGLE } from '../focus.ts'
import { useLocalStorage } from '../hooks.ts'
import { MyFilesSection } from './MyFiles.tsx'
import { useWorkspace } from './state.ts'

/** Which of the library's two sections are open, kept for every project in this browser. */
interface OpenSections {
  mine: boolean
  project: boolean
}

const SECTIONS_KEY = 'emudoc.library-sections'
const BOTH_OPEN: OpenSections = { mine: true, project: true }
const isOpenSections = (value: unknown): value is OpenSections =>
  typeof value === 'object' &&
  value !== null &&
  typeof (value as OpenSections).mine === 'boolean' &&
  typeof (value as OpenSections).project === 'boolean'

/**
 * The library's pane: the member's own My files on top, which only they see, then the project's files, which every member
 * sees (its folders, places and search, the trash, Email in and the connected folders), each opened and closed by its
 * heading. The workspace holds it around the library, which stays mounted while its section is closed, so an upload under
 * way goes on; going anywhere among the project's files opens their section again.
 */
export function FilesSections({ children }: { children: ReactNode }) {
  const workspace = useWorkspace()
  const [open, setOpen] = useLocalStorage<OpenSections>(SECTIONS_KEY, BOTH_OPEN, isOpenSections)
  const body = useId()
  // A folder, a place, a search or a page of the project's files opened from elsewhere (a document's path, the trash's
  // way back) is shown, never left behind a closed heading.
  const place = JSON.stringify(workspace.url.library)
  const shownPlace = useRef(place)
  useEffect(() => {
    if (shownPlace.current === place) return
    shownPlace.current = place
    setOpen((current) => (current.project ? current : { ...current, project: true }))
  }, [place, setOpen])

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      {/* My files that break leave the project's files below them. */}
      <ErrorBoundary part="library">
        <MyFilesSection open={open.mine} onToggle={() => setOpen((current) => ({ ...current, mine: !current.mine }))} capped={open.project} />
      </ErrorBoundary>
      <section aria-labelledby={PROJECT_FILES_TOGGLE} className={`flex min-h-0 flex-col ${open.project ? 'flex-1' : 'shrink-0'}`}>
        <SectionBar
          id={PROJECT_FILES_TOGGLE}
          controls={body}
          open={open.project}
          onToggle={() => setOpen((current) => ({ ...current, project: !current.project }))}
          icon={<Folders />}
          title="Project files"
        />
        <div id={body} hidden={!open.project} className="min-h-0 flex-1">
          {children}
        </div>
      </section>
    </div>
  )
}
