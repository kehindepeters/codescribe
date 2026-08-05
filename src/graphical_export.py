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
import time

import fbd_render
import ld_render
import parse_fbd
import parse_ld
import plcopen
import st_render
from util import open_utf8

# Suffix for the derived file. Deliberately not .st: these are not importable
# and must never be mistaken for source.
RENDERED_SUFFIX = ".txt"

# Rendering adds a second CODESYS-side export per graphical POU, so the cost
# is worth reporting rather than leaving people to wonder why the export got
# slower. Split so it is obvious whether CODESYS or this code is the cost.
STATS = {"rendered": 0, "skipped": 0, "export_xml_seconds": 0.0, "render_seconds": 0.0}


def reset_stats():
    STATS.update({"rendered": 0, "skipped": 0, "export_xml_seconds": 0.0, "render_seconds": 0.0})


def summary():
    """One line describing what rendering cost, or None if it did nothing."""
    if not STATS["rendered"] and not STATS["skipped"]:
        return None
    return "Rendered %d graphical POUs in %.1fs (%.1fs CODESYS export_xml, %.1fs rendering); skipped %d" % (
        STATS["rendered"],
        STATS["export_xml_seconds"] + STATS["render_seconds"],
        STATS["export_xml_seconds"],
        STATS["render_seconds"],
        STATS["skipped"],
    )


# Body language -> (parser, diagram renderer). SFC and CFC are absent, so they
# fall through and no file is written for them.
RENDERERS = {
    parse_ld.LANGUAGE: (parse_ld, ld_render),
    parse_fbd.LANGUAGE: (parse_fbd, fbd_render),
}


def _render_pous(plcopen_path):
    """(pou, art_renderer) for every POU in the file we know how to draw.

    One pass over the document. Asking each language parser in turn would
    re-read and re-parse the whole file once per language, which is pure waste
    on a project with hundreds of POUs.
    """
    found = []
    for pou_elem, language, body in plcopen.iter_bodies(plcopen_path):
        entry = RENDERERS.get(language)
        if entry is None:
            continue
        parser, art_renderer = entry
        found.append((parser.pou_from_body(pou_elem, body), art_renderer))
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


def _remove_quietly(path):
    """Best-effort delete. Cleanup trouble is never worth failing an export."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def write_rendered_text(obj, base_path):
    """Export obj as PLCopen xml, render it, and write <base_path>.txt.

    Returns True if a file was written. SFC and CFC bodies parse to nothing
    renderable, so they are skipped rather than producing an empty file.

    A rendering failure must not fail the export: the native xml has already
    been written and is complete and correct on its own. The problem is
    reported and the export carries on. That barrier has to hold around the
    temp-file scaffolding too, not just the rendering itself - a full %TEMP%
    or an antivirus scan holding the temp file open must degrade to a warning
    exactly like a parse failure does.
    """
    # Staged outside the export folder: exports are written to a staging
    # directory that gets swapped into place wholesale, and a temp file left
    # behind by a failed cleanup would be swapped in along with it.
    try:
        handle, temp_path = tempfile.mkstemp(suffix=".plcopen.xml")
        os.close(handle)
    except Exception as error:
        print("WARNING: could not render " + obj.get_name() + ": " + repr(error))
        return False
    try:
        started = time.time()
        obj.export_xml(path=temp_path, recursive=False)
        STATS["export_xml_seconds"] += time.time() - started

        started = time.time()
        lines = render_plcopen(temp_path)
        if not lines:
            STATS["skipped"] += 1
            STATS["render_seconds"] += time.time() - started
            return False

        with open_utf8(base_path + RENDERED_SUFFIX, "w") as f:
            f.write(u"\n".join(lines))
            f.write(u"\n")
        STATS["rendered"] += 1
        STATS["render_seconds"] += time.time() - started
        return True
    except Exception as error:
        print("WARNING: could not render " + obj.get_name() + ": " + repr(error))
        # Say what is actually in the file, so a failure explains itself
        # instead of needing a separate diagnostic run. The diagnostic is
        # best-effort: it must not turn a reported failure into a raised one.
        try:
            if os.path.exists(temp_path):
                for note in plcopen.describe_suspect_characters(temp_path):
                    print("         " + note)
        except Exception:
            pass
        # A write that died halfway leaves a truncated rendering that looks
        # exactly like a valid one. No file at all is the honest outcome.
        _remove_quietly(base_path + RENDERED_SUFFIX)
        return False
    finally:
        _remove_quietly(temp_path)
