"""Shared evidence workspace for multi-agent findings and hypotheses."""

from .store import EvidenceStore, EvidenceStoreError

__all__ = ["EvidenceStore", "EvidenceStoreError"]
