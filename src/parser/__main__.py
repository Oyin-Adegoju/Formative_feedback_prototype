"""Maakt `python -m src.parser ...` mogelijk."""

import sys

from src.parser import _main


if __name__ == "__main__":
    sys.exit(_main())
