import { useState } from 'react'
import { parseUnits } from 'viem'
import { createWalletClient, http, maxUint256 } from 'viem'
import { Button, Card, Field, PageTitle } from '@/components/ui'
import { PACK_CHAINS, getPublicClient } from '@/lib/chains'
import { CURATED_TOKENS, ERC20_ABI } from '@/lib/tokens'
import { fetch0xQuote, jumperBridgeUrl } from '@/lib/swap'
import { formatTokenAmount } from '@/lib/prices'
import { pushTx, updateTxStatus } from '@/lib/txlog'
import { useSettings } from '@/stores/settings'
import { useWallet } from '@/stores/wallet'
import clsx from 'clsx'

type Tab = 'swap' | 'bridge'

export function Swap() {
  const chainKey = useSettings((s) => s.chainKey)
  const customRpc = useSettings((s) => s.customRpcs[chainKey])
  const derived = useWallet((s) => s.derived)
  const touch = useWallet((s) => s.touch)
  const [tab, setTab] = useState<Tab>('swap')
  const tokens = CURATED_TOKENS.filter((t) => t.chainKey === chainKey)
  const [sellIdx, setSellIdx] = useState(0)
  const [buyIdx, setBuyIdx] = useState(Math.min(1, Math.max(0, tokens.length - 1)))
  const [amount, setAmount] = useState('')
  const [slippage, setSlippage] = useState('0.5')
  const [quoteInfo, setQuoteInfo] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [hash, setHash] = useState('')
  const sell = tokens[sellIdx]
  const buy = tokens[buyIdx]
  const pack = PACK_CHAINS[chainKey]

  async function getQuote() {
    setErr('')
    setQuoteInfo('')
    if (!derived || !sell || !buy) return
    if (!amount || Number(amount) <= 0) return setErr('Enter an amount')
    if (pack.chain.id !== 8453) return setErr('In-app 0x quotes on Base mainnet. Use Bridge for other chains.')
    setBusy(true)
    try {
      const q = await fetch0xQuote({
        chainId: pack.chain.id,
        sellToken: sell.address === 'native' ? 'ETH' : sell.address,
        buyToken: buy.address === 'native' ? 'ETH' : buy.address,
        sellAmount: parseUnits(amount, sell.decimals).toString(),
        taker: derived.address,
        slippageBps: Math.round(Number(slippage) * 100),
      })
      const out = formatTokenAmount(BigInt(q.buyAmount || '0'), buy.decimals)
      setQuoteInfo(`You receive ≈ ${out} ${buy.symbol}` + (q.estimatedGas ? ` · gas ~${q.estimatedGas}` : '') + ` · slip ${slippage}%`)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Quote failed')
    } finally {
      setBusy(false)
    }
  }

  async function executeSwap() {
    setErr('')
    if (!derived || !sell || !buy) return
    touch()
    setBusy(true)
    try {
      const q = await fetch0xQuote({
        chainId: pack.chain.id,
        sellToken: sell.address === 'native' ? 'ETH' : sell.address,
        buyToken: buy.address === 'native' ? 'ETH' : buy.address,
        sellAmount: parseUnits(amount, sell.decimals).toString(),
        taker: derived.address,
        slippageBps: Math.round(Number(slippage) * 100),
      })
      if (!q.transaction?.to) throw new Error('Quote missing transaction')
      const rpc = customRpc || pack.defaultRpc
      const publicClient = getPublicClient(chainKey, customRpc)
      const walletClient = createWalletClient({ account: derived.account, chain: pack.chain, transport: http(rpc) })

      if (sell.address !== 'native' && q.allowanceTarget) {
        const allowance = (await publicClient.readContract({
          address: sell.address, abi: ERC20_ABI, functionName: 'allowance',
          args: [derived.address, q.allowanceTarget],
        })) as bigint
        if (allowance < parseUnits(amount, sell.decimals)) {
          const ah = await walletClient.writeContract({
            address: sell.address, abi: ERC20_ABI, functionName: 'approve',
            args: [q.allowanceTarget, maxUint256], chain: pack.chain, account: derived.account,
          })
          pushTx({ hash: ah, chainId: pack.chain.id, from: derived.address, to: q.allowanceTarget, label: `Approve ${sell.symbol}`, status: 'pending', timestamp: Date.now(), kind: 'approve' })
          await publicClient.waitForTransactionReceipt({ hash: ah })
          updateTxStatus(ah, 'confirmed')
        }
      }

      const h = await walletClient.sendTransaction({
        to: q.transaction.to, data: q.transaction.data, value: BigInt(q.transaction.value || '0'),
        account: derived.account, chain: pack.chain,
      })
      pushTx({
        hash: h, chainId: pack.chain.id, from: derived.address, to: q.transaction.to,
        label: `Swap ${amount} ${sell.symbol} → ${buy.symbol}`, status: 'pending', timestamp: Date.now(), kind: 'swap',
      })
      publicClient.waitForTransactionReceipt({ hash: h })
        .then((r) => updateTxStatus(h, r.status === 'success' ? 'confirmed' : 'failed'))
      setHash(h)
      setQuoteInfo(`Swapped · ${h.slice(0, 10)}…`)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Swap failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <PageTitle title="Swap" subtitle="Transparent quotes · you sign every tx" />
      <div className="mb-4 grid grid-cols-2 gap-2">
        {(['swap', 'bridge'] as Tab[]).map((t) => (
          <button key={t} type="button" onClick={() => setTab(t)}
            className={clsx('rounded-xl border py-2 text-sm font-bold capitalize',
              tab === t ? 'border-[var(--mint)] bg-[var(--mint-dim)] text-[var(--mint)]' : 'border-[var(--border)] bg-[var(--panel)] text-[var(--muted)]')}>
            {t}
          </button>
        ))}
      </div>
      {tab === 'bridge' ? (
        <Card>
          <p className="m-0 text-sm">Bridge via LI.FI / Jumper across Base, Ethereum, OP, Arbitrum.</p>
          <a href={jumperBridgeUrl()} target="_blank" rel="noreferrer" className="btn btn-primary mt-4 no-underline">Open bridge</a>
        </Card>
      ) : (
        <>
          <Card className="mb-3">
            <label className="muted mb-1 block text-xs font-bold uppercase">You pay</label>
            <div className="flex gap-2">
              <Field className="flex-1" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0.0" inputMode="decimal" />
              <select className="field !w-28" value={sellIdx} onChange={(e) => setSellIdx(Number(e.target.value))}>
                {tokens.map((t, i) => <option key={i} value={i}>{t.symbol}</option>)}
              </select>
            </div>
          </Card>
          <Card className="mb-3">
            <label className="muted mb-1 block text-xs font-bold uppercase">You receive</label>
            <select className="field" value={buyIdx} onChange={(e) => setBuyIdx(Number(e.target.value))}>
              {tokens.map((t, i) => <option key={i} value={i}>{t.symbol} · {t.name}</option>)}
            </select>
          </Card>
          <Card className="mb-4">
            <label className="muted mb-1 block text-xs font-bold uppercase">Slippage %</label>
            <Field value={slippage} onChange={(e) => setSlippage(e.target.value)} inputMode="decimal" />
            <p className="muted m-0 mt-2 text-xs">Network: {pack.label}. Quotes via 0x when available.</p>
          </Card>
          {quoteInfo ? <Card className="mb-3"><p className="m-0 text-sm text-[var(--mint)]">{quoteInfo}</p></Card> : null}
          {err ? <p className="mb-3 text-sm text-[var(--red)]">{err}</p> : null}
          {hash ? <a href={`${pack.explorer}/tx/${hash}`} target="_blank" rel="noreferrer" className="mb-3 block text-sm font-bold">View tx →</a> : null}
          <div className="flex flex-col gap-2">
            <Button variant="secondary" disabled={busy} onClick={() => void getQuote()}>{busy ? '…' : 'Get quote'}</Button>
            <Button disabled={busy || !amount} onClick={() => void executeSwap()}>{busy ? 'Working…' : 'Swap'}</Button>
          </div>
        </>
      )}
    </div>
  )
}
