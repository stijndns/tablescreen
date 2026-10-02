"""Entry point: ``python -m tablescreen``.

Deliberately tiny — all startup logic lives in core.app so it can be
imported and tested without going through the -m machinery.
"""

from tablescreen.core.app import main

if __name__ == "__main__":
    main()