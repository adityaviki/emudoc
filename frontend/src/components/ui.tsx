import { ChevronRight, LoaderCircle } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useId } from 'react'
import type { AnchorHTMLAttributes, ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, Ref, SelectHTMLAttributes } from 'react'
import { Link } from 'react-router'
import type { LinkProps } from 'react-router'

const BUTTON_VARIANTS = {
  primary: 'bg-accent text-accent-ink shadow-xs hover:bg-accent-hover',
  secondary: 'border border-line bg-surface text-ink shadow-xs hover:bg-sunken',
  ghost: 'text-muted hover:bg-sunken hover:text-ink',
  danger: 'text-bad hover:bg-bad/10',
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: keyof typeof BUTTON_VARIANTS
  icon?: ReactNode
  ref?: Ref<HTMLButtonElement>
}

const BUTTON_BASE =
  'inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-[13px] font-medium whitespace-nowrap transition disabled:cursor-not-allowed disabled:opacity-50'

const buttonLook = (variant: keyof typeof BUTTON_VARIANTS) => `${BUTTON_BASE} ${BUTTON_VARIANTS[variant]}`

export function Button({ variant = 'secondary', icon, children, className = '', ...props }: ButtonProps) {
  return (
    <button type="button" className={`${buttonLook(variant)} ${className}`} {...props}>
      {icon}
      {children}
    </button>
  )
}

interface ButtonLinkProps extends AnchorHTMLAttributes<HTMLAnchorElement> {
  variant?: keyof typeof BUTTON_VARIANTS
  icon?: ReactNode
}

/** A link that looks like a button, for what the browser opens or saves itself, such as a download. */
export function ButtonLink({ variant = 'secondary', icon, children, className = '', ...props }: ButtonLinkProps) {
  return (
    <a className={`${buttonLook(variant)} ${className}`} {...props}>
      {icon}
      {children}
    </a>
  )
}

interface LinkButtonProps extends LinkProps {
  variant?: keyof typeof BUTTON_VARIANTS
}

/** A link to another of the app's pages that looks like a button, for the way a step is taken there. */
export function LinkButton({ variant = 'secondary', className = '', ...props }: LinkButtonProps) {
  return <Link className={`${buttonLook(variant)} ${className}`} {...props} />
}

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string
  danger?: boolean
  ref?: Ref<HTMLButtonElement>
}

export function IconButton({ label, danger, children, className = '', ...props }: IconButtonProps) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      className={`inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted transition hover:bg-sunken disabled:opacity-40 ${
        danger ? 'hover:text-bad' : 'hover:text-ink'
      } ${className}`}
      {...props}
    >
      {children}
    </button>
  )
}

/** The look of a text input or textarea; see the `field` utility in index.css. */
export const FIELD = 'field px-3 py-1.5 text-sm'

export interface InputFieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: ReactNode
  /** A line under the input: what is expected, or what went wrong. */
  hint?: ReactNode
  /** Something small at the right end of the label line, such as a "Forgot?" link. */
  corner?: ReactNode
}

/** A labelled text input, as the sign-in and account forms use it. */
export function InputField({ label, hint, corner, id, className = '', ...props }: InputFieldProps) {
  const generated = useId()
  const inputId = id ?? generated
  return (
    <div className={className}>
      <div className="mb-1 flex items-baseline justify-between gap-2 text-xs">
        <label htmlFor={inputId} className="font-medium text-muted">
          {label}
        </label>
        {corner}
      </div>
      <input id={inputId} className={FIELD} {...props} />
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  )
}

export interface SelectFieldProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label: ReactNode
  hint?: ReactNode
}

/** A labelled list to pick one from, beside the text inputs of a form. */
export function SelectField({ label, hint, id, className = '', children, ...props }: SelectFieldProps) {
  const generated = useId()
  const selectId = id ?? generated
  return (
    <div className={className}>
      <label htmlFor={selectId} className="mb-1 block text-xs font-medium text-muted">
        {label}
      </label>
      <select id={selectId} className={FIELD} {...props}>
        {children}
      </select>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  )
}

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  /** What ticking it says; may hold links. */
  label: ReactNode
  /** Neither ticked nor clear: true of some of what it stands for (the box's `indeterminate`, which is no attribute). */
  mixed?: boolean
}

/** A box to tick beside a sentence. */
export function Checkbox({ label, id, className = '', mixed = false, ...props }: CheckboxProps) {
  const generated = useId()
  const inputId = id ?? generated
  return (
    <div className={`flex items-start gap-2.5 ${className}`}>
      <input
        id={inputId}
        type="checkbox"
        className="mt-0.5 size-4 shrink-0 accent-accent"
        ref={(input) => {
          if (input) input.indeterminate = mixed
        }}
        {...props}
      />
      <label htmlFor={inputId} className="text-[13px] leading-snug">
        {label}
      </label>
    </div>
  )
}

export interface RadioProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label: ReactNode
  /** What choosing it means, under the label. */
  hint?: ReactNode
}

/** One of the choices that share a `name`, such as who sees a project. */
export function Radio({ label, hint, id, className = '', ...props }: RadioProps) {
  const generated = useId()
  const inputId = id ?? generated
  return (
    <div className={`flex items-start gap-2.5 ${className}`}>
      <input
        id={inputId}
        type="radio"
        className="mt-0.5 size-4 shrink-0 accent-accent"
        aria-describedby={hint ? `${inputId}-hint` : undefined}
        {...props}
      />
      <div className="text-[13px] leading-snug">
        <label htmlFor={inputId} className="font-medium">
          {label}
        </label>
        {hint && (
          <p id={`${inputId}-hint`} className="mt-0.5 text-xs text-muted">
            {hint}
          </p>
        )}
      </div>
    </div>
  )
}

export function Chip({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'accent' }) {
  const tones = {
    neutral: 'bg-sunken text-muted',
    accent: 'bg-accent-soft text-accent',
  }
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-px text-[11px] font-medium ${tones[tone]}`}>
      {children}
    </span>
  )
}

export function Spinner({ className = 'size-4' }: { className?: string }) {
  return <LoaderCircle className={`animate-spin text-muted ${className}`} aria-label="Loading" />
}

export function Loading() {
  return (
    <div className="flex justify-center py-16">
      <Spinner className="size-5" />
    </div>
  )
}

export function EmptyState({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-1.5 px-6 py-16 text-center">
      {icon && (
        <div className="mb-2 flex size-12 items-center justify-center rounded-full bg-sunken text-muted [&_svg]:size-5">
          {icon}
        </div>
      )}
      <p className="font-medium">{title}</p>
      {children && <div className="max-w-md text-[13px] leading-relaxed text-muted">{children}</div>}
    </div>
  )
}

interface SegmentedProps<T extends string> {
  value: T
  onChange: (value: T) => void
  options: { value: T; label: string; icon?: LucideIcon }[]
  /** What the choices are about, for a reader that cannot see the row's place. */
  label?: string
}

/** A row of mutually exclusive choices, one of them pressed. */
export function Segmented<T extends string>({ value, onChange, options, label }: SegmentedProps<T>) {
  return (
    <div role={label ? 'group' : undefined} aria-label={label} className="flex shrink-0 rounded-md bg-sunken p-0.5 text-[13px]">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
          className={`flex h-6.5 items-center gap-1.5 rounded-[5px] px-2.5 transition ${
            value === option.value ? 'bg-surface font-medium text-ink shadow-xs' : 'text-muted hover:text-ink'
          }`}
        >
          {option.icon && <option.icon className="size-3.5" />}
          {option.label}
        </button>
      ))}
    </div>
  )
}

export function Notice({ tone = 'warn', children }: { tone?: 'warn' | 'bad' | 'info'; children: ReactNode }) {
  const tones = {
    warn: 'border-warn/25 bg-warn/5 text-warn',
    bad: 'border-bad/25 bg-bad/5 text-bad',
    info: 'border-line bg-surface text-muted',
  }
  return <div className={`rounded-md border px-3 py-2 text-[13px] leading-relaxed ${tones[tone]}`}>{children}</div>
}

/** A titled card of a settings page, such as the account page's; an `id` lets a link open the page at it. */
export function Section({ title, description, id, children }: { title: string; description?: ReactNode; id?: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-6 rounded-xl border border-line bg-surface p-5 shadow-xs">
      <h2 className="font-semibold">{title}</h2>
      {description && <p className="mt-0.5 text-[13px] leading-relaxed text-muted">{description}</p>}
      <div className="mt-4">{children}</div>
    </section>
  )
}

interface SectionBarProps {
  /** The heading's button, which the section is labelled by and a page may give the focus to. */
  id: string
  /** The element the button shows and hides. */
  controls: string
  open: boolean
  onToggle: () => void
  icon: ReactNode
  title: string
  /** What the bar holds at its end, beside the heading: a count, a button. */
  children?: ReactNode
}

/**
 * The heading of a part of a pane that opens and closes, such as the library's My files under its search: a button turning
 * its chevron as it does, on the pane's own ground.
 */
export function SectionBar({ id, controls, open, onToggle, icon, title, children }: SectionBarProps) {
  return (
    <div className="flex h-9 shrink-0 items-center gap-1 px-2">
      <h2 className="flex min-w-0 flex-1">
        <button
          id={id}
          type="button"
          aria-expanded={open}
          aria-controls={controls}
          onClick={onToggle}
          className="flex h-7 min-w-0 items-center gap-1.5 rounded-md pr-2 pl-1 text-[13px] font-semibold text-ink transition hover:bg-sunken"
        >
          <ChevronRight aria-hidden className={`size-4 shrink-0 text-muted transition-transform ${open ? 'rotate-90' : ''}`} />
          <span aria-hidden className="flex shrink-0 text-muted [&_svg]:size-4">
            {icon}
          </span>
          <span className="truncate">{title}</span>
        </button>
      </h2>
      {children}
    </div>
  )
}

/** A label and its value, in the definition list of a settings section. */
export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1 py-2 text-[13px]">
      <dt className="w-28 shrink-0 text-muted">{label}</dt>
      <dd className="flex min-w-0 flex-1 flex-wrap items-center gap-2">{children}</dd>
    </div>
  )
}
