"""ai_usage — one function `record()` that writes a `ProviderCall` into
`ai_usage_log`.

Non-blocking: a log-write failure is warned and swallowed. The caller's
provider call is the thing that matters; a failed audit row must never break
the primary feature.
"""

from __future__ import annotations

import hashlib
import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AiUsageLog
from app.integrations.adapters.ai.provider import ProviderCall

log = logging.getLogger(__name__)


def hash_prompt(prompt: str | None) -> str | None:
    if not prompt:
        return None
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


async def record(
    session: AsyncSession,
    call: ProviderCall,
    *,
    feature: str,
    input_hash: str | None = None,
    compare_id: str | None = None,
) -> None:
    """Write one usage row. Never raises."""
    try:
        row = AiUsageLog(
            feature=feature[:48],
            provider=(call.provider or "null")[:16],
            model=(call.model or "none")[:64],
            input_tokens=int(call.input_tokens or 0),
            output_tokens=int(call.output_tokens or 0),
            latency_ms=int(call.latency_ms or 0),
            cost_usd=call.cost_usd if call.cost_usd is not None else Decimal(0),
            status=(call.status or "ok")[:24],
            error=(call.error or None),
            compare_id=compare_id,
            input_hash=input_hash,
        )
        session.add(row)
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("ai_usage.record failed: %s", exc)


async def record_via(
    sessionmaker: async_sessionmaker,
    call: ProviderCall,
    *,
    feature: str,
    input_hash: str | None = None,
    compare_id: str | None = None,
) -> None:
    """Convenience: open a short session, write, commit."""
    try:
        async with sessionmaker() as s:
            await record(s, call, feature=feature, input_hash=input_hash, compare_id=compare_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("ai_usage.record_via failed: %s", exc)
