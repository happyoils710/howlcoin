# Security audit scope (for independent auditors)

**Repository:** https://github.com/happyoils710/howlcoin  
**Primary language:** Python (node/consensus/explorer), TypeScript/JS (wallet)  
**Networks:** Howl L1 (Scrypt PoW), Solana (wHOWL SPL + bridge deposits)

## In scope

### A. Consensus & node (`howl/blockchain.py`, `howl/network.py`, `howl/scrypt_pow.py`, `howl/crypto.py`)

- Block/tx validation, difficulty, coinbase, signatures  
- Fork choice / reorg handling  
- Mempool rules, fee/nonce  
- P2P message injection / DoS  

### B. Web wallet (`assets/public-wallet.html`, `assets/howl-crypto.mjs`, related modules)

- Key generation, BIP39, encryption-at-rest (PIN/AES-GCM)  
- XSS → key exfiltration  
- Transaction construction / signing  
- Web3 / WalletConnect surfaces  

### C. Explorer (`howl/explorer.py`)

- Auth sessions, XSS/CSRF  
- Path traversal on `/assets/`, `/media/`  
- Admin/bridge/wrap endpoints  

### D. Bridge & wrap (`howl/bridge.py`, `howl/wrap.py`, `scripts/howl-*-relayer.py`)

- Order matching, double-pay, amount precision  
- Deposit spoofing / race conditions  
- Hot wallet / mint authority misuse paths  

### E. Solana mint configuration

- Mint authority control, freeze authority, metadata mutability  
- Whether metadata update authority is appropriately restricted  

## Out of scope

- Physical security, social engineering  
- Third-party RPCs (Solana public RPC), Cloudflare  
- Economic attacks requiring majority hashpower (document only)  

## Deliverables expected

1. Written report (PDF) with severity ratings (Critical/High/Medium/Low/Informational)  
2. PoC where applicable  
3. Fix verification pass after remediations  
4. Public summary suitable for `docs/security/audits/`  

## Suggested firms / platforms (not endorsements)

Trail of Bits, OpenZeppelin, OtterSec, Neodyme, Ackee, Halborn, Quantstamp, Spearbit/Cantina, Code4rena, Immunefi (bounty).

## Budget guidance (ballpark USD, 2025–2026)

| Package | Scope | Ballpark |
|---------|-------|----------|
| Wallet-only review | B | $15k–40k |
| Bridge + wrap + mint ops | D + E | $25k–75k |
| Full stack | A–E | $80k–250k+ |

## Contact for auditors

Open a private security advisory on the GitHub repository, or contact maintainers via the process in `SECURITY.md`.
