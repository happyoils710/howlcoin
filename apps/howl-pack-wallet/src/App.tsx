import { useEffect } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { Shell } from '@/components/Shell'
import { Onboarding } from '@/pages/Onboarding'
import { Unlock } from '@/pages/Unlock'
import { Home } from '@/pages/Home'
import { Send } from '@/pages/Send'
import { Receive } from '@/pages/Receive'
import { Activity } from '@/pages/Activity'
import { Discover } from '@/pages/Discover'
import { Swap } from '@/pages/Swap'
import { More } from '@/pages/More'
import { useWallet } from '@/stores/wallet'
import { useSettings } from '@/stores/settings'

function RequireWallet({ children }: { children: React.ReactNode }) {
  const hasVault = useWallet((s) => s.hasVault)
  const unlocked = useWallet((s) => s.unlocked)
  const location = useLocation()
  if (!hasVault) return <Navigate to="/onboarding" replace state={{ from: location }} />
  if (!unlocked) return <Navigate to="/unlock" replace state={{ from: location }} />
  return children
}

export default function App() {
  const bootstrap = useWallet((s) => s.bootstrap)
  const theme = useSettings((s) => s.theme)
  const setTheme = useSettings((s) => s.setTheme)
  useEffect(() => {
    bootstrap()
    setTheme(theme)
  }, [bootstrap, setTheme, theme])

  return (
    <Routes>
      <Route path="/onboarding" element={<Onboarding />} />
      <Route path="/unlock" element={<Unlock />} />
      <Route
        element={
          <RequireWallet>
            <Shell />
          </RequireWallet>
        }
      >
        <Route index element={<Home />} />
        <Route path="send" element={<Send />} />
        <Route path="receive" element={<Receive />} />
        <Route path="activity" element={<Activity />} />
        <Route path="discover" element={<Discover />} />
        <Route path="swap" element={<Swap />} />
        <Route path="more" element={<More />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
