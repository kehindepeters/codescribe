# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Parse Ladder Diagram bodies out of PLCopen XML.

The XML-level helpers live in plcopen.py; this module owns the LD-specific
part: turning a flat list of wired elements into one series/parallel
expression tree per rung.
"""

from model import (
    BLOCK,
    IN_VARIABLE,
    JUMP,
    LABEL,
    LEFT_RAIL,
    RAILS,
    RETURN,
    RIGHT_RAIL,
    CONTACT,
    COIL,
    Element,
    Empty,
    Node,
    Parallel,
    Pou,
    Series,
    is_simple_term,
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
    negated_output_pins,
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
    JUMP,
    RETURN,
    LABEL,
)


def _node_label(elem, kind):
    if kind == BLOCK:
        # typeName and instanceName are attributes in CODESYS's output, not
        # the child elements a literal schema reading would suggest.
        return elem.get("instanceName") or elem.get("typeName")
    if kind in (JUMP, LABEL):
        # The target is a "label" attribute, not a child element - same as in
        # FBD bodies. Reading child elements here loses the target entirely.
        return elem.get("label")
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
                negated_outputs=negated_output_pins(child) if is_block else None,
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
            text = (base + "." + expr.active_output) if expr.active_output else base
            # The negation bubble on the consumed output inverts what leaves
            # the box - on this flattened path just like on the power flow.
            if expr.active_output in expr.negated_outputs:
                return "NOT " + text
            return text
        label = expr.label or ""
        if expr.edge == "rising":
            return "R(" + label + ")"
        if expr.edge == "falling":
            return "F(" + label + ")"
        if expr.negated:
            return "NOT " + _bracket(label)
        return label
    return "?"


def _bracket(text):
    """Parenthesise a compound term before negating or nesting it.

    NOT binds above OR, AND and even comparison in IEC 61131-3, so both
    "NOT xA OR xB" and the spaceless "NOT iCount>5" regroup the logic their
    bracketed forms state.
    """
    if is_simple_term(text):
        return text
    return "(" + text + ")"


def _build_block(node, by_id, visiting, via_pin):
    """Build a block call, separating power flow from parameter inputs.

    Exactly one input carries the rung's power flow. Pins fed by a literal or
    an inVariable are parameters, not power, so the first genuinely wired pin
    wins and the rest become captions inside the box.
    """
    power_expr = Empty()
    power_pin = None
    power_negated = False
    side_pins = []

    for connection in node.inputs:
        upstream = by_id.get(connection.ref_id)
        if upstream is None:
            side_pins.append((connection.target_pin, "?"))
            continue
        sub_expr = _build_expr(upstream, by_id, visiting, connection.source_pin)
        if upstream.kind == IN_VARIABLE:
            # Flattened through expr_to_text, not taken from the raw label:
            # an in-place negated inVariable must keep its NOT, or the pin
            # silently inverts.
            side_pins.append((connection.target_pin, _pin_text(sub_expr, connection)))
        elif power_pin is None:
            power_pin = connection.target_pin
            power_expr = sub_expr
            # The pin's own negation bubble; it inverts the power flow at the
            # box wall, after everything the rung has accumulated.
            power_negated = connection.negated
        else:
            side_pins.append((connection.target_pin, _pin_text(sub_expr, connection)))

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
        # via_pin is set by whatever consumed this block; a block terminating
        # the rung has none.
        output_wired=via_pin is not None,
        power_negated=power_negated,
        negated_outputs=set(node.negated_outputs),
    )
    return series([power_expr, element])


def _pin_text(sub_expr, connection):
    """A side pin's caption, honouring the pin's own negation bubble."""
    text = expr_to_text(sub_expr)
    if connection.negated:
        return "NOT " + _bracket(text) if text else "NOT ?"
    return text


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


LANGUAGE = "LD"


def pou_from_body(pou_elem, body_elem):
    """Build a Pou from an already-located <LD> body.

    Split out from parse_pous so a caller handling several languages can make
    a single pass over the document instead of re-reading and re-parsing it
    once per language.
    """
    return Pou(
        name=pou_elem.get("name") or "<unnamed>",
        pou_type=pou_elem.get("pouType") or "program",
        language=LANGUAGE,
        variables=parse_interface(find_child(pou_elem, "interface")),
        rungs=build_rungs(parse_ld_body(body_elem)),
    )


def parse_pous(source):
    """Parse every LD POU in a PLCopen file. Other languages are skipped.

    ``source`` is a path or a file object, as accepted by ElementTree.
    """
    pous = []
    for pou_elem, language, body in iter_bodies(source):
        if language == LANGUAGE:
            pous.append(pou_from_body(pou_elem, body))
    return pous
