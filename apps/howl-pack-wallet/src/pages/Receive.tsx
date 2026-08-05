import { QRCodeSVG } from 'qrcode.react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Button, Card, PageTitle } from '@/components/ui'
import { PACK_CHAINS } from '@/lib/chains'
import { useSettings } from '@/stores/settings'
import { useWallet } from '@/stores/wallet'

export function Receive() {
  const address = useWallet((s) => s.derived?.address)
  const chainKey = useSettings((s) => s.chainKey)
  const [copied, setCopied] = useState(false)

  async function copy() {
    if (!address) return
    await navigator.clipboard.writeText(address)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div>
      <PageTitle title="Receive" subtitle={`On ${PACK_CHAINS[chainKey].label}`}
        right={<Link to="/" className="muted text-sm font-semibold no-underline">Close</Link>} />
      <Card className="mb-4 flex flex-col items-center py-6">
        {address ? <div className="rounded-2xl bg-white p-3"><QRCodeSVG value={address} size={180} level="M" /></div> : null}
        <p className="mono mt-4 text-center text-sm">{address || '—'}</p>
      </Card>
      <Button onClick={copy}>{copied ? 'Copied' : 'Copy address'}</Button>
      <p className="muted mt-4 text-center text-xs">
        Only send assets on {PACK_CHAINS[chainKey].label}. Wrong network = lost funds.
      </p>
    </div>
  )
}
