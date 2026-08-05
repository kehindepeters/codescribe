# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Tests for the Function Block Diagram renderer and the ST emitter.

Plain script rather than pytest, matching tools/ci/, so it runs under both
Python 3 and the IronPython 2.7 that CODESYS embeds.

    python tools/ladder/tests/test_fbd.py
"""

from __future__ import print_function, unicode_literals

import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# The renderers live in src/ so CODESYS can load them; tools/ladder keeps only
# the dev CLI and these tests.
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "src"))
sys.path.insert(0, os.path.join(HERE, ".."))

import charset  # noqa: E402
import fbd_render  # noqa: E402
import parse_ld  # noqa: E402
import parse_fbd  # noqa: E402
import st_render  # noqa: E402
from model import Call, Signal  # noqa: E402
from render import write  # noqa: E402

# Referenced through the charset table rather than as literal glyphs: this
# source file has to stay pure ASCII for IronPython 2.7 to load it at all.
U = charset.UNICODE

FIXTURES = os.path.join(HERE, "fixtures", "codesys")
FBD_SOURCE = os.path.join(FIXTURES, "FbTesting.xml")
LD_SOURCE = os.path.join(FIXTURES, "LDTesting.xml")
SFC_SOURCE = os.path.join(FIXTURES, "SFCTesting.xml")

failures = []


def check(name, condition, detail=""):
    if condition:
        print("OK      " + name)
    else:
        failures.append(name)
        print("FAIL    " + name + ((": " + detail) if detail else ""))


def check_equal(name, actual, expected):
    check(name, actual == expected, "expected %r, got %r" % (expected, actual))


def check_golden(name, rendered, golden_path):
    # Goldens hold box-drawing characters, so the encoding cannot be left to
    # the platform default - and neither can printing them on a mismatch.
    handle = io.open(golden_path, encoding="utf-8")
    try:
        expected = handle.read().replace("\r\n", "\n").rstrip("\n").split("\n")
    finally:
        handle.close()
    if rendered != expected:
        write(["--- expected ---"] + expected + ["--- actual ---"] + rendered)
    check_equal(name, rendered, expected)


# --- parsing ---------------------------------------------------------------

pous = parse_fbd.parse_pous(FBD_SOURCE)
check_equal("one FBD pou is found", len(pous), 1)

pou = pous[0]
check_equal("pou name", pou.name, "FB_TESTING")
check_equal("language is recorded", pou.language, "FBD")
check_equal("two networks", len(pou.networks), 2)

# localVars constant="true" is a separate group and must not merge with VAR.
check_equal("constant scope", pou.variables[0].scope, "VAR CONSTANT")
check_equal("constant initial value", pou.variables[0].initial_value, "5000")
check_equal("namespaced derived type", pou.variables[1].type_name, "ifmIOcommon.SystemSupply")

# Comments carry the network's intent and nest their text in an xhtml element.
comment1, tree1 = pou.networks[0]
check("network 1 comment is captured", comment1.startswith("// Function Block to monitor supply voltage"))

check("network 1 is a call", isinstance(tree1, Call))
check_equal("network 1 instance", tree1.instance_name, "fbSystemSupply")
check_equal("network 1 type", tree1.type_name, "ifmIOcommon.SystemSupply")
check_equal("network 1 has three inputs", len(tree1.inputs), 3)
check_equal("first pin name", tree1.inputs[0][0], "eChannel")
check("first pin source is a signal", isinstance(tree1.inputs[0][1], Signal))
check_equal("first pin value", tree1.inputs[0][1].label, "ifmIOcommon.SYS_VOLTAGE_CHANNEL.VBB15")

# An unconnected pin exports as an element with an empty expression, not as a
# missing element - so it reaches the tree as a Signal carrying no label.
check_equal("unwired pin name", tree1.inputs[2][0], "eFilter")
check_equal("unwired pin has an empty label", tree1.inputs[2][1].label, "")
check("unwired pin is not drawn with a wire", not fbd_render._is_wired(tree1.inputs[2][1]))

# CODESYS writes an assignment straight onto the output pin.
outputs = dict(tree1.outputs)
check_equal("output assignment is captured", outputs["uiOutVoltage"], "uiCurrSupplyVolt")

# Network 2 nests three calls: GT -> TOF -> SupplySwitch.
comment2, tree2 = pou.networks[1]
check_equal("network 2 root", tree2.instance_name, "fbSupplySwitch")
tof = tree2.inputs[1][1]
check_equal("nested TOF", tof.instance_name, "TOF_0")
check_equal("wire leaves TOF on Q", tof.active_output, "Q")
gt = tof.inputs[0][1]
check_equal("nested GT", gt.type_name, "GT")

# An operator has no instance name, so it inlines as an expression in ST.
check("GT is an operator", gt.is_operator)
check("TOF is not an operator", not tof.is_operator)
check_equal("operator title omits the instance", gt.title, "GT")
check_equal("function block title includes it", tof.title, "TOF_0 : TOF")

# --- FBD rendering ---------------------------------------------------------

art = fbd_render.render_pou(pou)
check("art: no trailing whitespace", all(line == line.rstrip() for line in art))
check("art: boxes do not fuse together", not any(U["TR"] + U["TL"] in line for line in art))
check("art: output assignment is drawn", any("uiOutVoltage => uiCurrSupplyVolt" in line for line in art))
check("art: nested operator box is drawn", any(U["PIN_L"] + "In1   Out1" + U["PIN_R"] in line for line in art))

# A tee marks a real connection, so an unconsumed output must leave the wall
# unbroken. fbSupplySwitch is a network sink: nothing takes its xError.
check("art: sink output is not teed", any("xError" + U["V"] in line for line in art))
check("art: consumed output is teed", any("Out1" + U["PIN_R"] in line for line in art))

# Every position in this export is x="0" y="0". If layout depended on those
# coordinates the three boxes would land on top of each other, so finding each
# title on its own distinct row is what proves layout comes from topology.
title_rows = {}
for row, line in enumerate(art):
    for title in ("GT", "TOF_0 : TOF", "fbSupplySwitch : ifmIOcommon.SupplySwitch"):
        if title in line and title not in title_rows:
            title_rows[title] = row
check_equal("art: all three boxes are placed", len(title_rows), 3)
check_equal("art: no two boxes share a row", len(set(title_rows.values())), 3)

check_golden("art: golden output matches", art, os.path.join(FIXTURES, "FbTesting.art.expected.txt"))

# --- ST emission -----------------------------------------------------------

fbd_st = st_render.render_pou(pou)

SUPPLY_CALL = (
    "fbSystemSupply(eChannel := ifmIOcommon.SYS_VOLTAGE_CHANNEL.VBB15,"
    " eMode := ifmIOcommon.MODE_SYSTEM_SUPPLY.SYS_SUPPLY);"
)
SWITCH_CALL = "fbSupplySwitch(eMode := ifmIOcommon.MODE_SUPPLY_SWITCH.SYS_SUPPLY_SWITCH, xValue := TOF_0.Q);"

check("st: function block becomes a call statement", SUPPLY_CALL in fbd_st)
check("st: output assignment becomes its own statement", "uiCurrSupplyVolt := fbSystemSupply.uiOutVoltage;" in fbd_st)
check("st: comparison operator inlines infix", "TOF_0(IN := uiCurrSupplyVolt > uiMinVoltage, PT := T#5S);" in fbd_st)
check("st: nested output is referenced by pin", SWITCH_CALL in fbd_st)
check("st: unwired pin is omitted", not any("eFilter" in line for line in fbd_st))

check_golden("st: FBD golden matches", fbd_st, os.path.join(FIXTURES, "FbTesting.st.expected.txt"))

ld_pou = parse_ld.parse_pous(LD_SOURCE)[0]
ld_st = st_render.render_pou(ld_pou)
check("st: parallel branch becomes OR", "IF (Sensor1 OR sensor3) AND NOT Sensor2 THEN PowerOn := TRUE; END_IF" in ld_st)
check("st: ladder block becomes a call", "TON_0(IN := PowerOn, PT := T#5S);" in ld_st)
check("st: block chains through its output pin", "CTU_0(CU := TON_0.Q, RESET := PowerOff, PV := 10);" in ld_st)
check("st: reset coil becomes a conditional", "IF CTU_0.Q THEN PowerOff := FALSE; END_IF" in ld_st)

check_golden("st: LD golden matches", ld_st, os.path.join(FIXTURES, "LDTesting.st.expected.txt"))

# --- control flow ----------------------------------------------------------

# Everything below was silently dropped before, which is worse than failing:
# the rendering looked complete while a guard clause and a body of inline ST
# were simply absent.
CONTROL_FLOW = os.path.join(HERE, "fixtures", "fbd_control_flow.plcopen.xml")
flow = parse_fbd.parse_pous(CONTROL_FLOW)[0]
flow_st = st_render.render_pou(flow)
flow_art = fbd_render.render_pou(flow)

check_equal("flow: four networks survive", len(flow.networks), 4)

# A jump terminates a network. Leaving it out of SINK_KINDS dropped the entire
# guard network, because nothing else consumed the OR feeding it.
check("flow: the guard network is not dropped", any("JMP END" in line for line in flow_st))
check("flow: the jump condition is kept", any("Mode.Current = Mode.ESTOP" in line for line in flow_st))
check("flow: the jump target is drawn", any(">> END" in line for line in flow_art))
check("flow: the label is shown", any("(* label: END *)" in line for line in flow_st))

# negated="true" on an inVariable inverts the logic if it is ignored.
guard = flow.networks[0][1]
check_equal("flow: negation reaches the tree", guard.condition.inputs[0][1].negated, True)
check_equal("flow: negation renders", guard.condition.inputs[0][1].text, "NOT xInitDone")
check("flow: negation survives into ST", any("(NOT xInitDone) OR" in line for line in flow_st))

# An EXECUTE box is nothing but inline ST; drawing the box alone loses it all.
execute = flow.networks[3][1]
check_equal("flow: inline ST is captured", len(execute.st_code), 4)
check("flow: inline ST reaches the ST output", any("Status.Faulted := FALSE;" in line for line in flow_st))
# The EN pin genuinely guards the box, so it has to show up as a condition
# rather than being dropped for looking redundant.
check("flow: the EN guard wraps the inline ST", any(line == "IF xInitDone THEN" for line in flow_st))
check("flow: inline ST reaches the diagram", any("Status.Faulted := FALSE;" in line for line in flow_art))

# Operators read as operators, not as function calls.
check("flow: arithmetic inlines infix", any("RawPressure / 100" in line for line in flow_st))
check("flow: conversions stay function calls", any("REAL_TO_UINT(" in line for line in flow_st))
check(
    "flow: compound operands are bracketed",
    any("(NOT xInitDone) OR (Mode.Current = Mode.ESTOP)" in line for line in flow_st),
)


# --- language dispatch -----------------------------------------------------

check_equal("LD parser ignores FBD bodies", parse_ld.parse_pous(FBD_SOURCE), [])
check_equal("FBD parser ignores LD bodies", parse_fbd.parse_pous(LD_SOURCE), [])
check_equal("SFC is skipped by both", parse_ld.parse_pous(SFC_SOURCE) + parse_fbd.parse_pous(SFC_SOURCE), [])

print("")
if failures:
    print("%d check(s) failed" % len(failures))
else:
    print("all checks passed")
sys.exit(1 if failures else 0)
