"""Agent Chaos package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agent-chaos")
except PackageNotFoundError:
    __version__ = "0.1.0"

__all__ = ["__version__"]
