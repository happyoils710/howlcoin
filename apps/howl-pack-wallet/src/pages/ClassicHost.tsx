import { useEffect, useRef, useState } from 'react'
import { useWallet } from '@/stores/wallet'
import { useSettings } from '@/stores/settings'

/** Full classic wallet feature surface, embedded in Pack chrome. */
export function ClassicHost({ page = 'home', playTab }: { page?: string; playTab?: string }) {
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const armClassicUnlock = useWallet((s) => s.armClassicUnlock)
  const theme = useSettings((s) => s.theme)
  const [ready, setReady] = useState(false)
  const [key, setKey] = useState(0)

  // Ensure session pin is available before iframe loads
  useEffect(() => {
    armClassicUnlock()
    setKey((k) => k + 1)
  }, [armClassicUnlock])

  useEffect(() => {
    function onMsg(ev: MessageEvent) {
      const d = ev.data
      if (!d || d.source !== 'howl-classic') return
      if (d.type === 'unlocked' || d.type === 'page') setReady(true)
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
  }, [])

  useEffect(() => {
    const win = iframeRef.current?.contentWindow
    if (!win) return
    win.postMessage(
      { source: 'howl-pack', type: 'nav', page, playTab, theme },
      window.location.origin,
    )
  }, [page, playTab, theme, ready, key])

  const src = `/classic?embed=1&t=${key}#${page}`

  return (
    <div className="relative -mx-4 flex min-h-[calc(100dvh-5.5rem)] flex-col">
      {!ready ? (
        <div className="pointer-events-none absolute inset-x-0 top-2 z-10 text-center">
          <span className="badge badge-ok">Loading full wallet…</span>
        </div>
      ) : null}
      <iframe
        ref={iframeRef}
        key={key}
        title="Howl Wallet"
        src={src}
        className="w-full flex-1 border-0 bg-[var(--bg)]"
        style={{ minHeight: 'calc(100dvh - 5.5rem)' }}
        allow="clipboard-read; clipboard-write; publickey-credentials-get *"
        onLoad={() => {
          // re-arm pin in case iframe raced
          armClassicUnlock()
          setTimeout(() => {
            iframeRef.current?.contentWindow?.postMessage(
              { source: 'howl-pack', type: 'nav', page, playTab, theme },
              window.location.origin,
            )
            setReady(true)
          }, 400)
        }}
      />
    </div>
  )
}
