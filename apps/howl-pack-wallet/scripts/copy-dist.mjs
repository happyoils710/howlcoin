import { cpSync, mkdirSync, rmSync, existsSync, writeFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const dist = join(root, 'dist')
// Prefer Desktop howlcoin when available
const destCandidates = [
  join(root, '../../Desktop/howlcoin/assets/pack-wallet'),
  join(process.env.HOME || '', 'Desktop/howlcoin/assets/pack-wallet'),
  join(root, 'pack-wallet-dist'),
]

if (!existsSync(dist)) {
  console.error('dist/ missing — run vite build first')
  process.exit(1)
}

let dest = destCandidates[2]
for (const d of destCandidates) {
  try {
    mkdirSync(dirname(d), { recursive: true })
    dest = d
    break
  } catch { /* try next */ }
}

rmSync(dest, { recursive: true, force: true })
mkdirSync(dest, { recursive: true })
cpSync(dist, dest, { recursive: true })
writeFileSync(join(dest, '.gitkeep'), '')
console.log('Copied dist →', dest)
