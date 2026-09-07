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

    def __init__(self, name, source=None, declaration=None):
        self._name = name
        self._source = source
        self.export_calls = []
        if declaration is not None:
            self.textual_declaration = type("TextualDeclaration", (object,), {"text": declaration})()

    def get_name(self):
        return self._name

    def export_xml(self, path, recursive, declarations_as_plaintext=None):
        self.export_calls.append((path, recursive, declarations_as_plaintext))
        if self._source is None:
            raise RuntimeError("export_xml exploded")
        shutil.copyfile(self._source, path)


class OldScriptEnginePou(FakePou):
    """A build without the declarations_as_plaintext overload.

    IronPython raises TypeError when no overload matches, which must fall back
    to the plain call rather than losing the rendering.
    """

    def export_xml(self, path, recursive):
        self.export_calls.append((path, recursive))
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
    # Without this the declaration loses comments, pragmas and attributes.
    check_equal("plaintext declarations are requested", pou.export_calls[0][2], True)

    content = read(base + ".txt")
    check("derived file leads with the declaration", content.startswith("PROGRAM LD_TEST"))
    # The diagram file holds the diagram. The two notations were written into
    # one file at first and that was worse, not better: the same network twice,
    # one rendering after the other, is harder to read than either alone.
    check("no ST rendering in the diagram file", "IF CTU_0.Q THEN PowerOff := FALSE; END_IF" not in content)
    check("networks are numbered", "(* Network 1 *)" in content)
    check("derived file contains the diagram", "TON_0 : TON" in content)
    check("the declaration appears once", content.count("END_VAR") == 1)
    check("derived file ends with a newline", content.endswith("\n"))

    # --- the ST rendering, in a file of its own -----------------------------

    st_content = read(base + ".st.txt")
    check("ST file lands beside the diagram", os.path.exists(base + ".st.txt"))
    # It reads like source and sits next to real .st exports, so the file has
    # to say what it is before it says anything else.
    check("ST file opens with the read-only banner", st_content.startswith("(* Equivalent Structured Text"))
    check("the banner forbids importing it", "must never be imported" in st_content)
    check("ST file carries the declaration", "PROGRAM LD_TEST" in st_content)
    check("ST file states the logic", "IF CTU_0.Q THEN PowerOff := FALSE; END_IF" in st_content)
    check("ST file is numbered like the diagram", "(* Network 1" in st_content)
    check("ST file has no diagram in it", "TON_0 : TON" not in st_content.replace("TON_0 : TON;", ""))
    check("ST file ends with a newline", st_content.endswith("\n"))

    # The temp PLCopen file is staged outside the export folder, so nothing but
    # the two renderings may appear next to the native xml.
    check_equal(
        "no stray files left behind",
        sorted(os.listdir(workspace)),
        ["LD_TEST.st.txt", "LD_TEST.txt"],
    )

    # --- an older ScriptEngine without the plaintext overload ---------------

    old_base = os.path.join(workspace, "OLD")
    old_pou = OldScriptEnginePou("OLD", os.path.join(FIXTURES, "LDTesting.xml"))
    check("an older ScriptEngine still renders", graphical_export.write_rendered_text(old_pou, old_base) is True)
    check("it fell back to the plain call", os.path.exists(old_base + ".txt"))

    # --- languages we cannot draw are skipped, not written empty ------------

    sfc_base = os.path.join(workspace, "SFC_TEST")
    sfc = FakePou("SFC_TEST", os.path.join(FIXTURES, "SFCTesting.xml"))
    check("sfc reports nothing rendered", graphical_export.write_rendered_text(sfc, sfc_base) is False)
    check("sfc writes no empty file", not os.path.exists(sfc_base + ".txt"))
    check("sfc writes no empty ST file", not os.path.exists(sfc_base + ".st.txt"))

    # --- cost reporting -----------------------------------------------------

    # The ScriptEngine can keep modules loaded between runs, so without an
    # explicit reset the summary would report totals accumulated across every
    # Export click since CODESYS started.
    # Two ladder POUs rendered by this point: the plain one and the one
    # standing in for an older ScriptEngine.
    check_equal("each render is counted", graphical_export.STATS["rendered"], 2)
    check_equal("the skipped sfc is counted", graphical_export.STATS["skipped"], 1)
    check("the summary names both costs", "CODESYS export_xml" in graphical_export.summary())

    graphical_export.reset_stats()
    check_equal("reset clears the counts", graphical_export.STATS["rendered"], 0)
    check_equal("nothing to report after a reset", graphical_export.summary(), None)

    graphical_export.STATS["rendered"] = 1
    graphical_export.STATS["verbatim_declarations"] = 1
    graphical_export.STATS["fallback_declarations"] = 1
    check(
        "mixed declaration sources are reported",
        "1 POU declaration(s) were rebuilt" in graphical_export.summary(),
    )
    graphical_export.reset_stats()

    source_declaration = """{attribute 'qualified_only'}
PROGRAM LD_TEST
VAR
    S_xSafe : SAFEBOOL;
    // OUT0200 is the hardware channel identifier.
    uiChannel : UINT := 0200;
END_VAR"""
    source_pou = FakePou("LD_TEST", os.path.join(FIXTURES, "LDTesting.xml"), source_declaration)
    source_base = os.path.join(workspace, "SOURCE")
    check("source declaration is rendered verbatim", graphical_export.write_rendered_text(source_pou, source_base) is True)
    source_content = read(source_base + ".txt")
    check("safety type survives", "S_xSafe : SAFEBOOL;" in source_content)
    check("declaration comment survives", "OUT0200 is the hardware channel identifier." in source_content)
    check("padded literal survives", "UINT := 0200;" in source_content)
    check("declaration pragma survives", "{attribute 'qualified_only'}" in source_content)

    # --- a rendering failure must not fail the export -----------------------

    broken_base = os.path.join(workspace, "BROKEN")
    broken = FakePou("BROKEN", None)
    check("a broken export is reported, not raised", graphical_export.write_rendered_text(broken, broken_base) is False)
    check("broken pou writes no file", not os.path.exists(broken_base + ".txt"))

    # The barrier must also hold around its own scaffolding: a temp file that
    # cannot be created (%TEMP% full) or removed (an antivirus scan holding it)
    # is exactly the kind of environmental hiccup that must not abort a whole
    # Export To Files run over a derived file.

    real_mkstemp = tempfile.mkstemp

    def failing_mkstemp(*args, **kwargs):
        raise OSError("no temp space")

    tempfile.mkstemp = failing_mkstemp
    try:
        no_temp = FakePou("NO_TEMP", os.path.join(FIXTURES, "LDTesting.xml"))
        try:
            outcome = graphical_export.write_rendered_text(no_temp, os.path.join(workspace, "NO_TEMP"))
            check("a temp-file creation failure is reported, not raised", outcome is False)
        except Exception as error:
            check("a temp-file creation failure is reported, not raised", False, repr(error))
    finally:
        tempfile.mkstemp = real_mkstemp

    real_remove = os.remove
    real_sticky_mkstemp = tempfile.mkstemp
    stranded = []

    def failing_remove(path):
        raise OSError("sharing violation")

    def recording_mkstemp(*args, **kwargs):
        result = real_sticky_mkstemp(*args, **kwargs)
        stranded.append(result[1])
        return result

    tempfile.mkstemp = recording_mkstemp
    os.remove = failing_remove
    try:
        sticky = FakePou("STICKY", os.path.join(FIXTURES, "LDTesting.xml"))
        try:
            outcome = graphical_export.write_rendered_text(sticky, os.path.join(workspace, "STICKY"))
            check("a temp-file cleanup failure is reported, not raised", outcome is True)
        except Exception as error:
            check("a temp-file cleanup failure is reported, not raised", False, repr(error))
    finally:
        os.remove = real_remove
        tempfile.mkstemp = real_sticky_mkstemp
        # The blocked cleanup deliberately strands the temp file; without this
        # the suite leaks one orphan into the real temp directory per run.
        for leaked in stranded:
            if os.path.exists(leaked):
                os.remove(leaked)

    # A write that dies halfway must not leave a truncated .txt behind: the
    # staging folder is swapped into place wholesale, and a half-written
    # rendering looks exactly like a valid one that misstates the logic.
    real_open_utf8 = graphical_export.open_utf8

    class FailingWriter(object):
        def __init__(self, handle):
            self._handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self._handle.close()
            return False

        def write(self, text):
            self._handle.write(text[: len(text) // 2])
            raise IOError("disk full")

    def failing_open_utf8(path, mode):
        return FailingWriter(real_open_utf8(path, mode))

    graphical_export.open_utf8 = failing_open_utf8
    try:
        torn = FakePou("TORN", os.path.join(FIXTURES, "LDTesting.xml"))
        torn_base = os.path.join(workspace, "TORN")
        try:
            outcome = graphical_export.write_rendered_text(torn, torn_base)
            check("a mid-write failure is reported, not raised", outcome is False)
        except Exception as error:
            check("a mid-write failure is reported, not raised", False, repr(error))
        check("a truncated rendering is not left behind", not os.path.exists(torn_base + ".txt"))
        # Both files go, not just the one that happened to fail: a diagram
        # with no ST beside it, or the reverse, is a rendering that disagrees
        # with itself.
        check("no half-written ST is left behind", not os.path.exists(torn_base + ".st.txt"))
    finally:
        graphical_export.open_utf8 = real_open_utf8
finally:
    shutil.rmtree(workspace)


# --- sub-POU members render their own body, never the parent's --------------

# An action's PLCopen export wraps it in its parent POU, parent body included.
# Without the member name the rendering drew the parent's networks under the
# action's filename - a dump describing a different POU than the .xml beside
# it, which a reviewer has no way to notice.
ACTION_FIXTURE = os.path.join(HERE, "fixtures", "action_member.plcopen.xml")
METHOD_FIXTURE = os.path.join(HERE, "fixtures", "method_member.plcopen.xml")

workspace = tempfile.mkdtemp()
try:
    graphical_export.reset_stats()

    action_base = os.path.join(workspace, "PLC_TEST.ACT_TEST")
    action = FakePou("ACT_TEST", ACTION_FIXTURE)
    check(
        "an action renders",
        graphical_export.write_rendered_text(action, action_base, member_name="ACT_TEST") is True,
    )
    action_content = read(action_base + ".txt")
    check("the action's own body is drawn", "Status.Action" in action_content)
    check("the parent's body is not drawn", "Status.Parent" not in action_content)
    check("the rendering is titled for the member", "PLC_TEST.ACT_TEST" in action_content)
    check(
        "the rendering says whose declaration it shows",
        "the declaration below is the parent POU's" in action_content,
    )

    # Both derived files describe the member. An ST file showing the parent
    # beside a diagram showing the action would be worse than either alone.
    action_st = read(action_base + ".st.txt")
    check("the ST file follows the member too", "Status.Action := xAction;" in action_st)
    check("the ST file does not show the parent", "Status.Parent" not in action_st)
    check("the ST file carries the member note", "the declaration below is the parent POU's" in action_st)

    # A graphical method: CODESYS spells the tag <Method> with a capital M
    # where it spells actions <action>. Matching one case only meant methods
    # never found their own body and silently rendered nothing.
    method_base = os.path.join(workspace, "FB_TEST.Compute")
    method = FakePou("Compute", METHOD_FIXTURE)
    check(
        "a graphical method renders",
        graphical_export.write_rendered_text(method, method_base, member_name="Compute") is True,
    )
    method_content = read(method_base + ".txt")
    check("the method's own body is drawn", "Status.Method" in method_content)
    check("the method does not draw the parent", "Status.Parent" not in method_content)

    # The parent's own rendering must still be the parent body, members
    # excluded - iter_bodies only ever took the pou's direct <body>.
    parent_base = os.path.join(workspace, "PLC_TEST")
    parent = FakePou("PLC_TEST", ACTION_FIXTURE)
    check("the parent still renders", graphical_export.write_rendered_text(parent, parent_base) is True)
    parent_content = read(parent_base + ".txt")
    check("the parent draws its own body", "Status.Parent" in parent_content)
    check("the parent does not absorb the action", "Status.Action" not in parent_content)
    check(
        "the parent's dump carries no member note",
        "the declaration below is the parent POU's" not in parent_content,
    )

    # A member whose body the export does not carry must produce NO file: an
    # absent rendering sends the reviewer to the native xml, a foreign one
    # does not.
    missing_base = os.path.join(workspace, "PLC_TEST.ACT_MISSING")
    missing = FakePou("ACT_MISSING", os.path.join(FIXTURES, "LDTesting.xml"))
    check(
        "a member the export lacks writes nothing",
        graphical_export.write_rendered_text(missing, missing_base, member_name="ACT_MISSING") is False,
    )
    check("no foreign dump is written", not os.path.exists(missing_base + ".txt"))
    check("no foreign ST is written either", not os.path.exists(missing_base + ".st.txt"))
    check_equal("the missing member is counted", graphical_export.STATS["members_missing"], 1)
    check("the summary reports the missing member", "nothing was written for those" in graphical_export.summary())
finally:
    shutil.rmtree(workspace)


# --- the importer ignores the derived file ---------------------------------

# This is the contract that keeps the round trip intact. import_directory_child
# dispatches on ".xml" and ".st"; a ".txt" matches no branch. Asserting it here
# means a later change to that dispatch cannot silently start importing
# derived files.
workspace = tempfile.mkdtemp()
try:
    for name in ("Main.txt", "Main.Method.txt", "Main.gvl.txt", "Main.st.txt", "Main.Method.st.txt"):
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
