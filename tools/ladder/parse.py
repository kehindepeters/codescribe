# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Parse Ladder Diagram bodies out of PLCopen XML.

The XML-level helpers live in plcopen.py; this module owns the LD-specific
part: turning a flat list of wired elements into one series/parallel
expression tree per rung.
"""

from model import (
    BLOCK,
    IN_VARIABLE,
    LEFT_RAIL,
    RAILS,
    RIGHT_RAIL,
    CONTACT,
    COIL,
    Element,
    Empty,
    Node,
    Parallel,
    Pou,
    Series,
    parallel,
    series,
)
from plcopen import (
    attr,
    block_connections,
    block_outputs,
    child_text,
    direct_connections,
    find_child,
    is_true,
    iter_bodies,
    parse_interface,
    tag,
)

# Elements that carry logic. Rails are structural: they anchor a rung but draw
# nothing themselves.
KNOWN_KINDS = (
    LEFT_RAIL,
    RIGHT_RAIL,
    CONTACT,
    COIL,
    BLOCK,
    "inVariable",
    "outVariable",
    "jump",
    "return",
)


def _node_label(elem, kind):
    if kind == BLOCK:
        # typeName and instanceName are attributes in CODESYS's output, not
        # the child elements a literal schema reading would suggest.
        return elem.get("instanceName") or elem.get("typeName")
    return child_text(elem, "variable") or child_text(elem, "expression")


def parse_ld_body(body_elem):
    """Return an ordered list of Nodes from an <LD> body element."""
    nodes = []
    for child in body_elem:
        kind = tag(child)
        if kind not in KNOWN_KINDS:
            continue
        local_id = child.get("localId")
        if local_id is None:
            continue
        is_block = kind == BLOCK
        nodes.append(
            Node(
                local_id=local_id,
                kind=kind,
                label=_node_label(child, kind),
                negated=is_true(child, "negated"),
                edge=attr(child, "edge"),
                storage=attr(child, "storage"),
                inputs=block_connections(child) if is_block else direct_connections(child),
                type_name=child.get("typeName") if is_block else None,
                instance_name=child.get("instanceName") if is_block else None,
                outputs=block_outputs(child) if is_block else None,
            )
        )
    return nodes


# --- graph to expression tree ----------------------------------------------


def _to_element(node):
    return Element(
        kind=node.kind,
        label=node.label,
        negated=node.negated,
        edge=node.edge,
        storage=node.storage,
    )


def expr_to_text(expr):
    """Flatten an expression to one line of ST-ish text.

    Used for a block's side inputs: a RESET pin fed by its own contact chain
    cannot be drawn as a second horizontal wire without a genuine 2-D layout,
    so it is written into the pin as "RESET := PowerOff" instead.
    """
    if isinstance(expr, Empty):
        return ""
    if isinstance(expr, Series):
        parts = [part for part in (expr_to_text(item) for item in expr.items) if part]
        return " AND ".join(parts)
    if isinstance(expr, Parallel):
        parts = [part for part in (expr_to_text(branch) for branch in expr.branches) if part]
        return "(" + " OR ".join(parts) + ")"
    if isinstance(expr, Element):
        if expr.kind == BLOCK:
            base = expr.instance_name or expr.type_name or "?"
            return base + "." + expr.active_output if expr.active_output else base
        label = expr.label or ""
        if expr.edge == "rising":
            return "R(" + label + ")"
        if expr.edge == "falling":
            return "F(" + label + ")"
        if expr.negated:
            return "NOT " + label
        return label
    return "?"


def _build_block(node, by_id, visiting, via_pin):
    """Build a block call, separating power flow from parameter inputs.

    Exactly one input carries the rung's power flow. Pins fed by a literal or
    an inVariable are parameters, not power, so the first genuinely wired pin
    wins and the rest become captions inside the box.
    """
    power_expr = Empty()
    power_pin = None
    side_pins = []

    for connection in node.inputs:
        upstream = by_id.get(connection.ref_id)
        if upstream is None:
            side_pins.append((connection.target_pin, "?"))
            continue
        sub_expr = _build_expr(upstream, by_id, visiting, connection.source_pin)
        if upstream.kind == IN_VARIABLE:
            side_pins.append((connection.target_pin, upstream.label or ""))
        elif power_pin is None:
            power_pin = connection.target_pin
            power_expr = sub_expr
        else:
            side_pins.append((connection.target_pin, expr_to_text(sub_expr)))

    input_pins = []
    if power_pin is not None:
        # None marks the power pin, and it sorts first so the wire runs
        # straight through the box instead of jogging to another row.
        input_pins.append((power_pin, None))
    input_pins.extend(side_pins)

    active = via_pin
    if active is None and node.outputs:
        active = node.outputs[0][0]
    output_pins = [out for out in node.outputs if out[0] == active]
    output_pins += [out for out in node.outputs if out[0] != active]

    element = Element(
        kind=BLOCK,
        label=node.label,
        type_name=node.type_name,
        instance_name=node.instance_name,
        input_pins=input_pins,
        output_pins=output_pins,
        active_output=active,
    )
    return series([power_expr, element])


def _build_expr(node, by_id, visiting, via_pin=None):
    """Walk backwards from a node to the power rail, building series/parallel.

    A node's expression is everything feeding it (OR'd together if there is
    more than one input) followed by the node itself.
    """
    if node.local_id in visiting:
        # Feedback loops are not legal in a rung, but a malformed export should
        # produce a visible marker rather than blow the stack.
        return Element(kind="cycle", label="<cycle at %s>" % node.local_id)

    visiting = visiting | set([node.local_id])

    if node.kind == BLOCK:
        return _build_block(node, by_id, visiting, via_pin)

    branches = []
    for connection in node.inputs:
        upstream = by_id.get(connection.ref_id)
        if upstream is None:
            continue
        branches.append(_build_expr(upstream, by_id, visiting, connection.source_pin))

    incoming = parallel(branches) if branches else Empty()

    if node.kind in RAILS:
        # Rails are anchors, not symbols - they contribute nothing to draw.
        return incoming

    return series([incoming, _to_element(node)])


def build_rungs(nodes):
    """Split a flat node list into one expression tree per rung.

    A rung is identified by its terminal: an element nothing else consumes.
    That is the right power rail where one exists, and the coil itself where
    the export omits it - CODESYS exports the right rail unconnected.
    """
    by_id = {}
    for node in nodes:
        by_id[node.local_id] = node

    consumed = set()
    for node in nodes:
        for connection in node.inputs:
            consumed.add(connection.ref_id)

    rungs = []
    for node in nodes:
        if node.local_id in consumed:
            continue
        if node.kind == LEFT_RAIL:
            # An unconnected left rail is an empty rung, not a terminal.
            continue
        expr = _build_expr(node, by_id, set())
        if isinstance(expr, Empty):
            continue
        rungs.append(expr)
    return rungs


def parse_pous(source):
    """Parse every LD POU in a PLCopen file. Other languages are skipped.

    ``source`` is a path or a file object, as accepted by ElementTree.
    """
    pous = []
    for pou_elem, language, body in iter_bodies(source):
        if language != "LD":
            continue
        pous.append(
            Pou(
                name=pou_elem.get("name") or "<unnamed>",
                pou_type=pou_elem.get("pouType") or "program",
                variables=parse_interface(find_child(pou_elem, "interface")),
                rungs=build_rungs(parse_ld_body(body)),
            )
        )
    return pous
