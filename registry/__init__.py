"""AWS Agent Registry client and local record definitions."""

from .client import RegistryClient, RegistryError, RegistryRecord

from .discovery import RegistryDiscoveryError, RegistryResolver

__all__ = [
    "RegistryClient",
    "RegistryError",
    "RegistryRecord",
    "RegistryDiscoveryError",
    "RegistryResolver",
]
