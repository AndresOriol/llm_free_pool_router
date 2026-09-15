"""Holding off the machine's idle sleep for the length of a run.

A session runs for hours with long gaps between provider calls, which looks
exactly like an idle machine. Windows suspends on that idleness, and a request
in flight when it does comes back to a socket the other end has long since
dropped -- one eval run woke to a dead connection and sat 43 minutes on a reply
that could never arrive.

The call timeout in llm_router/providers.py is what makes that survivable: the
dead socket becomes a reroute instead of a hang. This is the other half --
not being suspended in the first place -- and the two are independent. Keep
both: a suspend can still happen for reasons this cannot prevent.

What it does *not* do, deliberately:

- It does not keep the display on (`ES_DISPLAY_REQUIRED` is not set). Nothing
  here needs a lit screen.
- It does not stop a suspend the operator asks for. Closing the lid or hitting
  the power button is a policy decision that lives in Windows' power settings
  and belongs to whoever owns the machine, not to a library.

`SetThreadExecutionState` is per-thread and lasts as long as the calling thread
does, so this must be entered on the thread that lives for the whole run.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import sys

logger = logging.getLogger("harness.awake")

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


@contextlib.contextmanager
def keep_awake():
    """Ask Windows not to idle-suspend while the block runs. Never raises.

    A no-op everywhere else, and on any failure: losing the request is worth a
    warning, never the run.
    """
    if sys.platform != "win32":
        yield
        return

    try:
        set_state = ctypes.windll.kernel32.SetThreadExecutionState
    except (AttributeError, OSError) as exc:  # no kernel32 to talk to
        logger.warning(f"Could not ask Windows to stay awake: {exc!r}")
        yield
        return

    if not set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
        # Documented failure signal: the previous state, and 0 means refused.
        logger.warning("Windows refused the stay-awake request; a suspend "
                       "mid-run will be handled by the call timeout instead.")
    try:
        yield
    finally:
        # Drop back to "nothing required". Skipping this would leave the
        # machine unable to idle-suspend until the process happened to exit.
        set_state(ES_CONTINUOUS)
