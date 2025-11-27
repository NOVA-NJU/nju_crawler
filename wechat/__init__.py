"""Wechat package exports.

Expose the lifecycle manager so the main application can compose lifespans.
"""

from .lifecycle import wechat_lifespan  # re-export lifespan manager

__all__ = ["wechat_lifespan"]

