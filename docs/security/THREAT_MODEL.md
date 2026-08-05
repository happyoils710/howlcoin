# Howlcoin threat model

## Assets

1. User private keys / mnemonics (browser wallet)  
2. Native HOWL balances on L1  
3. wHOWL mint authority (Solana)  
4. Bridge / wrap hot HOWL inventory  
5. Solana deposit treasury (SOL/USDC/wHOWL)  
6. Explorer integrity (users trust displayed balances/tx)  

## Adversaries

- Remote attacker on the public internet  
- Malicious webpage / XSS  
- Compromised VPS  
- Malicious peer (P2P)  
- Insider with host access  

## Key threats & mitigations

| Threat | Mitigation (current) | Next |
|--------|----------------------|------|
| XSS steals keys | Wallet keys in encrypted vault; CSP headers | CSP tighten + SRI |
| Phishing site | User education; fixed domain | Certificate pinning (native app) |
| Rogue mint | Freeze authority off; public mint addr | Multi-sig mint authority |
| Relayer theft | Semi-custodial disclaimer; ops keys | Multi-sig + cold inventory |
| Consensus bugs | Small open-source codebase | External audit |
| API abuse | Stateless public API | Rate limits / WAF |
| Supply chain | Pinned deps where possible | Dependabot / SBOM |

## Non-goals

- Perfect anonymity (not Monero)  
- Trustless cross-chain until designed and audited  
- Guaranteeing third-party RPC honesty  

## Residual risk statement

Until mint authority is multi-sig/timelocked **and** independent audits are published, treat **wHOWL and bridges as higher risk** than native self-custody HOWL mining + holding.
