# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Tests for the Ladder Diagram renderer.

Written as a plain script rather than pytest, matching tools/ci/, so it runs
under both Python 3 and the IronPython 2.7 that CODESYS embeds. The renderer
is destined for src/ once it is proven, and it has to pass there too.

    python tools/ladder/tests/test_ladder.py
"""

from __future__ import print_function, unicode_literals

import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import charset  # noqa: E402
from ld_render import render_pou  # noqa: E402
from model import COIL, CONTACT, Element, Parallel, Series  # noqa: E402
from parse import parse_pous  # noqa: E402
from render import write  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")
SOURCE = os.path.join(FIXTURES, "motor_control.plcopen.xml")
EXPECTED = os.path.join(FIXTURES, "motor_control.expected.txt")

failures = []


def check(name, condition, detail=""):
    if condition:
        print("OK      " + name)
    else:
        failures.append(name)
        print("FAIL    " + name + ((": " + detail) if detail else ""))


def check_equal(name, actual, expected):
    check(name, actual == expected, "expected %r, got %r" % (expected, actual))


# --- parsing ---------------------------------------------------------------

pous = parse_pous(SOURCE)

check_equal("one LD pou is found", len(pous), 1)

pou = pous[0]
check_equal("pou name", pou.name, "Motor_Control")
check_equal("pou type", pou.pou_type, "program")
check_equal("interface variables", len(pou.variables), 8)
check_equal("derived type resolves to its name", pou.variables[-1].type_name, "TON")
check_equal("initial value is captured", pou.variables[2].initial_value, "FALSE")

check_equal("three rungs", len(pou.rungs), 3)

# Network 1: (Start_PB OR Motor_Run) AND NOT Stop_PB -> Motor_Run
rung1 = pou.rungs[0]
check("rung 1 is a series", isinstance(rung1, Series))
check_equal("rung 1 has three stages", len(rung1.items), 3)
check("rung 1 opens with a parallel branch", isinstance(rung1.items[0], Parallel))
check_equal("seal-in has two branches", len(rung1.items[0].branches), 2)
check_equal("first branch is Start_PB", rung1.items[0].branches[0].label, "Start_PB")
check_equal("second branch is Motor_Run", rung1.items[0].branches[1].label, "Motor_Run")
check("Stop_PB is negated", rung1.items[1].negated)
check_equal("Stop_PB is a contact", rung1.items[1].kind, CONTACT)
check_equal("rung 1 terminates in a coil", rung1.items[2].kind, COIL)
check_equal("coil drives Motor_Run", rung1.items[2].label, "Motor_Run")

# The right power rail anchors the rung but must not become a drawn element.
check(
    "right power rail is not drawn",
    all(not (isinstance(i, Element) and i.kind.endswith("PowerRail")) for i in rung1.items),
)

# Network 2: rising edge into a set coil
rung2 = pou.rungs[1]
check_equal("rising edge is captured", rung2.items[0].edge, "rising")
check_equal("set coil storage", rung2.items[1].storage, "set")

# Network 3: terminal is the coil itself, with no right power rail
rung3 = pou.rungs[2]
check_equal("rung 3 has three stages", len(rung3.items), 3)
check_equal("reset coil storage", rung3.items[2].storage, "reset")

# --- layout independence ---------------------------------------------------

handle = io.open(SOURCE, encoding="utf-8")
try:
    source_text = handle.read()
finally:
    handle.close()

# Shifting every element 500px right must not change a single character of
# output. This is the property that keeps diffs meaningful.
moved = source_text.replace('<position x="', '<position x="9')
moved_pou = None
try:
    import io

    # Bytes, not text: the fixture carries an encoding declaration, which
    # ElementTree refuses to parse from an already-decoded stream.
    moved_pou = parse_pous(io.BytesIO(moved.encode("utf-8")))[0]
except Exception as error:  # pragma: no cover - diagnostic path
    check("moved fixture parses", False, repr(error))

if moved_pou is not None:
    check_equal("layout changes do not affect output", render_pou(moved_pou), render_pou(pou))

# --- rendering -------------------------------------------------------------

rendered = render_pou(pou)

check("no trailing whitespace", all(line == line.rstrip() for line in rendered))
check("declaration comes first", rendered[0] == "PROGRAM Motor_Control")

# Referenced through the charset table rather than as literal glyphs: this
# source file has to stay pure ASCII for IronPython 2.7 to load it at all.
U = charset.UNICODE

# The seal-in branch closes on its own row: a bottom-left corner, a contact,
# and a bottom-right corner. Matching the shape rather than an exact wire
# length keeps this from breaking every time a variable is renamed.
branch_rows = [line for line in rendered if U["BL"] in line and U["BR"] in line]
check_equal("exactly one branch closes", len(branch_rows), 1)
check("seal-in branch holds a contact", U["CONTACT_L"] in branch_rows[0])
check("branch opens with a tee", any(U["T_DOWN"] in line for line in rendered))
check("negated contact is drawn", any(U["CONTACT_L"] + "/" + U["CONTACT_R"] in line for line in rendered))
check("rising edge contact is drawn", any(U["CONTACT_L"] + "P" + U["CONTACT_R"] in line for line in rendered))
check("set coil is drawn", any("(S)" in line for line in rendered))
check("reset coil is drawn", any("(R)" in line for line in rendered))


# --- character sets --------------------------------------------------------

# The ASCII set exists for terminals and diff viewers that mangle box drawing,
# so its defining property is that nothing in the output is non-ASCII.
charset.use("ascii")
try:
    ascii_rendered = render_pou(pou)
finally:
    charset.use("unicode")

check("ascii charset emits no non-ASCII", all(ord(ch) < 128 for line in ascii_rendered for ch in line))
check("ascii charset still draws the branch", any("+----| |----+" in line for line in ascii_rendered))
check("unicode is restored afterwards", any(U["V"] in line for line in render_pou(pou)))
check_equal("both charsets produce the same shape", len(ascii_rendered), len(rendered))

def check_golden(name, rendered_lines, golden_path):
    # Goldens hold box-drawing characters, so the encoding cannot be left to
    # the platform default - and neither can printing them on a mismatch.
    handle = io.open(golden_path, encoding="utf-8")
    try:
        expected_lines = handle.read().replace("\r\n", "\n").rstrip("\n").split("\n")
    finally:
        handle.close()
    if rendered_lines != expected_lines:
        write(["--- expected ---"] + expected_lines + ["--- actual ---"] + rendered_lines)
    check_equal(name, rendered_lines, expected_lines)


check_golden("golden output matches", rendered, EXPECTED)


# --- real CODESYS export ---------------------------------------------------

# Exported from CODESYS V3.5 SP11 via Project > Export > PLCopenXML. This is
# the dialect that actually matters; the hand-authored fixture above only
# covers what the spec says.
CODESYS_SOURCE = os.path.join(FIXTURES, "codesys", "LDTesting.xml")
CODESYS_EXPECTED = os.path.join(FIXTURES, "codesys", "LDTesting.expected.txt")

codesys_pous = parse_pous(CODESYS_SOURCE)
check_equal("codesys: one LD pou", len(codesys_pous), 1)

ld_test = codesys_pous[0]
check_equal("codesys: pou name", ld_test.name, "LD_TEST")
check_equal("codesys: two networks", len(ld_test.rungs), 2)

# CODESYS writes edge="none"/storage="none" rather than omitting the attribute.
network1 = ld_test.rungs[0]
check_equal("codesys: literal 'none' edge is normalised away", network1.items[1].edge, None)
check("codesys: negated contact survives", network1.items[1].negated)
check_equal("codesys: set coil", network1.items[2].storage, "set")

# typeName and instanceName are attributes in CODESYS's output. Reading them as
# child elements is what produced "[?]" boxes on the first run.
network2 = ld_test.rungs[1]
blocks = [item for item in network2.items if getattr(item, "kind", None) == "block"]
check_equal("codesys: two blocks in network 2", len(blocks), 2)
check_equal("codesys: block type name", blocks[0].type_name, "TON")
check_equal("codesys: block instance name", blocks[0].instance_name, "TON_0")
check_equal("codesys: block title", blocks[0].title, "TON_0 : TON")

# The power pin sorts first and carries no caption; parameter pins carry one.
check_equal("codesys: TON power pin is IN", blocks[0].input_pins[0], ("IN", None))
check_equal("codesys: TON PT is a parameter", blocks[0].input_pins[1], ("PT", "T#5S"))

# A second wired input cannot be drawn as another horizontal wire, so it is
# flattened to text inside the pin.
check_equal("codesys: CTU power pin is CU", blocks[1].input_pins[0], ("CU", None))
check_equal("codesys: CTU RESET is flattened to text", blocks[1].input_pins[1], ("RESET", "PowerOff"))
check_equal("codesys: CTU PV is a literal", blocks[1].input_pins[2], ("PV", "10"))

# The consumer's connection names the output pin it draws from.
check_equal("codesys: active output follows the wire", blocks[0].active_output, "Q")
check_equal("codesys: active output sorts first", blocks[0].output_pins[0][0], "Q")

codesys_rendered = render_pou(ld_test)
check("codesys: no trailing whitespace", all(line == line.rstrip() for line in codesys_rendered))
check_golden("codesys: golden output matches", codesys_rendered, CODESYS_EXPECTED)

print("")
if failures:
    print("%d check(s) failed" % len(failures))
else:
    print("all checks passed")
sys.exit(1 if failures else 0)
