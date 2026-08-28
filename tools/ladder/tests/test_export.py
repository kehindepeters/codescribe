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
import import_export  # noqa: E402
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
    # Diagram only. Rendering the same network twice, once as ST and once as a
    # diagram, made the files harder to read rather than easier.
    check("no ST rendering is written", "IF CTU_0.Q THEN PowerOff := FALSE; END_IF" not in content)
    check("networks are numbered", "(* Network 1 *)" in content)
    check("derived file contains the diagram", "TON_0 : TON" in content)
    check("the declaration appears once", content.count("END_VAR") == 1)
    check("derived file ends with a newline", content.endswith("\n"))

    # The temp PLCopen file is staged outside the export folder, so nothing but
    # the rendering may appear next to the native xml.
    check_equal("no stray files left behind", sorted(os.listdir(workspace)), ["LD_TEST.txt"])

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

    # The parent's own rendering must still be the parent body, actions
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
    check_equal("the missing member is counted", graphical_export.STATS["members_missing"], 1)
    check("the summary reports the missing member", "no .txt was written" in graphical_export.summary())
finally:
    shutil.rmtree(workspace)


# --- network numbers follow the native export -------------------------------

# An out-commented network is absent from the PLCopen export, so numbering the
# rendered networks 1..n renumbered everything after it away from the native
# .xml - the file reviewers actually hold next to the .txt. The native export
# written just before the rendering carries the full list, and is the
# authority.
NATIVE_FIXTURE = os.path.join(HERE, "fixtures", "native_networks.xml")

workspace = tempfile.mkdtemp()
try:
    graphical_export.reset_stats()

    merged_base = os.path.join(workspace, "MERGED")
    shutil.copyfile(NATIVE_FIXTURE, merged_base + ".xml")
    merged = FakePou("MERGED", os.path.join(FIXTURES, "LDTesting.xml"))
    check("a pou with a native list renders", graphical_export.write_rendered_text(merged, merged_base) is True)
    merged_content = read(merged_base + ".txt")
    check("native comments reach the headers", "(* Network 1: first rung *)" in merged_content)
    check("the out-commented network keeps its number", "(* Network 2: disabled logic *)" in merged_content)
    check(
        "the out-commented network is marked",
        "out-commented in CODESYS" in merged_content,
    )
    check("later networks keep the editor's numbers", "(* Network 3 *)" in merged_content)
    check_equal("nothing is misaligned", graphical_export.STATS["alignment_failures"], 0)

    # A native list that cannot be lined up must not renumber by guesswork:
    # the sequential numbering stays, and the doubt is written into the file.
    lying_base = os.path.join(workspace, "LYING")
    handle = io.open(lying_base + ".xml", "w", encoding="utf-8")
    handle.write(
        read(NATIVE_FIXTURE).replace(
            '<Single Name="OutCommented" Type="bool">True</Single>',
            '<Single Name="OutCommented" Type="bool">False</Single>',
        )
    )
    handle.close()
    lying = FakePou("LYING", os.path.join(FIXTURES, "LDTesting.xml"))
    check("a misaligned pou still renders", graphical_export.write_rendered_text(lying, lying_base) is True)
    lying_content = read(lying_base + ".txt")
    check("the misalignment is written into the file", "could not align" in lying_content)
    check("numbering falls back to sequential", "(* Network 1 *)" in lying_content)
    check_equal("the misalignment is counted", graphical_export.STATS["alignment_failures"], 1)
    check("the summary reports the misalignment", "may not match the CODESYS editor" in graphical_export.summary())

    # No native xml at all - the CLI path - must render exactly as before.
    plain_base = os.path.join(workspace, "PLAIN")
    plain = FakePou("PLAIN", os.path.join(FIXTURES, "LDTesting.xml"))
    check("no native xml still renders", graphical_export.write_rendered_text(plain, plain_base) is True)
    plain_content = read(plain_base + ".txt")
    check("sequential numbering without a native list", "(* Network 1 *)" in plain_content)
    check("no warning without a native list", "could not align" not in plain_content)

    graphical_export.reset_stats()
finally:
    shutil.rmtree(workspace)


# --- read-only service exports: library list and visualisation manager ------

# Library behaviour is not exportable, but which exact versions the project
# resolves is - and a bench check of a library is only meaningful against the
# version it characterises. The visualisation manager carries the global
# hotkey mapping nothing else exports; importing it raises interactive
# overwrite dialogs, so it exports read-only under a suffix the importer
# ignores by construction.


class FakeReference(object):
    def __init__(self, display_name=None, name=None, version=None, company=None):
        if display_name is not None:
            self.display_name = display_name
        if name is not None:
            self.name = name
        if version is not None:
            self.version = version
        if company is not None:
            self.company = company

    def __str__(self):
        return "raw reference"


class FakeLibManager(object):
    def __init__(self, name, references=None, names=None):
        self._name = name
        if references is not None:
            self.references = references
        self._names = names

    def get_name(self):
        return self._name

    def get_libraries(self):
        if self._names is None:
            raise RuntimeError("no such API on this build")
        return self._names


class FakeVisuManager(object):
    def __init__(self, name):
        self._name = name
        self.calls = []

    def get_name(self):
        return self._name

    def export_native(self, path, recursive=False):
        self.calls.append((path, recursive))
        handle = io.open(path, "w", encoding="utf-8")
        handle.write(u"<ExportFile />\n")
        handle.close()


workspace = tempfile.mkdtemp()
try:
    manager = FakeLibManager(
        "Library Manager",
        references=[
            # A display name that already carries version and company must not
            # have them appended again.
            FakeReference(
                display_name="ifmIOcommon, 1.5.0.0 (ifm electronic gmbh)",
                version="1.5.0.0",
                company="ifm electronic gmbh",
            ),
            FakeReference(name="Standard", version="3.5.11.0", company="3S"),
            FakeReference(),  # nothing probeable - falls back to str()
        ],
    )
    import_export.export_library_manager(manager, None, workspace, None)
    lib_list_path = os.path.join(workspace, "Library Manager.libraries.txt")
    check("the library list is written", os.path.exists(lib_list_path))
    lib_list = read(lib_list_path)
    check("the list says it is read-only", "read-only" in lib_list)
    check("a display name is taken verbatim", "ifmIOcommon, 1.5.0.0 (ifm electronic gmbh)\n" in lib_list)
    check("embedded details are not duplicated", lib_list.count("1.5.0.0") == 1)
    check("probed details are assembled", "Standard, 3.5.11.0 (3S)" in lib_list)
    check("an opaque reference still lands as a line", "raw reference" in lib_list)

    # An older build without .references still exports via get_libraries.
    named_only = FakeLibManager("Library Manager", names=["OldLib, 1.0.0.0 (Vendor)"])
    named_dir = tempfile.mkdtemp()
    try:
        import_export.export_library_manager(named_only, None, named_dir, None)
        named_list = read(os.path.join(named_dir, "Library Manager.libraries.txt"))
        check("get_libraries is the fallback", "OldLib, 1.0.0.0 (Vendor)" in named_list)
    finally:
        shutil.rmtree(named_dir)

    # A manager exposing neither API must warn, not raise, and write nothing.
    broken_dir = tempfile.mkdtemp()
    try:
        broken = FakeLibManager("Library Manager")
        try:
            import_export.export_library_manager(broken, None, broken_dir, None)
            check("a hostile lib manager is reported, not raised", True)
        except Exception as error:
            check("a hostile lib manager is reported, not raised", False, repr(error))
        check("no library list is written for it", os.listdir(broken_dir) == [])
    finally:
        shutil.rmtree(broken_dir)

    visu = FakeVisuManager("Visualization Manager")
    import_export.export_visualisation_manager(visu, None, workspace, None)
    service_path = os.path.join(workspace, "Visualization Manager.service.txt")
    check("the visualisation manager is exported", os.path.exists(service_path))
    # The key configuration and target/web visualisations live under the
    # manager, so a flat export would miss the hotkey mapping entirely.
    check_equal("the manager export is recursive", visu.calls[0][1], True)

    # The safety property behind the separate SERVICE table: import must never
    # remove these objects, because nothing would recreate them.
    class FakeTrackedObject(object):
        def __init__(self, type_guid):
            self.type = type_guid
            self.removed = False

        def get_name(self):
            return "service object"

        def remove(self):
            self.removed = True

    lib_manager_obj = FakeTrackedObject("adb5cb65-8e1d-4a00-b70a-375ea27582f3")
    visu_manager_obj = FakeTrackedObject("4d3fdb8f-ab50-4c35-9d3a-d4bb9bb9a628")
    import_export.remove_tracked_objects([lib_manager_obj, visu_manager_obj])
    check("import does not remove the library manager", not lib_manager_obj.removed)
    check("import does not remove the visualisation manager", not visu_manager_obj.removed)
finally:
    shutil.rmtree(workspace)


# --- the importer ignores the derived file ---------------------------------

# This is the contract that keeps the round trip intact. import_directory_child
# dispatches on ".xml" and ".st"; a ".txt" matches no branch. Asserting it here
# means a later change to that dispatch cannot silently start importing
# derived files.
workspace = tempfile.mkdtemp()
try:
    for name in (
        "Main.txt",
        "Main.Method.txt",
        "Main.gvl.txt",
        "Library Manager.libraries.txt",
        "Visualization Manager.service.txt",
    ):
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
