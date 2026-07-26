"""OpenManus deployment CLI."""

from importlib.metadata import PackageNotFoundError, version


try:
    __version__ = version("openmanusv2")
except PackageNotFoundError:
    __version__ = "2.2.1"
