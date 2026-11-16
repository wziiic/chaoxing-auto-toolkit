"""Keep Windows awake only while the learning worker is executing."""
import ctypes
import os
from contextlib import contextmanager


@contextmanager
def learning_awake(log):
    previous = 0
    setter = None
    if os.name == 'nt':
        try:
            setter = ctypes.windll.kernel32.SetThreadExecutionState
            setter.argtypes = [ctypes.c_uint]
            setter.restype = ctypes.c_uint
            previous = setter(0x80000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        except (AttributeError, OSError):
            pass
        if not previous:
            log('系统未接受防自动睡眠请求，请检查 Windows 电源设置')
    try:
        yield
    finally:
        if setter is not None and previous:
            setter(previous)  # Same worker thread; restore its prior requirements.
