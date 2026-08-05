"""DePIN and decentralized compute market adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from .akash import AkashProvider
from .base import ComputeProvider, ComputeQuote, DeployJob
from .local import LocalProvider
from .nosana import NosanaProvider


def build_providers(work_dir: Path, howl_root: Optional[Path] = None) -> Dict[str, ComputeProvider]:
    work_dir = Path(work_dir)
    return {
        "local": LocalProvider(work_dir / "local", howl_root=howl_root),
        "akash": AkashProvider(work_dir / "akash"),
        "nosana": NosanaProvider(work_dir / "nosana"),
    }


def best_quote(
    providers: Dict[str, ComputeProvider],
    *,
    prefer: Optional[List[str]] = None,
) -> ComputeQuote:
    prefer = prefer or ["local", "akash", "nosana"]
    quotes = []
    for name in prefer:
        p = providers.get(name)
        if not p:
            continue
        try:
            quotes.append(p.quote())
        except Exception:
            continue
    if not quotes:
        return ComputeQuote(
            provider="none",
            region="n/a",
            cpu=0,
            memory_gb=0,
            storage_gb=0,
            cost_howl_per_day=0,
            available=False,
        )
    # Prefer free local, else cheapest
    quotes.sort(key=lambda q: (0 if q.provider == "local" else 1, q.cost_howl_per_day))
    return quotes[0]


__all__ = [
    "ComputeProvider",
    "ComputeQuote",
    "DeployJob",
    "LocalProvider",
    "AkashProvider",
    "NosanaProvider",
    "build_providers",
    "best_quote",
]
