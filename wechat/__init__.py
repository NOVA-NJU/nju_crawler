"""Wechat package exports.

Keep package import lightweight; lazily load optional runtime integrations.
"""

__all__ = ["wechat_lifespan"]


def __getattr__(name: str):
    if name == "wechat_lifespan":
        from .lifecycle import wechat_lifespan

        return wechat_lifespan
    raise AttributeError(f"module 'wechat' has no attribute {name!r}")

