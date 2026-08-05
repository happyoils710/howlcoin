import { useEffect } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { Shell } from '@/components/Shell'
import { Onboarding } from '@/pages/Onboarding'
import { Unlock } from '@/pages/Unlock'
import { ClassicHost } from '@/pages/ClassicHost'
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

/** Map pack routes → classic showPage ids (full feature set). */
function Host({ page, playTab }: { page: string; playTab?: string }) {
  return <ClassicHost page={page} playTab={playTab} />
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
        <Route index element={<Host page="home" />} />
        <Route path="play" element={<Host page="play" />} />
        <Route path="play/city" element={<Host page="play" playTab="city" />} />
        <Route path="charts" element={<Host page="markets" />} />
        <Route path="markets" element={<Host page="markets" />} />
        <Route path="discover" element={<Host page="discover" />} />
        <Route path="browser" element={<Host page="browser" />} />
        <Route path="more" element={<Host page="more" />} />
        <Route path="send" element={<Host page="send" />} />
        <Route path="receive" element={<Host page="recv" />} />
        <Route path="recv" element={<Host page="recv" />} />
        <Route path="activity" element={<Host page="activity" />} />
        <Route path="swap" element={<Host page="swap" />} />
        <Route path="assets" element={<Host page="assets" />} />
        <Route path="nft" element={<Host page="nft" />} />
        <Route path="names" element={<Host page="names" />} />
        <Route path="contracts" element={<Host page="contracts" />} />
        <Route path="oracle" element={<Host page="oracle" />} />
        <Route path="status" element={<Host page="status" />} />
        <Route path="wc" element={<Host page="wc" />} />
        <Route path="appearance" element={<Host page="appearance" />} />
        <Route path="sec" element={<Host page="sec" />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
