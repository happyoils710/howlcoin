# Link click counters & monetization (Howlscan)

Howlscan counts **aggregate** clicks on site links. No visitor IPs or personal data are stored.

## Live pages

| URL | Purpose |
|-----|---------|
| https://howlscan.org/clicks | Public counter dashboard |
| `GET /api/public/clicks` | JSON stats |
| `POST /api/public/click` | Record a click `{id, href, kind, monetize}` |
| `GET /r/{id}` | Count + **302 redirect** to registered destination |

## How tracking works

1. **Every `<a href>`** on Howlscan fires a lightweight click beacon (except `/r/` which is counted on the server).
2. Links with **`data-click-id="my_id"`** get a small **click badge** with the running total.
3. **Monetized / affiliate** partners should use **`/r/{id}`** so every hop is counted even if the client blocks beacons.

## Add a paid partner link

On the seed VPS (or local data dir), create:

```json
/* /var/lib/howlcoin/click_links.json */
{
  "links": {
    "partner_x": {
      "href": "https://partner.example/?ref=howlcoin",
      "label": "Partner X",
      "kind": "sponsored",
      "monetize": true,
      "cpc_usd": 0.15
    }
  }
}
```

Or env (JSON object):

```bash
export HOWL_CLICK_LINKS='{"partner_x":{"href":"https://…","label":"Partner X","monetize":true,"cpc_usd":0.15}}'
```

Then in the site:

```html
<a href="/r/partner_x" data-click-id="partner_x" data-monetize="1">Partner X</a>
```

Estimated revenue on `/clicks` = sum of `cpc_usd × clicks` for monetized ids (you still settle with partners yourself).

## Storage

| File | Default path |
|------|----------------|
| Counters | `/var/lib/howlcoin/click_stats.json` |
| Link registry | `/var/lib/howlcoin/click_links.json` |

Override with `HOWL_CLICK_STATS` / `HOWL_CLICK_LINKS_JSON`.

## Honest limits

- Counters are **not** ad-network grade (bots, multi-device, ad-block).
- Use for **sponsored placements**, **affiliate URLs**, and **product analytics** — not for secretly hijacking every outbound URL.
- Disclose sponsored links when required by platform rules.
