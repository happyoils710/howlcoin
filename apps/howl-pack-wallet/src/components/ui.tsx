import clsx from 'clsx'
import type { ButtonHTMLAttributes, ReactNode } from 'react'

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={clsx('card p-4', className)}>{children}</div>
}

export function Button({
  variant = 'primary', className, ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger' }) {
  return (
    <button
      type="button"
      className={clsx(
        'btn w-full',
        variant === 'primary' && 'btn-primary',
        variant === 'secondary' && 'btn-secondary',
        variant === 'ghost' && 'btn-ghost',
        variant === 'danger' && 'btn-danger',
        className,
      )}
      {...props}
    />
  )
}

export function Field({
  multiline, className, ...rest
}: React.InputHTMLAttributes<HTMLInputElement> &
  React.TextareaHTMLAttributes<HTMLTextAreaElement> & { multiline?: boolean }) {
  if (multiline) {
    return <textarea className={clsx('field min-h-[100px] resize-y', className)} {...(rest as React.TextareaHTMLAttributes<HTMLTextAreaElement>)} />
  }
  return <input className={clsx('field', className)} {...(rest as React.InputHTMLAttributes<HTMLInputElement>)} />
}

export function PageTitle({ title, subtitle, right }: { title: string; subtitle?: string; right?: ReactNode }) {
  return (
    <div className="mb-4 flex items-start justify-between gap-3">
      <div>
        <h1 className="font-display m-0 text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle ? <p className="muted m-0 mt-1 text-sm">{subtitle}</p> : null}
      </div>
      {right}
    </div>
  )
}

export function Spinner({ label = 'Loading…' }: { label?: string }) {
  return <p className="muted py-6 text-center text-sm">{label}</p>
}
