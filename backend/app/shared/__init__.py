"""shared/ — infrastructure every module reaches for.

Houses the DB session factory, the per-request / per-job tenant context,
UoW helpers, and the shared httpx client. Nothing business-y goes here.
"""
