"""Shared helpers that neither the route layer nor the service layer owns.

Modules here are importable from both ``app/api/`` and ``app/services/`` — no
layering violation. If a helper is used from two layers, it belongs here.
"""
