"""按平台的进程内节流器：在发起下载前拉开相邻请求间隔，规避风控/限流。

设计要点：
- 进程内（同一 Flask 进程内的 ThreadPoolExecutor 多线程）共享一份计时状态；
- 线程安全：每个 platform_key 各自一把锁，串行化「计算等待 + 推进时间戳」并在锁外 sleep；
- 仅用 time.monotonic 计时（不受系统时钟回拨影响），不使用 random / datetime；
- 不同平台互不阻塞；同一平台的并发请求按 FIFO 般依次拉开间隔；
- 等待期间可被 cancel_event 中断，避免用户取消后仍长时间阻塞；
- 节流间隔统一从 config 读取（rate_limit_aggressive_sec / rate_limit_moderate_sec），
  config 是唯一来源，便于按网络环境集中调参。

用法：
    from app.services.throttle import throttle
    waited = throttle(rule.key, rule.rate_limit, cancel_event=ev)
"""
from __future__ import annotations

import threading
import time
from typing import Optional

# 等待时的轮询粒度（秒）：用于周期性检查 cancel_event。
_POLL_GRANULARITY = 0.2


def _interval_for(rate_limit: str) -> float:
    """按 rate_limit 档位从 config 取相邻请求最小间隔（秒）。

    aggressive / moderate 分别对应 config 的两档间隔；none 或未知档位不限（0）。
    """
    from app.config import get_config

    cfg = get_config()
    return {
        "aggressive": cfg.rate_limit_aggressive_sec,
        "moderate": cfg.rate_limit_moderate_sec,
    }.get(rate_limit, 0.0)


class PlatformThrottle:
    """按 platform_key 维护「上次放行时间戳 + 专属锁」的节流器。

    线程安全：
    - _registry_lock 保护「按 key 创建/获取专属锁与时间戳」的注册表；
    - 每个 key 一把 _key_locks[key]，保证同一平台的等待计算与时间戳推进串行；
    - 真正的 sleep 在 key 锁外进行，避免长时间持锁；推进后的目标时间戳已预占，
      因此并发请求会依次叠加间隔（请求1 等 0s、请求2 等 ~interval、请求3 等 ~2*interval）。
    """

    def __init__(self) -> None:
        self._registry_lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}
        # 每个平台「下一次允许放行」的 monotonic 时间戳（已预占，含排队叠加）。
        self._next_allowed: dict[str, float] = {}

    def _lock_for(self, key: str) -> threading.Lock:
        with self._registry_lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._key_locks[key] = lock
            return lock

    def wait(
        self,
        platform_key: str,
        rate_limit: str,
        cancel_event: Optional[threading.Event] = None,
    ) -> float:
        """按平台频率限制阻塞当前线程，返回实际等待秒数。

        rate_limit 不在已知档位或为 none 时不等待（返回 0.0）。
        """
        interval = _interval_for(rate_limit)
        if interval <= 0 or not platform_key:
            return 0.0

        key = platform_key
        key_lock = self._lock_for(key)

        # 在 key 锁内：计算本次应等待到的目标时间戳，并把「下一次允许放行」向后推进，
        # 从而为后续并发请求预占间隔（排队叠加）。sleep 放到锁外执行。
        #
        # 取舍：若本次请求在锁外 sleep 期间被 cancel_event 取消，已预占的这一段 interval
        # 不会回滚，后续同平台请求会因此多等最多一个 interval。这是「过度节流」方向、对风控
        # 安全（绝不会欠节流），且单调时间戳会自然追平，故有意不引入并发回滚的复杂度。
        with key_lock:
            now = time.monotonic()
            next_allowed = self._next_allowed.get(key, 0.0)
            target = now if next_allowed <= now else next_allowed
            # 预占：下一个请求至少要等到 target + interval。
            self._next_allowed[key] = target + interval

        wait_sec = target - time.monotonic()
        if wait_sec <= 0:
            return 0.0

        self._sleep_interruptible(wait_sec, cancel_event)
        return wait_sec

    @staticmethod
    def _sleep_interruptible(
        seconds: float,
        cancel_event: Optional[threading.Event],
    ) -> None:
        """分片 sleep，期间若 cancel_event 被置位则立即返回（由调用方处理取消）。"""
        if cancel_event is None:
            time.sleep(seconds)
            return
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            if cancel_event.is_set():
                return
            time.sleep(min(_POLL_GRANULARITY, remaining))


# 进程内单例：所有任务线程共享同一份计时状态。
_throttle = PlatformThrottle()


def throttle(
    platform_key: str,
    rate_limit: str,
    cancel_event: Optional[threading.Event] = None,
) -> float:
    """按平台频率限制节流：阻塞至允许放行，返回实际等待秒数（0 表示未等待）。"""
    return _throttle.wait(platform_key, rate_limit, cancel_event=cancel_event)
