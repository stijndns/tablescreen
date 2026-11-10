"""
api — The player webapp's way in: a bridge to the gateway (see service.py).

Features use ``Field`` and ``Caller`` to register actions; everything else is
core's business.
"""

from .actions import Caller, Field
from .service import ApiConfig, ApiService, quote

__all__ = ["ApiConfig", "ApiService", "Caller", "Field", "quote"]
