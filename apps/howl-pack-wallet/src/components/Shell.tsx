import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { Home, Sparkles, LineChart, Compass, MoreHorizontal } from 'lucide-react'
import clsx from 'clsx'
import { useEffect } from 'react'
import { useWallet } from '@/stores/wallet'
import { useSettings } from '@/stores/settings'

/** Bottom tabs mirror classic: Home · Play · Charts · Discover · More */
const tabs = [
  { to: '/', icon: Home, label: 'Home', end: true },
  { to: '/play', icon: Sparkles, label: 'Play' },
  { to: '/charts', icon: LineChart, label: 'Charts' },
  { to: '/discover', icon: Compass, label: 'Discover' },
  { to: '/more', icon: MoreHorizontal, label: 'More' },
]

export function Shell() {
  const touch = useWallet((s) => s.touch)
  const lock = useWallet((s) => s.lock)
  const unlocked = useWallet((s) => s.unlocked)
  const lastActivity = useWallet((s) => s.lastActivity)
  const autoLockMin = useSettings((s) => s.autoLockMin)
  const location = useLocation()

  useEffect(() => {
    const on = () => touch()
    window.addEventListener('pointerdown', on)
    window.addEventListener('keydown', on)
    return () => {
      window.removeEventListener('pointerdown', on)
      window.removeEventListener('keydown', on)
    }
  }, [touch])

  useEffect(() => {
    if (!unlocked || !autoLockMin) return
    const id = window.setInterval(() => {
      if (Date.now() - useWallet.getState().lastActivity > autoLockMin * 60_000) lock()
    }, 15_000)
    return () => clearInterval(id)
  }, [unlocked, autoLockMin, lock, lastActivity])

  // Full-bleed for classic host (no side padding)
  const fullBleed = !location.pathname.includes('/settings')

  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-lg flex-col">
      <main
        className={clsx(
          'flex-1 overflow-hidden pb-[calc(4.25rem+var(--safe-b))] pt-[max(0px,var(--safe-t))]',
          fullBleed ? 'px-0' : 'overflow-y-auto px-4',
        )}
      >
        <Outlet />
      </main>
      <nav
        className="fixed bottom-0 left-0 right-0 z-40 border-t border-[var(--border)] bg-[var(--bottom)] backdrop-blur-xl"
        style={{ paddingBottom: 'var(--safe-b)' }}
      >
        <div className="mx-auto grid max-w-lg grid-cols-5">
          {tabs.map(({ to, icon: Icon, label, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                clsx(
                  'flex flex-col items-center justify-center gap-0.5 py-2 text-[0.65rem] font-semibold no-underline',
                  isActive ? 'text-[var(--mint)]' : 'text-[var(--muted)]',
                )
              }
            >
              <Icon size={22} strokeWidth={2.2} />
              {label}
            </NavLink>
          ))}
        </div>
      </nav>
    </div>
  )
}
