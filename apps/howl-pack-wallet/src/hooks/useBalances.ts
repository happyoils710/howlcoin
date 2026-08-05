import { useQuery } from '@tanstack/react-query'
import { formatUnits, type Address } from 'viem'
import { getPublicClient } from '@/lib/chains'
import { CURATED_TOKENS, ERC20_ABI, type TokenDef } from '@/lib/tokens'
import { fetchUsdPrices, formatTokenAmount, formatUsd } from '@/lib/prices'
import { useSettings } from '@/stores/settings'
import { useWallet } from '@/stores/wallet'

export interface TokenBalanceRow {
  token: TokenDef
  raw: bigint
  amount: string
  usd: number
  usdLabel: string
  price: number
}

async function readBalance(token: TokenDef, owner: Address, rpc?: string | null): Promise<bigint> {
  const client = getPublicClient(token.chainKey, rpc)
  if (token.address === 'native') return client.getBalance({ address: owner })
  return client.readContract({
    address: token.address, abi: ERC20_ABI, functionName: 'balanceOf', args: [owner],
  }) as Promise<bigint>
}

export function useBalances() {
  const address = useWallet((s) => s.derived?.address)
  const chainKey = useSettings((s) => s.chainKey)
  const customRpcs = useSettings((s) => s.customRpcs)

  return useQuery({
    queryKey: ['balances', address, chainKey, customRpcs[chainKey]],
    enabled: !!address,
    refetchInterval: 30_000,
    queryFn: async (): Promise<{ rows: TokenBalanceRow[]; totalUsd: number }> => {
      if (!address) return { rows: [], totalUsd: 0 }
      const tokens = CURATED_TOKENS.filter((t) => t.chainKey === chainKey)
      const prices = await fetchUsdPrices(tokens.map((t) => t.coingeckoId || '').filter(Boolean))
      const rows: TokenBalanceRow[] = []
      let totalUsd = 0
      for (const token of tokens) {
        try {
          const raw = await readBalance(token, address, customRpcs[token.chainKey])
          const price = token.coingeckoId ? prices[token.coingeckoId] || 0 : 0
          const human = Number(formatUnits(raw, token.decimals))
          const usd = human * price
          totalUsd += usd
          rows.push({
            token, raw, amount: formatTokenAmount(raw, token.decimals),
            usd, usdLabel: formatUsd(usd), price,
          })
        } catch {
          rows.push({
            token, raw: 0n, amount: '0', usd: 0, usdLabel: formatUsd(0),
            price: token.coingeckoId ? prices[token.coingeckoId] || 0 : 0,
          })
        }
      }
      rows.sort((a, b) => b.usd - a.usd)
      return { rows, totalUsd }
    },
  })
}
