# REMEMBER: this is python 2.7
"""Write a human-readable rendering of a graphical POU alongside its native xml.

Graphical POUs (LD, FBD, SFC, CFC) have no textual implementation, so they
export as CODESYS native xml, which git can store but nobody can review. This
adds a derived .txt next to it: the ST equivalent followed by the diagram.

The .txt is READ-ONLY as far as CODESCRIBE is concerned. The native xml stays
the only thing Import From Files reads, so the round trip is unaffected and
editing the .txt achieves nothing. import_from_files dispatches on ".xml" and
".st", so a ".txt" is ignored by construction.

The rendering goes through PLCopen xml rather than the native format, because
PLCopen has a published schema for graphical bodies while the native format
does not.
"""

import os
import tempfile

import fbd_render
import ld_render
import parse_fbd
import parse_ld
import st_render
from util import open_utf8

# Suffix for the derived file. Deliberately not .st: these are not importable
# and must never be mistaken for source.
RENDERED_SUFFIX = ".txt"


def _render_pous(plcopen_path):
    """(pou, art_renderer) for every POU in the file we know how to draw."""
    found = []
    for pou in parse_ld.parse_pous(plcopen_path):
        found.append((pou, ld_render))
    for pou in parse_fbd.parse_pous(plcopen_path):
        found.append((pou, fbd_render))
    return found


def render_plcopen(plcopen_path):
    """Render every renderable POU in a PLCopen file. [] if there are none."""
    lines = []
    for pou, art_renderer in _render_pous(plcopen_path):
        lines.extend(st_render.render_pou(pou))
        lines.append(u"")
        # The diagram repeats the declaration, which is noise the second time.
        declaration_length = len(ld_render.render_declaration(pou))
        lines.extend(art_renderer.render_pou(pou)[declaration_length:])
        lines.append(u"")

    while lines and lines[-1] == u"":
        lines.pop()
    return lines


def write_rendered_text(obj, base_path):
    """Export obj as PLCopen xml, render it, and write <base_path>.txt.

    Returns True if a file was written. SFC and CFC bodies parse to nothing
    renderable, so they are skipped rather than producing an empty file.

    A rendering failure must not fail the export: the native xml has already
    been written and is complete and correct on its own. The problem is
    reported and the export carries on.
    """
    # Staged outside the export folder: exports are written to a staging
    # directory that gets swapped into place wholesale, and a temp file left
    # behind by a failed cleanup would be swapped in along with it.
    handle, temp_path = tempfile.mkstemp(suffix=".plcopen.xml")
    os.close(handle)
    try:
        obj.export_xml(path=temp_path, recursive=False)

        lines = render_plcopen(temp_path)
        if not lines:
            return False

        with open_utf8(base_path + RENDERED_SUFFIX, "w") as f:
            f.write(u"\n".join(lines))
            f.write(u"\n")
        return True
    except Exception as error:
        print("WARNING: could not render " + obj.get_name() + ": " + repr(error))
        return False
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
