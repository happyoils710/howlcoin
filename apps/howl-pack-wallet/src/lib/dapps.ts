export type DappCategory = 'defi' | 'nft' | 'social' | 'games' | 'tools'

export interface DappEntry {
  id: string
  name: string
  description: string
  url: string
  category: DappCategory
  chains: string[]
}

export const DAPPS: DappEntry[] = [
  { id: 'uniswap-base', name: 'Uniswap', description: 'Swap tokens on Base', url: 'https://app.uniswap.org/swap?chain=base', category: 'defi', chains: ['base'] },
  { id: 'aerodrome', name: 'Aerodrome', description: 'Base-native DEX & liquidity', url: 'https://aerodrome.finance/', category: 'defi', chains: ['base'] },
  { id: 'basescan', name: 'Basescan', description: 'Block explorer for Base', url: 'https://basescan.org/', category: 'tools', chains: ['base'] },
  { id: 'opensea-base', name: 'OpenSea', description: 'NFTs on Base & beyond', url: 'https://opensea.io/', category: 'nft', chains: ['base', 'ethereum'] },
  { id: 'zora', name: 'Zora', description: 'Create & collect onchain media', url: 'https://zora.co/', category: 'nft', chains: ['base'] },
  { id: 'farcaster', name: 'Warpcast', description: 'Farcaster social client', url: 'https://warpcast.com/', category: 'social', chains: ['base'] },
  { id: 'parallel', name: 'Parallel', description: 'Onchain sci-fi card game', url: 'https://parallel.life/', category: 'games', chains: ['base', 'ethereum'] },
  { id: 'mint-fun', name: 'mint.fun', description: 'Discover NFT mints', url: 'https://mint.fun/', category: 'nft', chains: ['base'] },
  { id: 'lifi', name: 'LI.FI', description: 'Bridge & swap across chains', url: 'https://jumper.exchange/', category: 'defi', chains: ['base', 'ethereum'] },
  { id: 'howlscan', name: 'Howlscan', description: 'Howlcoin explorer · City · Charts', url: 'https://howlscan.org/', category: 'tools', chains: ['howl'] },
  { id: 'howl-app', name: 'Howl L1 Wallet', description: 'Classic multi-chain app · HOWL · SOL', url: 'https://howlscan.org/app', category: 'tools', chains: ['howl'] },
]

export const CATEGORY_LABELS: Record<DappCategory, string> = {
  defi: 'DeFi', nft: 'NFTs', social: 'Social', games: 'Games', tools: 'Tools',
}
