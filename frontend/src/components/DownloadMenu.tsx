import { Download, FileCode, FileText, FileType } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useRef, useState } from 'react'
import type { MouseEvent } from 'react'
import type { TextFormat } from '../api.ts'
import { TEXT_FILES } from '../textFiles.ts'
import { Floating, Menu } from './Menu.tsx'
import type { MenuItem } from './Menu.tsx'
import { Button, IconButton } from './ui.tsx'

/** A file the menu offers: what it is for, its icon, and where it downloads from. */
export interface FileChoice {
  label: string
  icon: LucideIcon
  href: string
}

const TEXT_ICONS: Record<TextFormat, LucideIcon> = { docx: FileText, pdf: FileType, md: FileCode }

interface DownloadMenuProps {
  files: FileChoice[]
  /** What the button says, or with `iconOnly` what it is named. */
  label?: string
  /** The button shows its icon alone, where there is no room for words. */
  iconOnly?: boolean
  /** The button as small as the actions under an answer. */
  small?: boolean
  variant?: 'secondary' | 'ghost'
  disabled?: boolean
  alignEnd?: boolean
}

/**
 * A button that opens the menu of the files something downloads as. A choice is followed as a click on a link would
 * be, so the download leaves the page where it is, and the focus goes back to the button.
 */
export function DownloadMenu({ files, label = 'Download', iconOnly, small, variant, disabled, alignEnd }: DownloadMenuProps) {
  const button = useRef<HTMLButtonElement>(null)
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  const close = () => setAnchor(null)
  const save = (href: string) => () => {
    const link = window.document.createElement('a')
    Object.assign(link, { href, download: '' })
    link.click()
  }
  const items: MenuItem[] = files.map((file) => ({ label: file.label, icon: file.icon, onSelect: save(file.href) }))
  const opener = {
    ref: button,
    'aria-haspopup': 'menu' as const,
    'aria-expanded': anchor !== null,
    disabled,
    onClick: (event: MouseEvent<HTMLButtonElement>) => setAnchor(anchor ? null : event.currentTarget.getBoundingClientRect()),
  }
  return (
    <>
      {iconOnly ? (
        <IconButton label={label} {...opener}>
          <Download className="size-4" />
        </IconButton>
      ) : (
        <Button
          variant={variant}
          className={small ? 'h-6 px-2' : undefined}
          icon={<Download className={small ? 'size-3.5' : 'size-4'} />}
          {...opener}
        >
          {label}
        </Button>
      )}
      {anchor && (
        <Floating anchor={anchor} alignEnd={alignEnd} onClose={close} ignore={button}>
          <Menu items={items} onClose={close} />
        </Floating>
      )}
    </>
  )
}

/** The menu of the files a text citing passages downloads as (TEXT_FILES), each from where `urlOf` says. */
export function DownloadText({ urlOf, ...props }: Omit<DownloadMenuProps, 'files'> & { urlOf: (format: TextFormat) => string }) {
  const files = TEXT_FILES.map((file) => ({ label: file.label, icon: TEXT_ICONS[file.format], href: urlOf(file.format) }))
  return <DownloadMenu files={files} {...props} />
}
