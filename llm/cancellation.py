"""Bound local cancellation latency even while an HTTP call waits for headers."""
import threading
import time


class RequestCancelled(Exception):
    pass


def interruptible_request(operation, cancelled, close, waiting=None):
    if not cancelled:
        return operation()
    if cancelled():
        raise RequestCancelled()
    done = threading.Event()
    result = []
    def execute():
        try:
            result.append((True, operation()))
        except BaseException as exc:
            result.append((False, exc))
        finally:
            done.set()
    threading.Thread(target=execute, name='pid-api-request', daemon=True).start()
    started = time.monotonic()
    notified = -2.0
    while not done.wait(.05):
        if cancelled():
            # Closing a transport can itself wait on an SDK lock. Never block
            # the local stop acknowledgement on that cleanup operation.
            def cleanup():
                try: close()
                except Exception: pass
            threading.Thread(target=cleanup, name='pid-api-close', daemon=True).start()
            raise RequestCancelled()
        elapsed = time.monotonic() - started
        if waiting and elapsed - notified >= 2:
            waiting(elapsed)
            notified = elapsed
    if cancelled():
        raise RequestCancelled()
    success, value = result[0]
    if not success:
        raise value
    return value
