# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Render a parsed Function Block Diagram as ASCII boxes.

Layout is derived from the call tree, not from the exported coordinates. Each
block's inputs are rendered to its left and stacked vertically, so a pin fed
by another block gets that block's whole box beside it. Pin rows are placed at
whatever row their source ended up on, which keeps every wire horizontal.
"""

from __future__ import unicode_literals

import charset
from layout import Block, stack
from ld_render import ALIGNMENT_WARNING, network_header, render_declaration
from model import Assign, Call, Jump, Label, Signal
import native_networks


def _is_label_network(network):
    """True for a network holding nothing but jump labels.

    CODESYS stores a network's label on the network itself, but PLCopen
    exports it as a free-standing element wired to nothing, so grouping by
    wiring gives it a component - and therefore a network - of its own. The
    native network list knows which real network each label belongs to; this
    predicate is how the alignment recognises the artefact.
    """
    return bool(network.outputs) and all(isinstance(tree, Label) for tree in network.outputs)


def _render_signal(node):
    return Block([node.text], 0)


def _render_label(node):
    return Block(["(* label: " + node.name + " *)"], 0)


def _render_jump(node):
    chars = charset.active()
    tail = chars["H"] * 3 + ">> " + (node.target or "?")
    if node.condition is None:
        return Block([tail], 0)
    source = _render(node.condition)
    lines = source.padded(source.width)
    out = []
    for index, line in enumerate(lines):
        out.append(line + tail if index == source.connect_row else line)
    return Block(out, source.connect_row)


def _render_assign(node):
    chars = charset.active()
    source = _render(node.source) if node.source is not None else Block([""], 0)
    lines = source.padded(source.width)
    # The negation circle CODESYS draws on the pin, as an "o" on the wire.
    head = "o> " if node.negated else "> "
    tail = chars["H"] * 3 + head + (node.label or "?")
    out = []
    for index, line in enumerate(lines):
        out.append(line + tail if index == source.connect_row else line)
    return Block(out, source.connect_row)


def _is_wired(source):
    """False for a pin CODESYS exported with no source, or an empty expression.

    Those must not be drawn with a wire running off to the left, because there
    is nothing out there feeding them.
    """
    if source is None:
        return False
    return not (isinstance(source, Signal) and not source.label)


def _render_call(call):
    chars = charset.active()
    input_blocks = []
    for _pin, source in call.inputs:
        input_blocks.append(_render(source) if source is not None else Block([""], 0))

    left_lines, pin_rows = stack(input_blocks)
    # A minimum lead-in, so a source exactly as wide as the column still shows
    # a wire and back-to-back boxes do not fuse into one run of borders.
    left_width = (max([len(line) for line in left_lines]) + 2) if left_lines else 0

    # Only the rows where a source hands off to a pin get their wire extended;
    # a nested box's own internal wires already end at that box's edge.
    handoff = set()
    for index, pin_and_source in enumerate(call.inputs):
        if _is_wired(pin_and_source[1]):
            handoff.add(pin_rows[index])

    left = []
    for index, line in enumerate(left_lines):
        fill = chars["H"] if index in handoff else " "
        left.append(line + fill * (left_width - len(line)))

    input_rows = list(pin_rows)
    output_rows = []
    for index in range(len(call.outputs)):
        if index < len(input_rows):
            output_rows.append(input_rows[index])
        else:
            # More outputs than inputs: the surplus hangs below the last pin.
            base = input_rows[-1] if input_rows else -1
            output_rows.append(base + index - len(input_rows) + 1)

    all_rows = (input_rows + output_rows) or [0]
    box_first, box_last = min(all_rows), max(all_rows)

    # The title and top border sit two rows above the first pin, so everything
    # shifts down if the first pin would land at the very top of the grid.
    shift = max(0, 2 - box_first)
    if shift:
        left = [" " * left_width] * shift + left
        input_rows = [row + shift for row in input_rows]
        output_rows = [row + shift for row in output_rows]
        box_first += shift
        box_last += shift

    in_at = {}
    for index, pin_and_source in enumerate(call.inputs):
        in_at[input_rows[index]] = pin_and_source[0] or "?"

    out_at = {}
    for index, pin_and_assignment in enumerate(call.outputs):
        pin, assigned = pin_and_assignment
        text = pin or "?"
        if assigned:
            # =o> is => with the negation bubble: the pin stores its inverse.
            text += (" =o> " if pin in call.negated_outputs else " => ") + assigned
        elif pin in call.negated_outputs:
            text += " o"
        out_at[output_rows[index]] = text

    title = call.title
    widths = [len(title)]
    for row in range(box_first, box_last + 1):
        widths.append(len(in_at.get(row, "")) + 3 + len(out_at.get(row, "")))
    inner = max(widths)

    height = max(len(left), box_last + 2)
    left += [" " * left_width] * (height - len(left))

    # handoff was computed before the shift; recompute against the final rows.
    handoff_pins = set()
    for index, pin_and_source in enumerate(call.inputs):
        if _is_wired(pin_and_source[1]):
            handoff_pins.add(input_rows[index])

    # The active output only breaks the box wall with a tee if a consumer is
    # actually there to receive it.
    pins = [pin for pin, _assigned in call.outputs]
    live_output_row = None
    if call.output_wired and call.active_output in pins:
        live_output_row = output_rows[pins.index(call.active_output)]

    lines = []
    for row in range(height):
        if row == box_first - 2:
            box = title.center(inner + 2)
        elif row == box_first - 1:
            box = chars["TL"] + chars["H"] * inner + chars["TR"]
        elif row == box_last + 1:
            box = chars["BL"] + chars["H"] * inner + chars["BR"]
        elif box_first <= row <= box_last:
            left_pin = in_at.get(row, "")
            right_pin = out_at.get(row, "")
            left_edge = chars["PIN_L"] if row in handoff_pins else chars["V"]
            right_edge = chars["PIN_R"] if row == live_output_row else chars["V"]
            gap = inner - len(left_pin) - len(right_pin)
            box = left_edge + left_pin + " " * gap + right_pin + right_edge
        else:
            box = " " * (inner + 2)
        lines.append(left[row] + box)

    # The wire leaves on whichever output pin the consumer asked for.
    connect_row = box_first
    pins = [pin for pin, _assigned in call.outputs]
    if call.active_output in pins:
        connect_row = output_rows[pins.index(call.active_output)]
    elif output_rows:
        connect_row = output_rows[0]

    return Block(lines, connect_row)


def _render(node):
    if isinstance(node, Call):
        return _render_call(node)
    if isinstance(node, Assign):
        return _render_assign(node)
    if isinstance(node, Jump):
        return _render_jump(node)
    if isinstance(node, Label):
        return _render_label(node)
    if isinstance(node, Signal):
        return _render_signal(node)
    raise TypeError("cannot render %r" % (node,))


def _assign_tail(node):
    chars = charset.active()
    # The negation circle CODESYS draws on the pin, as an "o" on the wire.
    return chars["H"] * 2 + ("o " if node.negated else "> ") + (node.label or "?")


def _render_fanout(outputs):
    """One source driving several outputs: draw it once and branch.

    This is how CODESYS shows it, and drawing the box once per output would
    both misrepresent the program and double the width of the diff.
    """
    chars = charset.active()
    source = _render(outputs[0].source)
    # A short lead before the junction, so the branch is not welded to the box
    # edge. padded() extends the wire row and pads the rest with spaces.
    width = source.width + 2
    lines = source.padded(width)

    rows = [source.connect_row + index for index in range(len(outputs))]
    while len(lines) <= rows[-1]:
        lines.append(" " * width)

    first, last = rows[0], rows[-1]
    out = []
    for row, line in enumerate(lines):
        if row == first:
            joint = chars["T_DOWN"] if len(rows) > 1 else chars["H"]
        elif row == last:
            joint = chars["BL"]
        elif row in rows:
            joint = chars["T_RIGHT"]
        elif first < row < last:
            joint = chars["V"]
        else:
            joint = " "
        tail = _assign_tail(outputs[rows.index(row)]) if row in rows else ""
        out.append(line + joint + tail)

    return Block(out, first)


def _shared_source(outputs):
    """The single source every output hangs off, or None.

    Identity, not equality: the parser memoises shared nodes, so two outputs
    fed by one block hold the very same object.
    """
    if len(outputs) < 2:
        return None
    if not all(isinstance(output, Assign) for output in outputs):
        return None
    first = outputs[0].source
    if first is None:
        return None
    return first if all(output.source is first for output in outputs) else None


def render_network(network):
    """Render one network, which may drive several outputs from one source."""
    outputs = getattr(network, "outputs", [network])

    if _shared_source(outputs) is not None:
        return _render_fanout(outputs).lines

    lines = []
    for tree in outputs:
        lines.extend(_render(tree).lines)
        # An EXECUTE box's body is the logic; drawing the box without it would
        # be an empty rectangle where a dozen lines of ST should be.
        if isinstance(tree, Call) and tree.st_code:
            lines = lines + [""] + ["    " + line for line in tree.st_code]
    return lines


def render_pou(pou):
    """Render a whole FBD POU: declaration, then one box tree per network.

    Numbering follows the native export's network list when one is attached
    to the pou: out-commented and empty networks keep their number and get a
    placeholder, because dropping them renumbers everything after them away
    from what the reviewer sees in CODESYS.
    """
    lines = render_declaration(pou)
    lines.append("")

    entries = native_networks.entries_for(pou, pou.networks, _is_label_network)
    if getattr(pou, "native_merge_failed", False):
        lines.append(ALIGNMENT_WARNING)
        lines.append("")

    if not entries:
        lines.append("(* no networks *)")

    # In the native-aligned case the native comment is the authority: a
    # parsed comment can belong to the wrong network, because CODESYS writes
    # no comment element for a network without one and the preceding
    # network's comment then attaches to the next component it sees. In the
    # sequential case the parsed comment is all there is.
    for number, comment, note, networks in entries:
        lines.append(network_header(number, comment))
        if note is not None:
            lines.append("(* " + note + " *)")
        for network in networks:
            lines.extend(render_network(network))
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()

    return [line.rstrip() for line in lines]
