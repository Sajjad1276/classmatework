"""Tenant and organization domain boundary.

Tenant context must be resolved from a verified principal and membership,
never trusted directly from a client-supplied tenant identifier.
"""