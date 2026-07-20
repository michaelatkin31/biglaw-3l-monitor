"""Fetcher registry.

One fetcher per ATS backend (the core architecture principle: classify each
firm by ATS once, maintain one fetcher per ATS -- never one scraper per firm).

`get_fetcher(ats_type)` returns the fetcher instance for a given ats_type, or
None for unknown/unsupported types (main.py skips those without crashing).
"""

from __future__ import annotations

from typing import Optional

from core.http import HttpClient

from .ashby import AshbyFetcher
from .base import Fetcher
from .careerpage import CareerPageFetcher
from .generic import GenericFetcher
from .greenhouse import GreenhouseFetcher
from .lever import LeverFetcher
from .radancy import RadancyFetcher
from .smartrecruiters import SmartRecruitersFetcher
from .virecruit import ViRecruitFetcher
from .workday import WorkdayFetcher


def build_registry(client: HttpClient) -> dict[str, Fetcher]:
    return {
        "greenhouse": GreenhouseFetcher(client),
        "lever": LeverFetcher(client),
        "workday": WorkdayFetcher(client),
        "generic": GenericFetcher(client),
        "careerpage": CareerPageFetcher(client),
        "smartrecruiters": SmartRecruitersFetcher(client),
        "virecruit": ViRecruitFetcher(client),
        "radancy": RadancyFetcher(client),
        "ashby": AshbyFetcher(client),
    }


def get_fetcher(registry: dict[str, Fetcher], ats_type: str) -> Optional[Fetcher]:
    return registry.get((ats_type or "").lower())


__all__ = [
    "Fetcher",
    "GreenhouseFetcher",
    "LeverFetcher",
    "WorkdayFetcher",
    "GenericFetcher",
    "CareerPageFetcher",
    "SmartRecruitersFetcher",
    "ViRecruitFetcher",
    "RadancyFetcher",
    "AshbyFetcher",
    "build_registry",
    "get_fetcher",
]
