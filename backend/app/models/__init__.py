"""All ORM models — re-exported from each per-module `models.py`.

Keeping this symbol table whole is what lets Alembic autogenerate still see
every table after the per-module split. Business logic imports from the
module (e.g. `app.prospecting.models.Lead`); callers that already used
`from app.models import X` keep working with no churn.
"""

from __future__ import annotations

from app.analysis.models import (  # noqa: F401
    AiUsageLog,
    BrokerLookalike,
    BrokerPrediction,
    ForgetContactAudit,
    LanePrediction,
    ObjectionCluster,
    PredictionRun,
)
from app.identity.models import (  # noqa: F401
    Organization,
    OrganizationMember,
    PlatformSettings,
    SettingsRow,
    TenantCredential,
    TenantFeatureFlag,
    TenantSettings,
    User,
)
from app.inbox.models import MailCursor, MailMessage, MessageInsight, NoReplyTracker  # noqa: F401
from app.outreach.models import (  # noqa: F401
    CallOutcome,
    CapacityPost,
    EmailTemplate,
    SentLog,
    Suppression,
)
from app.prospecting.models import (  # noqa: F401
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
    "AiUsageLog",
    "BrokerLookalike",
    "BrokerPrediction",
    "CallOutcome",
    "ForgetContactAudit",
    "LanePrediction",
    "ObjectionCluster",
    "PredictionRun",
    "CapacityPost",
    "CrawlRun",
    "EmailTemplate",
    "EnrichmentCandidate",
    "FitScoreHistory",
    "Lead",
    "LeadContact",
    "LeadContactProvenance",
    "LeadSource",
    "Load",
    "MailCursor",
    "MailMessage",
    "MessageInsight",
    "NoReplyTracker",
    "Organization",
    "OrganizationMember",
    "PlatformSettings",
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
