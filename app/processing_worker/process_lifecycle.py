"""Cooperative process shutdown; no threads or remote control channel."""
import signal


def run_with_signals(run, stop, close_on_setup_failure):
    previous = {}
    running = False
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, lambda *_: stop.set())
        running = True
        run()
    finally:
        try:
            if not running:
                close_on_setup_failure()
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    return 0
