"""EchoGist app entry point: ``python -m echogist`` (launched by run.bat).

run.bat runs provisioning first, then launches this. The interactive menu lands
in T9; until then this is a clear placeholder rather than a crash.
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from .menu import run_menu
    except ImportError:
        print("EchoGist is provisioned. The interactive menu (T9) is not implemented yet.")
        return 0
    return int(run_menu())


if __name__ == "__main__":
    sys.exit(main())
