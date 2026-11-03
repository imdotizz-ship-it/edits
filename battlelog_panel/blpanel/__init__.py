"""Battlelog Profile Control Panel.

Read-only Battlelog integration, isolated modification lab and site sync.
Nothing in this package writes to EA / Battlelog servers: the only write path
(AuthorizedWriteAdapter) ships with an empty capability registry.
"""

__version__ = "0.1.0"
