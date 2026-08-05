import type { Address, Hex } from 'viem'

const ZEROX = 'https://api.0x.org'

export interface SwapQuoteRequest {
  chainId: number
  sellToken: string
  buyToken: string
  sellAmount: string
  taker: Address
  slippageBps?: number
}

export interface SwapQuote {
  buyAmount: string
  sellAmount: string
  price: string
  estimatedGas?: string
  allowanceTarget?: Address
  transaction?: { to: Address; data: Hex; value: string; gas?: string }
  raw: unknown
}

export async function fetch0xQuote(req: SwapQuoteRequest): Promise<SwapQuote> {
  const params = new URLSearchParams({
    chainId: String(req.chainId),
    sellToken: req.sellToken,
    buyToken: req.buyToken,
    sellAmount: req.sellAmount,
    taker: req.taker,
    slippageBps: String(req.slippageBps ?? 50),
  })
  const headers: Record<string, string> = { Accept: 'application/json', '0x-version': 'v2' }
  const key = import.meta.env.VITE_ZEROX_API_KEY as string | undefined
  if (key) headers['0x-api-key'] = key
  const res = await fetch(`${ZEROX}/swap/allowance-holder/quote?${params}`, { headers })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`Swap quote failed (${res.status}): ${text.slice(0, 180) || res.statusText}`)
  }
  const raw = await res.json()
  const tx = raw.transaction || raw
  return {
    buyAmount: String(raw.buyAmount ?? '0'),
    sellAmount: String(raw.sellAmount ?? req.sellAmount),
    price: String(raw.price ?? ''),
    estimatedGas: raw.transaction?.gas ? String(raw.transaction.gas) : undefined,
    allowanceTarget: (raw.allowanceTarget || raw.issues?.allowance?.spender) as Address | undefined,
    transaction: tx?.to
      ? { to: tx.to as Address, data: (tx.data || '0x') as Hex, value: String(tx.value || '0'), gas: tx.gas ? String(tx.gas) : undefined }
      : undefined,
    raw,
  }
}

export function jumperBridgeUrl(): string {
  return 'https://jumper.exchange/?fromChain=8453&toChain=1'
}
