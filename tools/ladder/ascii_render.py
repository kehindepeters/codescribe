# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Render a parsed Ladder Diagram as ASCII rungs.

Layout comes from the expression tree only - the x/y coordinates in the source
XML are deliberately ignored. Dragging a contact sideways in CODESYS must not
show up as a diff.

Composition works on Blocks: a rectangle of text plus the row index its wire
enters and leaves on. Series concatenates Blocks horizontally aligned on that
row; Parallel stacks them and threads a junction column down each side.
"""

from model import BLOCK, COIL, CONTACT, Element, Empty, Parallel, Series

POU_TYPE_KEYWORDS = {
    "program": "PROGRAM",
    "functionBlock": "FUNCTION_BLOCK",
    "function": "FUNCTION",
}


class Block(object):
    def __init__(self, lines, connect_row):
        self.lines = lines
        self.connect_row = connect_row

    @property
    def width(self):
        if not self.lines:
            return 0
        return max(len(line) for line in self.lines)


def _symbol_and_label(element):
    """The drawn symbol, and the caption sitting above it."""
    kind = element.kind

    if kind == CONTACT:
        if element.edge == "rising":
            symbol = "|P|"
        elif element.edge == "falling":
            symbol = "|N|"
        elif element.negated:
            symbol = "|/|"
        else:
            symbol = "| |"
        return symbol, element.label or ""

    if kind == COIL:
        if element.storage == "set":
            symbol = "(S)"
        elif element.storage == "reset":
            symbol = "(R)"
        elif element.negated:
            symbol = "(/)"
        else:
            symbol = "( )"
        return symbol, element.label or ""

    if kind == "jump":
        return ">>" + (element.label or "?"), ""

    if kind == "return":
        return "<RETURN>", ""

    # In/out variables and anything unrecognised draw as a named box so
    # unhandled logic is visible rather than silently dropped.
    return "[" + (element.label or "?") + "]", ""


def _render_block(element):
    """Draw a function block as a pin box.

        TON_0 : TON
       +-----------+
    ---|IN        Q|---
       |PT := T#5S |
       +-----------+

    The power pin sorts first, so the wire enters and leaves on the same row.
    """
    left = []
    for pin, label in element.input_pins:
        text = pin or "?"
        # A label of None is the power pin - it is wired, not parameterised.
        if label is not None:
            text += " := " + label if label else ""
        left.append(text)

    right = []
    for pin, assigned in element.output_pins:
        text = pin or "?"
        if assigned:
            text += " => " + assigned
        right.append(text)

    rows = max(len(left), len(right), 1)
    left += [""] * (rows - len(left))
    right += [""] * (rows - len(right))

    title = element.title
    inner = max([len(title)] + [len(left[i]) + 3 + len(right[i]) for i in range(rows)])

    lines = [title.center(inner + 2)]
    lines.append("+" + "-" * inner + "+")
    for index in range(rows):
        gap = inner - len(left[index]) - len(right[index])
        lines.append("|" + left[index] + " " * gap + right[index] + "|")
    lines.append("+" + "-" * inner + "+")

    # Row 0 is the title and row 1 the top border, so the first pin is row 2.
    connect_row = 2

    # A lead-in and lead-out stub, so back-to-back boxes do not fuse into one
    # unreadable run of border characters.
    stubbed = []
    for index, line in enumerate(lines):
        stub = "-" if index == connect_row else " "
        stubbed.append(stub + line + stub)

    return Block(stubbed, connect_row)


def _render_element(element):
    if element.kind == BLOCK:
        return _render_block(element)

    symbol, label = _symbol_and_label(element)
    width = max(len(label) + 2, len(symbol) + 4)

    lead = (width - len(symbol)) // 2
    symbol_line = "-" * lead + symbol + "-" * (width - len(symbol) - lead)

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
    blocks = [_render(branch) for branch in branches]
    width = max(block.width for block in blocks)

    stacked = []
    connect_rows = []
    for block in blocks:
        connect_rows.append(len(stacked) + block.connect_row)
        for index, line in enumerate(block.lines):
            # The wire itself extends with dashes; everything else with spaces,
            # so short branches still reach the junction on the right.
            fill = "-" if index == block.connect_row else " "
            stacked.append(line + fill * (width - len(line)))

    junctions = set(connect_rows)
    first, last = connect_rows[0], connect_rows[-1]

    lines = []
    for row, line in enumerate(stacked):
        if row in junctions:
            edge = "+"
        elif first < row < last:
            edge = "|"
        else:
            edge = " "
        lines.append(edge + line + edge)

    return Block(lines, first)


def _render(expr):
    if isinstance(expr, Empty):
        return Block(["   ", "---"], 1)
    if isinstance(expr, Element):
        return _render_element(expr)
    if isinstance(expr, Series):
        return _render_series(expr.items)
    if isinstance(expr, Parallel):
        return _render_parallel(expr.branches)
    raise TypeError("cannot render %r" % (expr,))


def render_rung(expr):
    """Render one rung, bounded by the left power rail."""
    block = _render(expr)
    lines = []
    for row, line in enumerate(block.lines):
        if row == block.connect_row:
            lines.append("|--" + line + "--|")
        else:
            lines.append("|  " + line)
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
