"""The package's version, read from its installed metadata (one source: pyproject.toml)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("earningspy-glass")
except PackageNotFoundError:      # a source tree that was never installed
    __version__ = "0.0.0.dev0"
