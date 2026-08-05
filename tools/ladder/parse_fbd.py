# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Parse Function Block Diagram bodies out of PLCopen XML.

FBD has no power rail, so networks are found the same way rungs are - by
looking for sinks nothing else consumes - but the result is a tree of calls
rather than a series/parallel chain.
"""

from model import BLOCK, Assign, Call, Node, Pou, Signal
from plcopen import (
    block_connections,
    block_outputs,
    child_text,
    comment_text,
    direct_connections,
    find_child,
    iter_bodies,
    parse_interface,
    tag,
)

COMMENT = "comment"
IN_VARIABLE = "inVariable"
OUT_VARIABLE = "outVariable"

# vendorElement carries CODESYS editor state (network titles, implementation
# attributes) and holds no logic, so it is skipped entirely.
FBD_KINDS = (BLOCK, IN_VARIABLE, OUT_VARIABLE, COMMENT, "jump", "return", "label", "continuation", "connector")

# Elements that can terminate a network.
SINK_KINDS = (BLOCK, OUT_VARIABLE)


def parse_fbd_body(body_elem):
    """Return an ordered list of Nodes from an <FBD> body element."""
    nodes = []
    for child in body_elem:
        kind = tag(child)
        if kind not in FBD_KINDS:
            continue
        local_id = child.get("localId")
        if local_id is None:
            continue

        if kind == COMMENT:
            nodes.append(Node(local_id=local_id, kind=COMMENT, label=comment_text(child)))
            continue

        is_block = kind == BLOCK
        if is_block:
            label = child.get("instanceName") or child.get("typeName")
        else:
            label = child_text(child, "expression")

        nodes.append(
            Node(
                local_id=local_id,
                kind=kind,
                label=label,
                inputs=block_connections(child) if is_block else direct_connections(child),
                type_name=child.get("typeName") if is_block else None,
                instance_name=child.get("instanceName") if is_block else None,
                outputs=block_outputs(child) if is_block else None,
            )
        )
    return nodes


def _build(node, by_id, visiting, via_pin=None):
    if node.local_id in visiting:
        return Signal("<cycle at %s>" % node.local_id)
    visiting = visiting | set([node.local_id])

    if node.kind == BLOCK:
        inputs = []
        for connection in node.inputs:
            upstream = by_id.get(connection.ref_id)
            source = None
            if upstream is not None:
                source = _build(upstream, by_id, visiting, connection.source_pin)
            inputs.append((connection.target_pin, source))

        active = via_pin
        if active is None and node.outputs:
            active = node.outputs[0][0]

        return Call(
            type_name=node.type_name,
            instance_name=node.instance_name,
            inputs=inputs,
            outputs=list(node.outputs),
            active_output=active,
            # via_pin is set by the consumer; a network sink has none.
            output_wired=via_pin is not None,
        )

    if node.kind == OUT_VARIABLE:
        source = None
        for connection in node.inputs:
            upstream = by_id.get(connection.ref_id)
            if upstream is not None:
                source = _build(upstream, by_id, visiting, connection.source_pin)
                break
        return Assign(node.label or "?", source)

    return Signal(node.label or "")


def build_networks(nodes):
    """Split a flat node list into (comment, tree) per network.

    Comments are matched to networks by document order: a comment applies to
    the sink that follows it, which is how CODESYS lays out the export.
    """
    by_id = {}
    for node in nodes:
        if node.kind != COMMENT:
            by_id[node.local_id] = node

    consumed = set()
    for node in nodes:
        for connection in node.inputs:
            consumed.add(connection.ref_id)

    networks = []
    comment = ""
    for node in nodes:
        if node.kind == COMMENT:
            comment = node.label or ""
            continue
        if node.local_id in consumed or node.kind not in SINK_KINDS:
            continue
        networks.append((comment, _build(node, by_id, set())))
        comment = ""
    return networks


def parse_pous(source):
    """Parse every FBD POU in a PLCopen file. Other languages are skipped."""
    pous = []
    for pou_elem, language, body in iter_bodies(source):
        if language != "FBD":
            continue
        pous.append(
            Pou(
                name=pou_elem.get("name") or "<unnamed>",
                pou_type=pou_elem.get("pouType") or "program",
                language="FBD",
                variables=parse_interface(find_child(pou_elem, "interface")),
                networks=build_networks(parse_fbd_body(body)),
            )
        )
    return pous
