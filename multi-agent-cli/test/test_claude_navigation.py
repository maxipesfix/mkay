#!/usr/bin/env python3
"""Run the shared live navigation test against claude's two views."""
import sys

from navigation import main


if __name__ == '__main__':
    sys.exit(main(default_app='claude'))
