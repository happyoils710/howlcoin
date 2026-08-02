"""
Howlscan link click counters + optional monetized redirects.

- Counts are aggregate only (no IPs, no cookies required).
- Monetized links use /r/{id} so every paid/outbound hop is counted server-side.
- Registry: HOWL_CLICK_LINKS_JSON or <data-dir>/click_links.json
- Counters: <data-dir>/click_stats.json
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

_LOCK = threading.RLock()

# Default catalog — add monetize:true + your affiliate URL when you have partners
DEFAULT_LINKS: Dict[str, Dict[str, Any]] = {
    "github": {
        "href": "https://github.com/happyoils710/howlcoin",
        "label": "GitHub / source",
        "kind": "external",
        "monetize": False,
    },
    "wallet_app": {
        "href": "/app",
        "label": "Web wallet",
        "kind": "internal",
        "monetize": False,
    },
    "whitepaper": {
        "href": "/whitepaper",
        "label": "White paper",
        "kind": "internal",
        "monetize": False,
    },
    "token_info": {
        "href": "/token",
        "label": "Token info",
        "kind": "internal",
        "monetize": False,
    },
    "run_node": {
        "href": "/#/run",
        "label": "Run a node",
        "kind": "internal",
        "monetize": False,
    },
    "howlscan_home": {
        "href": "/",
        "label": "Howlscan home",
        "kind": "internal",
        "monetize": False,
    },
    # Example monetized slot (disabled until you set a real partner URL):
    # "partner_demo": {
    #     "href": "https://example.com/?ref=howlcoin",
    #     "label": "Partner (demo)",
    #     "kind": "sponsored",
    #     "monetize": True,
    #     "cpc_usd": 0.0,
    # },
}

_ID_RE = re.compile(r"^[a-zA-Z0-9_\-.]{1,64}$")


def data_dir() -> Path:
    return Path(os.environ.get("HOWL_PUBLIC_DATA", "/var/lib/howlcoin"))


def stats_path() -> Path:
    custom = os.environ.get("HOWL_CLICK_STATS", "").strip()
    if custom:
        return Path(custom)
    return data_dir() / "click_stats.json"


def links_path() -> Path:
    custom = os.environ.get("HOWL_CLICK_LINKS_JSON", "").strip()
    if custom:
        return Path(custom)
    return data_dir() / "click_links.json"


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return default


def _save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def load_link_registry() -> Dict[str, Dict[str, Any]]:
    """Merge defaults with optional on-disk / env registry."""
    reg = {k: dict(v) for k, v in DEFAULT_LINKS.items()}
    # Env: HOWL_CLICK_LINKS='{"partner":{"href":"...","monetize":true}}'
    env_raw = os.environ.get("HOWL_CLICK_LINKS", "").strip()
    if env_raw:
        try:
            extra = json.loads(env_raw)
            if isinstance(extra, dict):
                for k, v in extra.items():
                    if isinstance(v, dict) and v.get("href"):
                        reg[str(k)] = {**reg.get(str(k), {}), **v}
        except json.JSONDecodeError:
            pass
    disk = _load_json(links_path(), {})
    if isinstance(disk, dict):
        # allow {"links": {...}} or flat map
        blob = disk.get("links") if isinstance(disk.get("links"), dict) else disk
        if isinstance(blob, dict):
            for k, v in blob.items():
                if isinstance(v, dict) and v.get("href"):
                    reg[str(k)] = {**reg.get(str(k), {}), **v}
    return reg


def _empty_stats() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated_at": 0,
        "total_clicks": 0,
        "by_id": {},  # id -> {clicks, last_ts, href?, kind?, monetize?}
        "by_href": {},  # normalized href -> clicks
        "by_kind": {"internal": 0, "external": 0, "sponsored": 0, "nav": 0, "other": 0},
    }


def load_stats() -> Dict[str, Any]:
    raw = _load_json(stats_path(), None)
    if not isinstance(raw, dict):
        return _empty_stats()
    base = _empty_stats()
    base.update({k: raw.get(k, base[k]) for k in base})
    if not isinstance(base["by_id"], dict):
        base["by_id"] = {}
    if not isinstance(base["by_href"], dict):
        base["by_href"] = {}
    if not isinstance(base["by_kind"], dict):
        base["by_kind"] = _empty_stats()["by_kind"]
    return base


def normalize_href(href: str) -> str:
    h = (href or "").strip()
    if not h:
        return ""
    if h.startswith("#"):
        return h.split("?")[0]
    if h.startswith("/"):
        return h.split("?")[0] or "/"
    try:
        u = urlparse(h)
        if u.scheme in ("http", "https"):
            path = u.path or "/"
            return f"{u.scheme}://{u.netloc}{path}".rstrip("/") or h
    except Exception:
        pass
    return h[:500]


def classify_href(href: str, site: str = "https://howlscan.org") -> str:
    h = (href or "").strip()
    if not h or h.startswith("javascript:"):
        return "other"
    if h.startswith("#") or h.startswith("/#") or h.startswith("/"):
        return "internal"
    try:
        host = urlparse(h).netloc.lower()
        site_host = urlparse(site).netloc.lower()
        if host and site_host and (host == site_host or host.endswith("." + site_host)):
            return "internal"
        if host:
            return "external"
    except Exception:
        pass
    return "other"


def record_click(
    *,
    link_id: Optional[str] = None,
    href: Optional[str] = None,
    kind: Optional[str] = None,
    monetize: Optional[bool] = None,
    source: str = "web",
) -> Dict[str, Any]:
    """Increment counters. Returns updated row for this id/href."""
    reg = load_link_registry()
    lid = (link_id or "").strip()
    if lid and not _ID_RE.match(lid):
        lid = ""

    reg_entry = reg.get(lid) if lid else None
    final_href = (href or (reg_entry or {}).get("href") or "").strip()
    norm = normalize_href(final_href)

    if kind:
        k = kind.strip().lower()
    elif reg_entry and reg_entry.get("kind"):
        k = str(reg_entry["kind"]).lower()
    else:
        k = classify_href(final_href)
    if k not in ("internal", "external", "sponsored", "nav", "other"):
        k = "other"

    if monetize is None:
        mon = bool((reg_entry or {}).get("monetize"))
    else:
        mon = bool(monetize)
    if mon:
        k = "sponsored"

    now = int(time.time())
    with _LOCK:
        stats = load_stats()
        stats["total_clicks"] = int(stats.get("total_clicks") or 0) + 1
        stats["updated_at"] = now
        bk = stats.setdefault("by_kind", {})
        bk[k] = int(bk.get(k) or 0) + 1

        if norm:
            bh = stats.setdefault("by_href", {})
            bh[norm] = int(bh.get(norm) or 0) + 1

        row_key = lid or (norm[:80] if norm else "unknown")
        by_id = stats.setdefault("by_id", {})
        row = by_id.get(row_key) if isinstance(by_id.get(row_key), dict) else {}
        row = {
            "id": row_key,
            "clicks": int(row.get("clicks") or 0) + 1,
            "last_ts": now,
            "href": final_href or row.get("href") or norm,
            "kind": k,
            "monetize": mon or bool(row.get("monetize")),
            "label": (reg_entry or {}).get("label") or row.get("label") or row_key,
            "source": source,
        }
        by_id[row_key] = row
        _save_json(stats_path(), stats)
        return {"ok": True, "total_clicks": stats["total_clicks"], "link": row}


def resolve_redirect(link_id: str) -> Optional[Dict[str, Any]]:
    """Look up monetized/catalog link for /r/{id}."""
    lid = (link_id or "").strip()
    if not _ID_RE.match(lid):
        return None
    reg = load_link_registry()
    entry = reg.get(lid)
    if not entry or not entry.get("href"):
        return None
    return {"id": lid, **entry}


def public_summary(limit: int = 50) -> Dict[str, Any]:
    stats = load_stats()
    reg = load_link_registry()
    rows: List[Dict[str, Any]] = []
    by_id = stats.get("by_id") or {}
    # ensure registered links appear even at 0 clicks
    ids = set(by_id.keys()) | set(reg.keys())
    for lid in ids:
        row = dict(by_id.get(lid) or {})
        meta = reg.get(lid) or {}
        rows.append(
            {
                "id": lid,
                "label": row.get("label") or meta.get("label") or lid,
                "href": row.get("href") or meta.get("href") or "",
                "clicks": int(row.get("clicks") or 0),
                "kind": row.get("kind") or meta.get("kind") or "other",
                "monetize": bool(row.get("monetize") or meta.get("monetize")),
                "last_ts": row.get("last_ts"),
                "cpc_usd": meta.get("cpc_usd"),
                "redirect": f"/r/{lid}" if meta.get("href") else None,
            }
        )
    rows.sort(key=lambda r: (-int(r.get("clicks") or 0), str(r.get("id"))))
    monetized = [r for r in rows if r.get("monetize")]
    est = 0.0
    for r in monetized:
        try:
            est += float(r.get("cpc_usd") or 0) * int(r.get("clicks") or 0)
        except (TypeError, ValueError):
            pass
    return {
        "total_clicks": int(stats.get("total_clicks") or 0),
        "updated_at": stats.get("updated_at"),
        "by_kind": stats.get("by_kind") or {},
        "links": rows[: max(1, min(int(limit), 200))],
        "monetized_links": monetized,
        "estimated_revenue_usd": round(est, 4),
        "note": "Aggregate click counts only. No visitor IPs stored. Monetize via /r/{id} + cpc_usd in click_links.json.",
    }
