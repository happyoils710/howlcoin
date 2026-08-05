import { base, baseSepolia, mainnet, optimism, arbitrum, type Chain } from 'viem/chains'
import { http, createPublicClient, type PublicClient } from 'viem'

export type ChainKey = 'base' | 'baseSepolia' | 'ethereum' | 'optimism' | 'arbitrum'

export interface PackChain {
  key: ChainKey
  chain: Chain
  label: string
  short: string
  isTestnet?: boolean
  defaultRpc: string
  explorer: string
  nativeSymbol: string
}

const envRpc = (key: string, fallback: string) => {
  const v = (import.meta.env as Record<string, string | undefined>)[key]
  return v && v.length > 8 ? v : fallback
}

export const PACK_CHAINS: Record<ChainKey, PackChain> = {
  base: {
    key: 'base', chain: base, label: 'Base', short: 'Base',
    defaultRpc: envRpc('VITE_BASE_RPC', 'https://mainnet.base.org'),
    explorer: 'https://basescan.org', nativeSymbol: 'ETH',
  },
  baseSepolia: {
    key: 'baseSepolia', chain: baseSepolia, label: 'Base Sepolia', short: 'Base · test', isTestnet: true,
    defaultRpc: 'https://sepolia.base.org', explorer: 'https://sepolia.basescan.org', nativeSymbol: 'ETH',
  },
  ethereum: {
    key: 'ethereum', chain: mainnet, label: 'Ethereum', short: 'ETH',
    defaultRpc: 'https://cloudflare-eth.com', explorer: 'https://etherscan.io', nativeSymbol: 'ETH',
  },
  optimism: {
    key: 'optimism', chain: optimism, label: 'Optimism', short: 'OP',
    defaultRpc: 'https://mainnet.optimism.io', explorer: 'https://optimistic.etherscan.io', nativeSymbol: 'ETH',
  },
  arbitrum: {
    key: 'arbitrum', chain: arbitrum, label: 'Arbitrum', short: 'Arb',
    defaultRpc: 'https://arb1.arbitrum.io/rpc', explorer: 'https://arbiscan.io', nativeSymbol: 'ETH',
  },
}

export const DEFAULT_CHAIN_KEY: ChainKey = 'base'
const clientCache = new Map<string, PublicClient>()

export function getRpcUrl(key: ChainKey, custom?: string | null): string {
  if (custom && custom.startsWith('http')) return custom
  return PACK_CHAINS[key].defaultRpc
}

export function getPublicClient(key: ChainKey, customRpc?: string | null): PublicClient {
  const rpc = getRpcUrl(key, customRpc)
  const cacheKey = `${key}:${rpc}`
  let c = clientCache.get(cacheKey)
  if (!c) {
    c = createPublicClient({ chain: PACK_CHAINS[key].chain, transport: http(rpc, { timeout: 20_000 }) })
    clientCache.set(cacheKey, c)
  }
  return c
}
