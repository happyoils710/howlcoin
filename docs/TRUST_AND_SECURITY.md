# Howlcoin — Trust & Security

This document is the **public trust statement** for Howlcoin (HOWL), Howlscan, the web wallet, and **wHOWL** (SPL).

> Honest framing: trust is earned with **architecture, transparency, process, and third-party review** — not marketing.  
> A formal external audit is **not complete** until a report is published under `docs/security/audits/`.

## Trust model (what users actually trust)

| Surface | Who holds power | Risk if compromised |
|---------|-----------------|---------------------|
| **Native HOWL (L1)** | Miners + your seed/keys | Consensus / your wallet |
| **Web wallet (`/app`)** | Your device (keys encrypted with PIN) | XSS, malware, phishing |
| **wHOWL mint authority** | Solana key on operator host | Unlimited mint / supply inflation |
| **Howl Swap / Wrap relayers** | Hot wallets + deposit detection | Stolen deposits or unpaid mints |
| **howlscan.org** | VPS + DNS (Cloudflare) | Phishing / API spoofing |

**Semi-custodial bridges are not trustless.** Until a DEX pool or multi-sig/timelocked mint is live, wrap/swap require trusting the mint authority and hot wallets.

## What “trusted token” means for HOWL / wHOWL

1. **Transparent mint authority** — address published; freeze authority none  
2. **Documented threat model** — see [THREAT_MODEL.md](./security/THREAT_MODEL.md)  
3. **Vulnerability disclosure** — [SECURITY.md](../SECURITY.md)  
4. **Security headers & wallet hygiene** — CSP, no emoji noise, clear semi-custodial warnings  
5. **Audit-ready scope** — [SECURITY_AUDIT_SCOPE.md](./security/SECURITY_AUDIT_SCOPE.md)  
6. **Published audit reports** (when funded) — `docs/security/audits/`  
7. **Operational controls** — key permissions, rate limits, monitoring  

## wHOWL (SPL) facts (verify yourself)

| Field | Value |
|-------|--------|
| Mint | `HYRKhV2Y9HEtKCCHSgH18Zfo4U9Ln9vAg2dCmBJSLWaG` |
| Decimals | 8 |
| Freeze authority | **None** (cannot freeze user accounts) |
| Mint authority | Solana treasury (operator-controlled until renounced or multi-sig) |
| Metadata | Metaplex; URI `https://howlscan.org/assets/whowl-token.json` |
| Explorer | https://solscan.io/token/HYRKhV2Y9HEtKCCHSgH18Zfo4U9Ln9vAg2dCmBJSLWaG |

**Verify:** Solana CLI `spl-token display <mint>` or Solscan “Authorities”.

## Roadmap to higher trust (ordered)

| Phase | Action | Trust gain | Typical cost |
|-------|--------|------------|--------------|
| **0** | This public docs + headers + disclosure | Clarity | Done in-repo |
| **1** | Multi-sig mint authority (e.g. Squads 2-of-3) | No single laptop mint | Low–med |
| **2** | Timelock / mint rate limit program | Bounds inflation | Med |
| **3** | DEX liquidity (wHOWL/SOL) | Exit without desk | Liquidity capital |
| **4** | Independent wallet + bridge review | Professional signal | ~$15k–80k |
| **5** | Full consensus / crypto review | High assurance | ~$80k–250k+ |
| **6** | Bug bounty (Immunefi / Cantina) | Continuous pressure | Pool + ops |

## User security checklist

- Prefer **official** `https://howlscan.org/app` only — check the URL  
- Never type your **seed** into a website you didn’t open yourself  
- Enable **PIN + 2FA** in the wallet Security page  
- Treat wrap/swap as **semi-custodial** until multi-sig + audits land  
- Verify **mint address** before adding custom tokens  

## Operator security checklist

- Hot wallet keys `0600`, not world-readable  
- Separate mint authority from day trading wallet when possible  
- Monitor wrap/bridge orders and SOL treasury balance  
- Keep `HOWL_BRIDGE_ADMIN_SECRET` strong and rotated  
- Back up seed nodes offline  

## Contact

Vulnerability reports: see [SECURITY.md](../SECURITY.md).  
Public status: https://howlscan.org/#/security  
