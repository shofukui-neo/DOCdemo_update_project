"""Site-specific extraction strategies."""

from .base import DiscoveryRecord
from .hellowork import discover_from_hellowork
from .pr_times import discover_from_pr_times
from .sns_discovery import discover_from_sns
from .wantedly import discover_from_wantedly

__all__ = [
    "DiscoveryRecord",
    "discover_from_wantedly",
    "discover_from_pr_times",
    "discover_from_hellowork",
    "discover_from_sns",
]
