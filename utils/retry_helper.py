"""包内网络重试工具。"""

import time
from functools import wraps

from loguru import logger


def with_retry(max_retries: int = 3, initial_delay: float = 2.0):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception:
                    if attempt == max_retries:
                        raise
                    delay = initial_delay * (2**attempt)
                    logger.warning(
                        "{} 调用失败，{:.1f} 秒后重试（{}/{}）",
                        func.__name__,
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                    time.sleep(delay)
        return wrapper
    return decorator


def with_graceful_retry(default_factory, max_retries: int = 2):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return with_retry(max_retries=max_retries)(func)(*args, **kwargs)
            except Exception as exc:
                logger.error("{} 最终失败：{}", func.__name__, exc)
                return default_factory(*args, **kwargs)
        return wrapper
    return decorator
