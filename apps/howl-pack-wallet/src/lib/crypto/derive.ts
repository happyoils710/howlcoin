import { HDKey } from '@scure/bip32'
import { generateMnemonic, mnemonicToSeedSync, validateMnemonic } from '@scure/bip39'
import { wordlist } from '@scure/bip39/wordlists/english'
import { privateKeyToAccount, type PrivateKeyAccount } from 'viem/accounts'
import { bytesToHex, type Hex, type Address } from 'viem'

export function ethPath(index = 0): string {
  return `m/44'/60'/0'/0/${index}`
}

export function createMnemonic(strength: 128 | 256 = 128): string {
  return generateMnemonic(wordlist, strength)
}

export function isValidMnemonic(phrase: string): boolean {
  return validateMnemonic(normalizeMnemonic(phrase), wordlist)
}

export function normalizeMnemonic(phrase: string): string {
  return phrase.trim().toLowerCase().replace(/\s+/g, ' ')
}

export interface DerivedAccount {
  index: number
  path: string
  address: Address
  privateKey: Hex
  account: PrivateKeyAccount
}

export function deriveEthAccount(mnemonic: string, index = 0, passphrase = ''): DerivedAccount {
  const norm = normalizeMnemonic(mnemonic)
  if (!validateMnemonic(norm, wordlist)) throw new Error('Invalid recovery phrase')
  const seed = mnemonicToSeedSync(norm, passphrase)
  const root = HDKey.fromMasterSeed(seed)
  const path = ethPath(index)
  const child = root.derive(path)
  if (!child.privateKey) throw new Error('Failed to derive private key')
  const privateKey = bytesToHex(child.privateKey) as Hex
  const account = privateKeyToAccount(privateKey)
  return { index, path, address: account.address, privateKey, account }
}

export function shortAddress(addr: string, n = 4): string {
  if (!addr || addr.length < 10) return addr || '—'
  return `${addr.slice(0, 2 + n)}…${addr.slice(-n)}`
}
