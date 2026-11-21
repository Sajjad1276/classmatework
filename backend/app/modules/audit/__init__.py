"""Append-only audit domain boundary.

Audit events must be written by application use-cases and must not be silently
rewritten or deleted by ordinary administrative operations.
"""