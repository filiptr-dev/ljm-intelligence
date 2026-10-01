"""Integration ports — the Protocol surfaces core modules depend on.

Core modules import from here only; adapters live under `integrations/adapters/`
and may be ugly in their own way. See the plan for the full rationale.
"""

from app.integrations.ports.ai import AIProviderPort
from app.integrations.ports.email import EmailMailboxPort, EmailSenderPort
from app.integrations.ports.enrichment import EnrichmentPort
from app.integrations.ports.loadboard import LoadBoardPort

__all__ = [
    "AIProviderPort",
    "EmailMailboxPort",
    "EmailSenderPort",
    "EnrichmentPort",
    "LoadBoardPort",
]
