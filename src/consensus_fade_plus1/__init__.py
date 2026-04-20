"""Consensus-Fade +1 Offset strategy — live execution package.

See STRATEGY.md in this folder for the full design doc.

Public API:
- `strategy.Recommendation` — one trade recommendation
- `strategy.build_recommendations(date)` — compute today's list
- `cli.main` — CLI entry point (via `uv run cfp`)
"""
from consensus_fade_plus1.strategy import Recommendation, build_recommendations

__all__ = ["Recommendation", "build_recommendations"]
