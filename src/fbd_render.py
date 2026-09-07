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
from ld_render import network_headers, render_declaration
from model import Assign, Call, Jump, Label, OutputRef, Signal


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


def _store_head(node):
    """The arrow head on a store: its set/reset marker, or its negation.

    A set or reset holds the target until the other one fires. Drawing it as
    a plain arrow says the store follows its input, which is the opposite.
    """
    if node.storage == "set":
        return "(S)> "
    if node.storage == "reset":
        return "(R)> "
    # The negation circle CODESYS draws on the pin, as an "o" on the wire.
    return "o> " if node.negated else "> "


def _render_assign(node):
    chars = charset.active()
    source = _render(node.source) if node.source is not None else Block([""], 0)
    lines = source.padded(source.width)
    head = _store_head(node)
    tail = chars["H"] * 3 + head + (node.label or "?")
    out = []
    for index, line in enumerate(lines):
        out.append(line + tail if index == source.connect_row else line)
    return Block(out, source.connect_row)


def _pin_arrow(box, pin):
    """The arrow for an assignment written straight onto an output pin.

    "=o>" is "=>" with the negation bubble: the pin stores its inverse. "=S>"
    and "=R>" are the set and reset a pin can carry, exactly as a coil does.
    """
    storage = box.stored_outputs.get(pin)
    if storage == "set":
        return " =S> "
    if storage == "reset":
        return " =R> "
    return " =o> " if pin in box.negated_outputs else " => "


def _is_wired(source):
    """False for a pin CODESYS exported with no source, or an empty expression.

    Those must not be drawn with a wire running off to the left, because there
    is nothing out there feeding them.
    """
    if source is None:
        return False
    return not (isinstance(source, Signal) and not source.label)


def _render_call(call, read_pin=None):
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
            text += _pin_arrow(call, pin) + assigned
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

    # An output pin only breaks the box wall with a tee if a consumer is
    # actually there to receive it - and a box read through two pins breaks
    # it twice.
    pins = [pin for pin, _assigned in call.outputs]
    live_output_rows = set()
    for pin in call.wired_outputs:
        if pin in pins:
            live_output_rows.add(output_rows[pins.index(pin)])

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
            right_edge = chars["PIN_R"] if row in live_output_rows else chars["V"]
            gap = inner - len(left_pin) - len(right_pin)
            box = left_edge + left_pin + " " * gap + right_pin + right_edge
        else:
            box = " " * (inner + 2)
        lines.append(left[row] + box)

    # The wire leaves on whichever output pin this consumer asked for.
    pin_rows = {}
    for index, pin in enumerate(pins):
        pin_rows[pin] = output_rows[index]

    wanted = read_pin if read_pin is not None else call.active_output
    connect_row = box_first
    if wanted in pin_rows:
        connect_row = pin_rows[wanted]
    elif output_rows:
        connect_row = output_rows[0]

    return Block(lines, connect_row, pin_rows)


def _render(node):
    if isinstance(node, OutputRef):
        # One box, entered on the pin this wire reads.
        return _render_call(node.call, node.pin)
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
    head = "o " if node.negated and not node.storage else _store_head(node)
    return chars["H"] * 2 + head + (node.label or "?")


def _fanout_groups(source, outputs):
    """[(rows, outputs)] - one group per output pin that is read.

    Outputs reading the same pin share one wire and are branched off it, so
    they stack on consecutive rows under that pin. Outputs reading different
    pins do not share anything: each leaves the box on its own pin's row, and
    joining them into one junction column would draw two signals as one.
    """
    order = []
    at_pin = {}
    for output in outputs:
        pin = output.source.pin if isinstance(output.source, OutputRef) else None
        if pin not in at_pin:
            at_pin[pin] = []
            order.append(pin)
        at_pin[pin].append(output)
    order.sort(key=lambda pin: source.pin_rows.get(pin, source.connect_row))

    groups = []
    taken = set()
    for pin in order:
        rows = []
        row = source.pin_rows.get(pin, source.connect_row)
        for output in at_pin[pin]:
            while row in taken:
                row += 1
            taken.add(row)
            rows.append(row)
            row += 1
        groups.append((rows, at_pin[pin]))
    return groups


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
    groups = _fanout_groups(source, outputs)

    tails = {}
    joints = {}
    verticals = set()
    for rows, group in groups:
        for row, output in zip(rows, group):
            tails[row] = output
        if len(rows) == 1:
            joints[rows[0]] = chars["H"]
            continue
        # One wire, branched: the junction column belongs to this pin alone.
        joints[rows[0]] = chars["T_DOWN"]
        joints[rows[-1]] = chars["BL"]
        for row in rows[1:-1]:
            joints[row] = chars["T_RIGHT"]
        for row in range(rows[0] + 1, rows[-1]):
            verticals.add(row)

    # Only the row a wire actually leaves the box on is extended to the
    # junction; the rows below it are carried by the junction column.
    lines = source.padded(width, wire_rows=set(rows[0] for rows, _group in groups))

    last = max(tails)
    while len(lines) <= last:
        lines.append(" " * width)

    out = []
    for row, line in enumerate(lines):
        joint = joints.get(row, chars["V"] if row in verticals else " ")
        tail = _assign_tail(tails[row]) if row in tails else ""
        out.append(line + joint + tail)

    return Block(out, min(tails))


def _shared_source(outputs):
    """The single source every output hangs off, or None.

    Identity, not equality: the parser memoises shared nodes, so two outputs
    fed by one block hold the very same object - through an OutputRef each
    when they read different pins of it.
    """
    if len(outputs) < 2:
        return None
    if not all(isinstance(output, Assign) for output in outputs):
        return None
    sources = [output.source for output in outputs]
    if sources[0] is None:
        return None
    boxes = [source.call if isinstance(source, OutputRef) else source for source in sources]
    return sources[0] if all(box is boxes[0] for box in boxes) else None


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
    """Render a whole FBD POU: declaration, then one box tree per network."""
    lines = render_declaration(pou)
    lines.append("")

    if not pou.networks:
        lines.append("(* no networks *)")

    for index, network in enumerate(pou.networks):
        lines.extend(network_headers(index + 1, network))
        lines.extend(render_network(network))
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()

    return [line.rstrip() for line in lines]
