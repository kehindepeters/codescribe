# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Render a parsed Ladder Diagram as rungs.

Layout comes from the expression tree only - the x/y coordinates in the source
XML are deliberately ignored. Dragging a contact sideways in CODESYS must not
show up as a diff.

Composition works on Blocks: a rectangle of text plus the row index its wire
enters and leaves on. Series concatenates Blocks horizontally aligned on that
row; Parallel stacks them and threads a junction column down each side.

Drawing characters come from charset, so the same layout renders as either
box-drawing Unicode or plain ASCII.
"""

from __future__ import unicode_literals

import charset
from layout import Block
from model import BLOCK, COIL, CONTACT, Element, Empty, Parallel, Series

POU_TYPE_KEYWORDS = {
    "program": "PROGRAM",
    "functionBlock": "FUNCTION_BLOCK",
    "function": "FUNCTION",
}


def _symbol_and_label(element):
    """The drawn symbol, and the caption sitting above it."""
    chars = charset.active()
    kind = element.kind

    if kind == CONTACT:
        if element.edge == "rising":
            middle = "P"
        elif element.edge == "falling":
            middle = "N"
        elif element.negated:
            middle = "/"
        else:
            middle = " "
        return chars["CONTACT_L"] + middle + chars["CONTACT_R"], element.label or ""

    if kind == COIL:
        if element.storage == "set":
            middle = "S"
        elif element.storage == "reset":
            middle = "R"
        elif element.negated:
            middle = "/"
        else:
            middle = " "
        return "(" + middle + ")", element.label or ""

    if kind == "jump":
        return ">>" + (element.label or "?"), ""

    if kind == "return":
        return "<RETURN>", ""

    if kind == "label":
        # A jump target: a marker in the rung order, not a symbol on a wire.
        return (element.label or "?") + ":", ""

    # In/out variables and anything unrecognised draw as a named box so
    # unhandled logic is visible rather than silently dropped. A negated
    # variable spells its NOT out - there is no bubble to draw on a box.
    label = element.label or "?"
    if element.negated:
        label = "NOT " + label
    return "[" + label + "]", ""


def _render_block(element):
    """Draw a function block as a pin box.

    The power pin sorts first, so the wire enters and leaves on the same row.
    Only pins that are genuinely wired get a tee on the box edge; a
    parameterised or unconsumed pin leaves the wall unbroken.
    """
    chars = charset.active()

    left = []
    wired = []
    for pin, label in element.input_pins:
        text = pin or "?"
        # A label of None is the power pin - it is wired, not parameterised.
        if label is not None:
            text += " := " + label if label else ""
        left.append(text)
        wired.append(label is None)

    right = []
    for pin, assigned in element.output_pins:
        text = pin or "?"
        if assigned:
            # =o> is => with the negation bubble: the pin stores its inverse.
            text += (" =o> " if pin in element.negated_outputs else " => ") + assigned
        elif pin in element.negated_outputs:
            text += " o"
        right.append(text)

    rows = max(len(left), len(right), 1)
    left += [""] * (rows - len(left))
    wired += [False] * (rows - len(wired))
    right += [""] * (rows - len(right))

    title = element.title
    inner = max([len(title)] + [len(left[i]) + 3 + len(right[i]) for i in range(rows)])

    lines = [title.center(inner + 2)]
    lines.append(chars["TL"] + chars["H"] * inner + chars["TR"])
    for index in range(rows):
        gap = inner - len(left[index]) - len(right[index])
        left_edge = chars["PIN_L"] if wired[index] else chars["V"]
        if wired[index] and element.power_negated:
            # The negation bubble on the power pin, drawn on the box wall.
            left_edge = "o"
        # Only the active output continues onward, and only if consumed.
        right_edge = chars["PIN_R"] if (index == 0 and element.output_wired) else chars["V"]
        if index == 0 and element.output_wired and element.active_output in element.negated_outputs:
            right_edge = "o"
        lines.append(left_edge + left[index] + " " * gap + right[index] + right_edge)
    lines.append(chars["BL"] + chars["H"] * inner + chars["BR"])

    # Row 0 is the title and row 1 the top border, so the first pin is row 2.
    connect_row = 2

    # A lead-in and lead-out stub, so back-to-back boxes do not fuse into one
    # unreadable run of border characters.
    stubbed = []
    for index, line in enumerate(lines):
        stub = chars["H"] if index == connect_row else " "
        stubbed.append(stub + line + stub)

    return Block(stubbed, connect_row)


def _render_element(element):
    if element.kind == BLOCK:
        return _render_block(element)

    chars = charset.active()
    symbol, label = _symbol_and_label(element)
    width = max(len(label) + 2, len(symbol) + 4)

    lead = (width - len(symbol)) // 2
    symbol_line = chars["H"] * lead + symbol + chars["H"] * (width - len(symbol) - lead)

    lead = (width - len(label)) // 2
    label_line = " " * lead + label + " " * (width - len(label) - lead)

    return Block([label_line, symbol_line], 1)


def _render_series(items):
    blocks = [_render(item) for item in items]
    connect_row = max(block.connect_row for block in blocks)
    height = max(connect_row - block.connect_row + len(block.lines) for block in blocks)

    columns = []
    for block in blocks:
        width = block.width
        above = connect_row - block.connect_row
        lines = [" " * width] * above
        lines += [line.ljust(width) for line in block.lines]
        lines += [" " * width] * (height - len(lines))
        columns.append(lines)

    joined = []
    for row in range(height):
        joined.append("".join(column[row] for column in columns))
    return Block(joined, connect_row)


def _render_parallel(branches):
    chars = charset.active()
    blocks = [_render(branch) for branch in branches]
    width = max(block.width for block in blocks)

    stacked = []
    connect_rows = []
    for block in blocks:
        connect_rows.append(len(stacked) + block.connect_row)
        for index, line in enumerate(block.lines):
            # The wire itself extends horizontally; everything else with
            # spaces, so short branches still reach the junction on the right.
            fill = chars["H"] if index == block.connect_row else " "
            stacked.append(line + fill * (width - len(line)))

    junctions = set(connect_rows)
    first, last = connect_rows[0], connect_rows[-1]

    lines = []
    for row, line in enumerate(stacked):
        if row == first:
            # The main line carries straight on and drops a branch downward.
            left, right = chars["T_DOWN"], chars["T_DOWN"]
        elif row == last:
            left, right = chars["BL"], chars["BR"]
        elif row in junctions:
            left, right = chars["T_RIGHT"], chars["T_LEFT"]
        elif first < row < last:
            left = right = chars["V"]
        else:
            left = right = " "
        lines.append(left + line + right)

    return Block(lines, first)


def _render(expr):
    chars = charset.active()
    if isinstance(expr, Empty):
        return Block(["   ", chars["H"] * 3], 1)
    if isinstance(expr, Element):
        return _render_element(expr)
    if isinstance(expr, Series):
        return _render_series(expr.items)
    if isinstance(expr, Parallel):
        return _render_parallel(expr.branches)
    raise TypeError("cannot render %r" % (expr,))


def render_rung(expr):
    """Render one rung, bounded by the power rails."""
    chars = charset.active()
    block = _render(expr)
    lines = []
    for row, line in enumerate(block.lines):
        if row == block.connect_row:
            lines.append(chars["T_RIGHT"] + chars["H"] * 2 + line + chars["H"] * 2 + chars["T_LEFT"])
        else:
            lines.append(chars["V"] + "  " + line)
    return lines


def render_declaration(pou):
    keyword = POU_TYPE_KEYWORDS.get(pou.pou_type, "PROGRAM")
    lines = [keyword + " " + pou.name]

    scope = None
    for variable in pou.variables:
        if variable.scope != scope:
            if scope is not None:
                lines.append("END_VAR")
            lines.append(variable.scope)
            scope = variable.scope
        entry = "    " + variable.name + " : " + variable.type_name
        if variable.initial_value is not None:
            entry += " := " + variable.initial_value
        lines.append(entry + ";")
    if scope is not None:
        lines.append("END_VAR")

    return lines


def render_pou(pou):
    """Render a whole POU: declaration, then one block per rung."""
    lines = render_declaration(pou)
    lines.append("")

    if not pou.rungs:
        lines.append("(* no rungs *)")

    for index, rung in enumerate(pou.rungs):
        lines.append("(* Network " + str(index + 1) + " *)")
        lines.extend(render_rung(rung))
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()

    # Trailing whitespace is an artefact of grid composition, and the repo's
    # pre-commit hooks would strip it anyway.
    return [line.rstrip() for line in lines]
