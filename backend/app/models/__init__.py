"""All ORM models — re-exported from each per-module `models.py`.

Keeping this symbol table whole is what lets Alembic autogenerate still see
every table after the per-module split. Business logic imports from the
module (e.g. `app.prospecting.models.Lead`); callers that already used
`from app.models import X` keep working with no churn.
"""

from __future__ import annotations

from app.analysis.models import (
    AiUsageLog,
    BrokerLookalike,
    BrokerPrediction,
    ForgetContactAudit,
    LanePrediction,
    LeadAiSummary,
    ObjectionCluster,
    PredictionRun,
)
from app.identity.models import (
    Organization,
    OrganizationMember,
    PlatformSettings,
    SettingsRow,
    TenantCredential,
    TenantFeatureFlag,
    TenantSettings,
    User,
)
from app.inbox.models import MailCursor, MailMessage, MessageInsight, NoReplyTracker
from app.outreach.models import (
    CallOutcome,
    CapacityPost,
    EmailTemplate,
    SentLog,
    Suppression,
)
from app.rates.models import DieselPrice
from app.prospecting.models import (
    AgentRun,
    CrawlRun,
    EnrichmentCandidate,
    FitScoreHistory,
    Lead,
    LeadContact,
    LeadContactProvenance,
    LeadSource,
    Load,
    Score,
    ShipperCandidate,
)

__all__ = [
    "AgentRun",
    "AiUsageLog",
    "BrokerLookalike",
    "BrokerPrediction",
    "CallOutcome",
    "CapacityPost",
    "CrawlRun",
    "DieselPrice",
    "EmailTemplate",
    "EnrichmentCandidate",
    "FitScoreHistory",
    "ForgetContactAudit",
    "LanePrediction",
    "Lead",
    "LeadAiSummary",
    "LeadContact",
    "LeadContactProvenance",
    "LeadSource",
    "Load",
    "MailCursor",
    "MailMessage",
    "MessageInsight",
    "NoReplyTracker",
    "ObjectionCluster",
    "Organization",
    "OrganizationMember",
    "PlatformSettings",
    "PredictionRun",
    "Score",
    "SentLog",
    "SettingsRow",
    "ShipperCandidate",
    "Suppression",
    "TenantCredential",
    "TenantFeatureFlag",
    "TenantSettings",
    "User",
]
