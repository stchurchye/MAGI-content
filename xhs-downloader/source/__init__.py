from .application import XHS
from .module import Settings

__all__ = [
    "XHS",
    "XHSDownloader",
    "cli",
    "Settings",
]


def __getattr__(name: str):
    if name == "XHSDownloader":
        from .TUI import XHSDownloader
        return XHSDownloader
    if name == "cli":
        from .CLI import cli
        return cli
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
