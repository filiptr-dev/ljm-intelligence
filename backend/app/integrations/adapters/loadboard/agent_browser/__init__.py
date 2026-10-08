"""Headless-browser agent driver for load sources.

Shape mirrors ``news-aggregator/services/web-agent`` 1:1 (ADR 0004):

* a Playwright/Chromium sidecar exposing a closed verb set
  (``navigate | read_page | scroll | wait | finish``) over HTTP,
* a Gemini tool-loop that calls those verbs — never ``type`` or ``click``,
* one scripted login per source (``login/<src>.py``), credentials via
  :class:`app.identity.credentials.CredentialVault` — the LLM never sees
  them,
* ``AgentSource`` — a ``LoadSource``-shaped façade one instance per real
  source, selected by ``LOADS_<SRC>_DRIVER=agent``.

The sidecar process is deliberately external: the FastAPI backend never
imports Playwright directly, so a missing ``playwright`` package can never
crash boot. ``AGENT_BROWSER_URL`` empty → every agent source reports
``enabled=False``, ``reason="agent_browser_url_unset"``.
"""

from app.integrations.adapters.loadboard.agent_browser.driver import AgentSource

__all__ = ["AgentSource"]
