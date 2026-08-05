import { parseUnits, type Address, type Hex } from 'viem'
import { createWalletClient, http } from 'viem'
import { getPublicClient, PACK_CHAINS, type ChainKey } from '@/lib/chains'
import { ERC20_ABI, type TokenDef } from '@/lib/tokens'
import { pushTx, updateTxStatus } from '@/lib/txlog'
import { useWallet } from '@/stores/wallet'

export async function sendToken(opts: {
  token: TokenDef
  to: Address
  amount: string
  chainKey: ChainKey
  customRpc?: string | null
}): Promise<Hex> {
  const derived = useWallet.getState().derived
  if (!derived) throw new Error('Wallet locked')
  const pack = PACK_CHAINS[opts.chainKey]
  const rpc = opts.customRpc || pack.defaultRpc
  const publicClient = getPublicClient(opts.chainKey, opts.customRpc)
  const walletClient = createWalletClient({
    account: derived.account, chain: pack.chain, transport: http(rpc),
  })

  let hash: Hex
  if (opts.token.address === 'native') {
    hash = await walletClient.sendTransaction({
      to: opts.to, value: parseUnits(opts.amount, opts.token.decimals),
      chain: pack.chain, account: derived.account,
    })
  } else {
    hash = await walletClient.writeContract({
      address: opts.token.address, abi: ERC20_ABI, functionName: 'transfer',
      args: [opts.to, parseUnits(opts.amount, opts.token.decimals)],
      chain: pack.chain, account: derived.account,
    })
  }

  pushTx({
    hash, chainId: pack.chain.id, from: derived.address, to: opts.to, value: opts.amount,
    label: `Sent ${opts.amount} ${opts.token.symbol}`, status: 'pending',
    timestamp: Date.now(), kind: 'send',
  })
  publicClient.waitForTransactionReceipt({ hash })
    .then((r) => updateTxStatus(hash, r.status === 'success' ? 'confirmed' : 'failed'))
    .catch(() => updateTxStatus(hash, 'failed'))
  return hash
}
