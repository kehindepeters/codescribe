# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Render the Ladder POUs in a PLCopen XML file as ASCII.

    python tools/ladder/render_ld.py <file.xml> [more.xml ...]

Prototype only - not yet wired into the CODESYS export path.
"""

from __future__ import print_function

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ascii_render import render_pou  # noqa: E402
from parse import parse_pous  # noqa: E402


def render_file(path):
    lines = []
    for pou in parse_pous(path):
        lines.extend(render_pou(pou))
        lines.append("")
    return lines


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    for path in argv:
        for line in render_file(path):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
