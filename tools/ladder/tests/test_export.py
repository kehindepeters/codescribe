# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Tests for the export-path bridge and the derived file's contract.

The renderers being correct is not enough: the derived .txt must not disturb
Export To Files / Import From Files. These cover the parts that would break a
working project rather than just produce an ugly diagram.

    python tools/ladder/tests/test_export.py
"""

from __future__ import print_function, unicode_literals

import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..", "..", "..")
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, os.path.join(REPO, "tools", "ci"))  # stubbed scriptengine

import graphical_export  # noqa: E402
import import_from_files  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures", "codesys")

failures = []


def check(name, condition, detail=""):
    if condition:
        print("OK      " + name)
    else:
        failures.append(name)
        print("FAIL    " + name + ((": " + detail) if detail else ""))


def check_equal(name, actual, expected):
    check(name, actual == expected, "expected %r, got %r" % (expected, actual))


class FakePou(object):
    """Stands in for a CODESYS ScriptObject.

    export_xml hands back a fixture instead of talking to CODESYS, which is
    exactly what the real call does from this module's point of view.
    """

    def __init__(self, name, source=None):
        self._name = name
        self._source = source
        self.export_calls = []

    def get_name(self):
        return self._name

    def export_xml(self, path, recursive):
        self.export_calls.append((path, recursive))
        if self._source is None:
            raise RuntimeError("export_xml exploded")
        shutil.copyfile(self._source, path)


class RecordingParent(object):
    """Records anything the importer tries to do to the project."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append(name)
            return RecordingParent()

        return record


def read(path):
    handle = io.open(path, encoding="utf-8")
    try:
        return handle.read()
    finally:
        handle.close()


# --- the derived file is written next to the native xml --------------------

workspace = tempfile.mkdtemp()
try:
    base = os.path.join(workspace, "LD_TEST")
    pou = FakePou("LD_TEST", os.path.join(FIXTURES, "LDTesting.xml"))

    check("ladder pou is rendered", graphical_export.write_rendered_text(pou, base) is True)
    check("derived file lands beside the xml", os.path.exists(base + ".txt"))
    check_equal("export_xml is asked for a single object", pou.export_calls[0][1], False)

    content = read(base + ".txt")
    check("derived file leads with ST", content.startswith("PROGRAM LD_TEST"))
    check("derived file contains the ST equivalent", "IF CTU_0.Q THEN PowerOff := FALSE; END_IF" in content)
    check("derived file contains the diagram", "TON_0 : TON" in content)
    check("declaration is not repeated", content.count("END_VAR") == 1)
    check("derived file ends with a newline", content.endswith("\n"))

    # The temp PLCopen file is staged outside the export folder, so nothing but
    # the rendering may appear next to the native xml.
    check_equal("no stray files left behind", sorted(os.listdir(workspace)), ["LD_TEST.txt"])

    # --- languages we cannot draw are skipped, not written empty ------------

    sfc_base = os.path.join(workspace, "SFC_TEST")
    sfc = FakePou("SFC_TEST", os.path.join(FIXTURES, "SFCTesting.xml"))
    check("sfc reports nothing rendered", graphical_export.write_rendered_text(sfc, sfc_base) is False)
    check("sfc writes no empty file", not os.path.exists(sfc_base + ".txt"))

    # --- cost reporting -----------------------------------------------------

    # The ScriptEngine can keep modules loaded between runs, so without an
    # explicit reset the summary would report totals accumulated across every
    # Export click since CODESYS started.
    check_equal("one render is counted", graphical_export.STATS["rendered"], 1)
    check_equal("the skipped sfc is counted", graphical_export.STATS["skipped"], 1)
    check("the summary names both costs", "CODESYS export_xml" in graphical_export.summary())

    graphical_export.reset_stats()
    check_equal("reset clears the counts", graphical_export.STATS["rendered"], 0)
    check_equal("nothing to report after a reset", graphical_export.summary(), None)

    # --- a rendering failure must not fail the export -----------------------

    broken_base = os.path.join(workspace, "BROKEN")
    broken = FakePou("BROKEN", None)
    check("a broken export is reported, not raised", graphical_export.write_rendered_text(broken, broken_base) is False)
    check("broken pou writes no file", not os.path.exists(broken_base + ".txt"))
finally:
    shutil.rmtree(workspace)


# --- the importer ignores the derived file ---------------------------------

# This is the contract that keeps the round trip intact. import_directory_child
# dispatches on ".xml" and ".st"; a ".txt" matches no branch. Asserting it here
# means a later change to that dispatch cannot silently start importing
# derived files.
workspace = tempfile.mkdtemp()
try:
    for name in ("Main.txt", "Main.Method.txt", "Main.gvl.txt"):
        handle = io.open(os.path.join(workspace, name), "w", encoding="utf-8")
        handle.write("PROGRAM Main\n")
        handle.close()

        parent = RecordingParent()
        import_from_files.import_directory_child(name, workspace, parent)
        check_equal("importer ignores " + name, parent.calls, [])

    # A control: the native xml alongside it must still import, or the test
    # above would pass for the wrong reason.
    shutil.copyfile(os.path.join(FIXTURES, "LDTesting.xml"), os.path.join(workspace, "Main.xml"))
    parent = RecordingParent()
    import_from_files.import_directory_child("Main.xml", workspace, parent)
    check_equal("native xml still imports", parent.calls, ["import_native"])
finally:
    shutil.rmtree(workspace)

print("")
if failures:
    print("%d check(s) failed" % len(failures))
else:
    print("all checks passed")
sys.exit(1 if failures else 0)
