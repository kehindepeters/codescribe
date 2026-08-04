# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Render the graphical POUs in a PLCopen XML file.

    python tools/ladder/render.py [--format art|st|both] <file.xml> [...]

    art   ASCII rungs and block diagrams, close to the CODESYS layout
    st    equivalent Structured Text - diffs and greps far better
    both  ST first, then the diagram (the default)

Prototype only - not yet wired into the CODESYS export path. Ladder and
Function Block Diagram are supported; SFC bodies are skipped.
"""

from __future__ import print_function

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ascii_render  # noqa: E402
import fbd_render  # noqa: E402
import parse  # noqa: E402
import parse_fbd  # noqa: E402
import st_render  # noqa: E402

FORMATS = ("art", "st", "both")


def _pous(path):
    """Every graphical POU in the file, paired with its art renderer."""
    found = []
    for pou in parse.parse_pous(path):
        found.append((pou, ascii_render))
    for pou in parse_fbd.parse_pous(path):
        found.append((pou, fbd_render))
    return found


def render_file(path, output_format="both"):
    lines = []
    for pou, art_renderer in _pous(path):
        if output_format in ("st", "both"):
            lines.extend(st_render.render_pou(pou))
            lines.append("")
        if output_format in ("art", "both"):
            if output_format == "both":
                # The diagram repeats the declaration, which is noise the
                # second time around.
                lines.extend(art_renderer.render_pou(pou)[len(ascii_render.render_declaration(pou)) :])
            else:
                lines.extend(art_renderer.render_pou(pou))
            lines.append("")
    return [line.rstrip() for line in lines]


def main(argv):
    output_format = "both"
    paths = []
    index = 0
    while index < len(argv):
        if argv[index] == "--format":
            index += 1
            if index >= len(argv) or argv[index] not in FORMATS:
                print("--format must be one of: " + ", ".join(FORMATS))
                return 2
            output_format = argv[index]
        else:
            paths.append(argv[index])
        index += 1

    if not paths:
        print(__doc__)
        return 2

    for path in paths:
        for line in render_file(path, output_format):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
