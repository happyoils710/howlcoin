# Security policy

## Supported projects

- **Howlcoin** node, explorer, and public wallet (`howl/`, `assets/`)
- **howlscan.org** public explorer and web wallet
- **wHOWL** SPL mint configuration and wrap/bridge relayers

Public trust overview: [docs/TRUST_AND_SECURITY.md](docs/TRUST_AND_SECURITY.md)  
Threat model: [docs/security/THREAT_MODEL.md](docs/security/THREAT_MODEL.md)  
Audit scope: [docs/security/SECURITY_AUDIT_SCOPE.md](docs/security/SECURITY_AUDIT_SCOPE.md)

## Reporting a vulnerability

Please **do not** open a public issue for sensitive reports.

1. Prefer **[GitHub private security advisories](https://github.com/happyoils710/howlcoin/security/advisories/new)** for `happyoils710/howlcoin`.
2. If unavailable, contact maintainers privately with a minimal public issue that does **not** include exploit details.

### Please include

- Affected component (node, explorer, wallet, bridge, wrap, mint)
- Version / commit / chain height if known
- Steps to reproduce
- Impact (funds at risk, DoS, privacy, key leakage)
- Suggested fix if you have one

### Response targets (best-effort)

| Severity | Acknowledge | Initial assessment |
|----------|-------------|--------------------|
| Critical (funds at risk) | 24–48h | 72h |
| High | 72h | 1 week |
| Medium / Low | 1 week | 2 weeks |

We may ship hotfixes before full public disclosure. Coordinated disclosure preferred.

## Scope

**In scope:** RCE, unauthorized fund movement, consensus-breaking bugs, wallet key leakage, serious DoS on public seed/explorer, XSS/CSRF on howlscan.org that can steal funds or keys, wrap/bridge double-spend or free mint.

**Out of scope:** social engineering, physical attacks, third-party RPC/CDN outages, spam, pure UI nits, theoretical issues without realistic impact.

## Bug bounty (status)

**No formal bounty program is funded yet.**  
Valid Critical/High findings that protect user funds will be considered for discretionary rewards and public credit (with permission).

To launch a formal program, maintainers should fund a pool and publish rules under `docs/security/bounty.md`.

## Safe harbor

We will not pursue legal action against good-faith research that:

- Avoids privacy violations and destruction of data  
- Avoids mainnet disruption beyond minimal PoC  
- Reports promptly via private channels  
- Gives reasonable time to fix before disclosure  

## Disclosure

We aim to acknowledge reports promptly and coordinate disclosure after a fix is available when the issue is confirmed. Thank you for helping keep Howlcoin safer.
