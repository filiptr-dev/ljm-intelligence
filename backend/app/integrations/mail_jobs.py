"""Mail connector module jobs (placeholder import for the queue registry).

The real mail tasks (``inbox.mail_backfill``, ``inbox.mail_incremental``)
live under ``app/inbox/jobs.py`` as per the queue plan's module layout.
This file exists only so the ``import_paths`` list in
``app.shared.queue`` can reference the integrations side without a
``ModuleNotFoundError`` — nothing is registered here.
"""
