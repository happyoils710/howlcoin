# Security policy

## Supported projects

- **Howlcoin** node, explorer, and public wallet (`howl/`, `assets/`)
- **howlscan.org** public explorer and web wallet

## Reporting a vulnerability

Please **do not** open a public issue for sensitive reports.

1. Prefer **[GitHub private security advisories](https://github.com/happyoils710/howlcoin/security/advisories/new)** for the `happyoils710/howlcoin` repository.
2. If that is unavailable, open a **private** discussion with maintainers or a minimal public issue that does **not** include exploit details.

Include when possible:

- Affected component (node, explorer, wallet, bridge, etc.)
- Version / commit / height if known
- Steps to reproduce
- Impact (funds at risk, DoS, privacy, etc.)
- Suggested fix if you have one

## Scope

**In scope (examples):** remote code execution, unauthorized fund movement, consensus-breaking bugs, wallet key leakage, serious DoS on public seed/explorer, XSS/CSRF on howlscan.org that can steal funds or keys.

**Out of scope (examples):** social engineering, physical attacks, third-party RPC/CDN outages, spam, and theoretical issues without a realistic path to impact.

## Disclosure

We aim to acknowledge reports promptly and coordinate disclosure after a fix is available when the issue is confirmed. Thank you for helping keep Howlcoin safer.
