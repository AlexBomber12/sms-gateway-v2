from __future__ import annotations


class ModemError(Exception):
    """Base exception for ModemManager wrapper failures."""


class ModemManagerUnavailable(ModemError):  # noqa: N818
    """Raised when ModemManager is unavailable or D-Bus connection fails."""


class ModemNotFound(ModemError):  # noqa: N818
    """Raised when no modem object is exposed by ModemManager."""


class ModemBusy(ModemError):  # noqa: N818
    """Raised when the modem is locked, initializing, or in an incompatible state."""


class MessageDeleteFailed(ModemError):  # noqa: N818
    """Raised when deleting an SMS object fails."""


class MessageReadMissing(ModemError):  # noqa: N818
    """Raised when an SMS object disappears before it can be read."""


class MessageReadSkipped(ModemError):  # noqa: N818
    """Raised when an SMS object is intentionally skipped during read."""
