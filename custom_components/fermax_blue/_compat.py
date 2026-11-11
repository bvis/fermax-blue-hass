"""The HTTP and validation libraries, under the names the type checker needs.

Home Assistant 2026.11 moved from httpx to httpx2 and from voluptuous to probatio,
and aliases the old names when it starts, so `import httpx` and `import voluptuous`
keep working on every core we support. A type checker doesn't see that alias: on
the newer core it finds Home Assistant's own signatures asking for httpx2 and
probatio types. The cores that made the move only run on Python 3.14, and the one
our Python 3.13 checks install predates it, so the check follows the Python version.
At run time this module always imports the old names.
"""

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING and sys.version_info >= (3, 14):
    import httpx2 as httpx
    import probatio as vol
else:
    import httpx
    import voluptuous as vol

__all__ = ["httpx", "vol"]
