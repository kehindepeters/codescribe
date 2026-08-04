# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Parse Ladder Diagram bodies out of PLCopen XML.

Namespaces are stripped rather than matched, because the exact namespace URI
varies between PLCopen schema revisions (tc6_0200 vs tc6_0201) and CODESYS
adds proprietary extensions of its own. Matching on local tag names keeps this
working across dialects.
"""

import xml.etree.ElementTree as ET

from model import (
    BLOCK,
    COIL,
    CONTACT,
    IN_VARIABLE,
    LEFT_RAIL,
    RAILS,
    RIGHT_RAIL,
    Connection,
    Element,
    Empty,
    Node,
    Parallel,
    Pou,
    Series,
    Variable,
    parallel,
    series,
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

TRUTHY = ("true", "1")


def _tag(elem):
    """Local tag name, with any namespace stripped."""
    return elem.tag.split("}")[-1]


def _find_child(elem, name):
    for child in elem:
        if _tag(child) == name:
            return child
    return None


def _child_text(elem, name):
    child = _find_child(elem, name)
    if child is None or child.text is None:
        return None
    return child.text.strip()


def _is_true(elem, attr):
    return (elem.get(attr) or "").lower() in TRUTHY


def _attr(elem, name):
    """An attribute, treating CODESYS's literal "none" as absent."""
    value = elem.get(name)
    if value in (None, "", "none"):
        return None
    return value


def _direct_connections(elem):
    """Wires arriving at this element's own connectionPointIn children.

    Several <connection> under a single connectionPointIn is how PLCopen
    spells a parallel branch (a wired OR), so order and multiplicity matter.
    This deliberately does not recurse: a block's pins hang off
    <inputVariables> and are collected separately, with their pin names.
    """
    connections = []
    for point in elem:
        if _tag(point) != "connectionPointIn":
            continue
        for child in point:
            if _tag(child) != "connection":
                continue
            ref = child.get("refLocalId")
            if ref is not None:
                connections.append(Connection(ref, source_pin=_attr(child, "formalParameter")))
    return connections


def _block_connections(block_elem):
    """Wires arriving at a block, tagged with the pin they land on."""
    connections = []
    for group_name in ("inputVariables", "inOutVariables"):
        group = _find_child(block_elem, group_name)
        if group is None:
            continue
        for var in group:
            if _tag(var) != "variable":
                continue
            pin = var.get("formalParameter")
            for connection in _direct_connections(var):
                connection.target_pin = pin
                connections.append(connection)
    return connections


def _block_outputs(block_elem):
    """(pin, assigned variable) for each block output.

    CODESYS writes an assignment straight onto the output pin as
    <connectionPointOut><expression>uiCurrSupplyVolt</expression>.
    """
    outputs = []
    group = _find_child(block_elem, "outputVariables")
    if group is None:
        return outputs
    for var in group:
        if _tag(var) != "variable":
            continue
        assigned = None
        point = _find_child(var, "connectionPointOut")
        if point is not None:
            expression = _find_child(point, "expression")
            if expression is not None and expression.text:
                assigned = expression.text.strip()
        outputs.append((var.get("formalParameter"), assigned))
    return outputs


def _node_label(elem, kind):
    if kind == BLOCK:
        # typeName and instanceName are attributes in CODESYS's output, not
        # the child elements a literal schema reading would suggest.
        return elem.get("instanceName") or elem.get("typeName")
    return _child_text(elem, "variable") or _child_text(elem, "expression")


def parse_ld_body(body_elem):
    """Return an ordered list of Nodes from an <LD> body element."""
    nodes = []
    for child in body_elem:
        kind = _tag(child)
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
                negated=_is_true(child, "negated"),
                edge=_attr(child, "edge"),
                storage=_attr(child, "storage"),
                inputs=_block_connections(child) if is_block else _direct_connections(child),
                type_name=child.get("typeName") if is_block else None,
                instance_name=child.get("instanceName") if is_block else None,
                outputs=_block_outputs(child) if is_block else None,
            )
        )
    return nodes


def _type_name(var_elem):
    type_elem = _find_child(var_elem, "type")
    if type_elem is None:
        return "BOOL"
    for child in type_elem:
        name = _tag(child)
        if name == "derived":
            return child.get("name") or "UNKNOWN"
        return name
    return "BOOL"


def _initial_value(var_elem):
    value_elem = _find_child(var_elem, "initialValue")
    if value_elem is None:
        return None
    simple = _find_child(value_elem, "simpleValue")
    if simple is None:
        return None
    return simple.get("value")


SCOPE_TAGS = {
    "localVars": "VAR",
    "inputVars": "VAR_INPUT",
    "outputVars": "VAR_OUTPUT",
    "inOutVars": "VAR_IN_OUT",
    "tempVars": "VAR_TEMP",
    "globalVars": "VAR_GLOBAL",
}


def parse_interface(interface_elem):
    variables = []
    if interface_elem is None:
        return variables
    for group in interface_elem:
        scope = SCOPE_TAGS.get(_tag(group))
        if scope is None:
            continue
        if _is_true(group, "constant"):
            scope += " CONSTANT"
        for var_elem in group:
            if _tag(var_elem) != "variable":
                continue
            variables.append(
                Variable(
                    name=var_elem.get("name") or "",
                    type_name=_type_name(var_elem),
                    initial_value=_initial_value(var_elem),
                    scope=scope,
                )
            )
    return variables


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
    the export omits it.
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


# --- top level -------------------------------------------------------------


def parse_pous(source):
    """Parse every LD POU in a PLCopen file. Non-LD POUs are skipped.

    ``source`` is a path or a file object, as accepted by ElementTree.
    """
    tree = ET.parse(source)
    root = tree.getroot()

    pous = []
    for elem in root.iter():
        if _tag(elem) != "pou":
            continue
        body = _find_child(elem, "body")
        if body is None:
            continue
        ld_body = _find_child(body, "LD")
        if ld_body is None:
            continue
        pous.append(
            Pou(
                name=elem.get("name") or "<unnamed>",
                pou_type=elem.get("pouType") or "program",
                variables=parse_interface(_find_child(elem, "interface")),
                rungs=build_rungs(parse_ld_body(ld_body)),
            )
        )
    return pous
