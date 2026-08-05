import type { Address } from 'viem'
import type { ChainKey } from './chains'

export interface TokenDef {
  symbol: string
  name: string
  decimals: number
  address: Address | 'native'
  chainKey: ChainKey
  coingeckoId?: string
}

export const CURATED_TOKENS: TokenDef[] = [
  { symbol: 'ETH', name: 'Ether', decimals: 18, address: 'native', chainKey: 'base', coingeckoId: 'ethereum' },
  { symbol: 'USDC', name: 'USD Coin', decimals: 6, address: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', chainKey: 'base', coingeckoId: 'usd-coin' },
  { symbol: 'WETH', name: 'Wrapped Ether', decimals: 18, address: '0x4200000000000000000000000000000000000006', chainKey: 'base', coingeckoId: 'weth' },
  { symbol: 'ETH', name: 'Ether', decimals: 18, address: 'native', chainKey: 'ethereum', coingeckoId: 'ethereum' },
  { symbol: 'ETH', name: 'Ether', decimals: 18, address: 'native', chainKey: 'optimism', coingeckoId: 'ethereum' },
  { symbol: 'ETH', name: 'Ether', decimals: 18, address: 'native', chainKey: 'arbitrum', coingeckoId: 'ethereum' },
  { symbol: 'ETH', name: 'Ether (test)', decimals: 18, address: 'native', chainKey: 'baseSepolia', coingeckoId: 'ethereum' },
]

export const ERC20_ABI = [
  { type: 'function', name: 'balanceOf', stateMutability: 'view', inputs: [{ name: 'account', type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', name: 'transfer', stateMutability: 'nonpayable', inputs: [{ name: 'to', type: 'address' }, { name: 'amount', type: 'uint256' }], outputs: [{ type: 'bool' }] },
  { type: 'function', name: 'allowance', stateMutability: 'view', inputs: [{ name: 'owner', type: 'address' }, { name: 'spender', type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', name: 'approve', stateMutability: 'nonpayable', inputs: [{ name: 'spender', type: 'address' }, { name: 'amount', type: 'uint256' }], outputs: [{ type: 'bool' }] },
] as const
