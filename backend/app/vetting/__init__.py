"""Vetting module — Broker Check tool.

Turns the FMCSA snapshot cache + prior-contact history into a single
"safe to haul?" verdict. Pure domain rules live in ``domain.py``; all
I/O is isolated to ``repository.py`` and injected into ``service.py``
via Protocol seams (DIP, onion plan).
"""
