# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Parse Function Block Diagram bodies out of PLCopen XML.

FBD has no power rail, so networks are found the same way rungs are - by
looking for sinks nothing else consumes - but the result is a tree of calls
rather than a series/parallel chain.
"""

from model import BLOCK, Assign, Call, Jump, Label, Network, Node, OutputRef, Pou, Signal
from plcopen import (
    block_connections,
    block_outputs,
    block_st_code,
    child_text,
    declaration_text,
    comment_text,
    direct_connections,
    find_child,
    is_true,
    iter_bodies,
    negated_output_pins,
    parse_interface,
    tag,
)

COMMENT = "comment"
IN_VARIABLE = "inVariable"
OUT_VARIABLE = "outVariable"
JUMP = "jump"
LABEL = "label"
RETURN = "return"
CONNECTOR = "connector"
CONTINUATION = "continuation"

# vendorElement carries CODESYS editor state (network titles, implementation
# attributes) and holds no logic, so it is skipped entirely.
FBD_KINDS = (BLOCK, IN_VARIABLE, OUT_VARIABLE, COMMENT, JUMP, RETURN, LABEL, CONTINUATION, CONNECTOR)

# Elements that can terminate a network. A jump or return ends one just as
# surely as an assignment does - leaving them out drops the entire guard
# network they belong to, silently. A connector too: its continuations refer
# to it by name, never by localId, so nothing ever "consumes" it and without
# a sink entry its whole upstream network would vanish.
SINK_KINDS = (BLOCK, OUT_VARIABLE, JUMP, RETURN, LABEL, CONNECTOR)


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
        elif kind in (JUMP, LABEL):
            # Both carry their target in a "label" attribute, not a child.
            label = child.get("label")
        elif kind in (CONNECTOR, CONTINUATION):
            # The wire's name is a "name" attribute; there is no expression.
            label = child.get("name")
        else:
            label = child_text(child, "expression")

        node = Node(
            local_id=local_id,
            kind=kind,
            label=label,
            negated=is_true(child, "negated"),
            inputs=block_connections(child) if is_block else direct_connections(child),
            type_name=child.get("typeName") if is_block else None,
            instance_name=child.get("instanceName") if is_block else None,
            outputs=block_outputs(child) if is_block else None,
            st_code=block_st_code(child) if is_block else None,
            negated_outputs=negated_output_pins(child) if is_block else None,
        )
        nodes.append(node)
    return nodes


def _negate(source):
    """Wrap a pin's source in the negation its pin bubble demands.

    A Signal simply flips; anything else becomes an explicit NOT operator so
    the inversion is visible in both the ST and the diagram.
    """
    if isinstance(source, Signal):
        return Signal(source.label, negated=not source.negated)
    return Call(
        type_name="NOT",
        inputs=[("In", source)],
        outputs=[("Out", None)],
        active_output="Out",
        wired_outputs=["Out"],
    )


def _build(node, by_id, visiting, via_pin=None, memo=None):
    """Build the tree feeding a node, as read through ``via_pin``.

    Memoised on the localId alone, so every reader of an element gets the very
    same object - which is what lets the renderers draw one box with a branch
    instead of two identical boxes, and lets the ST emit one call. Keying on
    the pin as well made a block read through two of its pins into two blocks.
    The pin is instead recorded on the wire, as an OutputRef, and remembered
    on the call so the renderer knows which pins to break the box wall for.
    """
    if memo is None:
        memo = {}
    if node.local_id not in memo:
        memo[node.local_id] = _build_node(node, by_id, visiting, memo)
    built = memo[node.local_id]
    if via_pin is not None and isinstance(built, Call):
        built.wired_outputs.add(via_pin)
        return OutputRef(built, via_pin)
    return built


def _build_node(node, by_id, visiting, memo):
    if node.local_id in visiting:
        return Signal("<cycle at %s>" % node.local_id)
    visiting = visiting | set([node.local_id])

    if node.kind == BLOCK:
        inputs = []
        for connection in node.inputs:
            upstream = by_id.get(connection.ref_id)
            source = None
            if upstream is not None:
                source = _build(upstream, by_id, visiting, connection.source_pin, memo)
            if connection.negated and source is not None:
                # The bubble on the pin itself, not on what feeds it.
                source = _negate(source)
            inputs.append((connection.target_pin, source))

        return Call(
            type_name=node.type_name,
            instance_name=node.instance_name,
            inputs=inputs,
            outputs=list(node.outputs),
            st_code=list(node.st_code),
            negated_outputs=set(node.negated_outputs),
        )

    if node.kind in (OUT_VARIABLE, JUMP, RETURN, CONNECTOR):
        source = None
        for connection in node.inputs:
            upstream = by_id.get(connection.ref_id)
            if upstream is not None:
                source = _build(upstream, by_id, visiting, connection.source_pin, memo)
                break
        if node.kind == OUT_VARIABLE:
            return Assign(node.label or "?", source, negated=node.negated)
        if node.kind == CONNECTOR:
            # A connector names the wire feeding it, so it renders as an
            # assignment to that name and the matching continuation reads the
            # name back. Not real ST - but the logic stays on the page.
            return Assign(node.label or "?", source, negated=node.negated)
        return Jump(node.label or ("RETURN" if node.kind == RETURN else "?"), source)

    if node.kind == LABEL:
        return Label(node.label or "?")

    # A continuation lands here: its label is the wire's name, so it reads
    # like any other signal.
    return Signal(node.label or "", negated=node.negated)


def _component_finder(logic):
    """Union-find over the wires, ignoring direction.

    Two outputs fed from one block belong to the same network, so grouping has
    to follow wires backwards as well as forwards.
    """
    parent = {}
    for node in logic:
        parent[node.local_id] = node.local_id

    def find(item):
        root = item
        while parent[root] != root:
            root = parent[root]
        while parent[item] != root:
            parent[item], item = root, parent[item]
        return root

    for node in logic:
        for connection in node.inputs:
            if connection.ref_id not in parent:
                continue
            left, right = find(node.local_id), find(connection.ref_id)
            if left != right:
                parent[left] = right
    return find


def build_networks(nodes):
    """Group a flat node list into Networks.

    One network per connected component, not one per sink. A block driving two
    outVariables is a single network in the editor; splitting it produced two
    networks with the whole shared expression written out twice, and threw the
    numbering out against what a reviewer sees in CODESYS.
    """
    logic = [node for node in nodes if node.kind != COMMENT]

    by_id = {}
    for node in logic:
        by_id[node.local_id] = node

    find = _component_finder(logic)

    consumed = set()
    for node in logic:
        for connection in node.inputs:
            consumed.add(connection.ref_id)

    # A comment applies to the component whose first element follows it.
    comments = {}
    pending = ""
    for node in nodes:
        if node.kind == COMMENT:
            pending = node.label or ""
            continue
        root = find(node.local_id)
        if root not in comments:
            comments[root] = pending
            pending = ""

    # Shared upstream nodes must come back as the same object, so the
    # renderers can tell a fan-out from two coincidentally equal expressions.
    memo = {}
    networks = []
    by_root = {}
    for node in logic:
        if node.local_id in consumed or node.kind not in SINK_KINDS:
            continue
        tree = _build(node, by_id, set(), None, memo)
        root = find(node.local_id)
        if root in by_root:
            by_root[root].outputs.append(tree)
        else:
            network = Network(comment=comments.get(root, ""), outputs=[tree])
            by_root[root] = network
            networks.append(network)
    return networks


LANGUAGE = "FBD"


def pou_from_body(pou_elem, body_elem):
    """Build a Pou from an already-located <FBD> body.

    Split out from parse_pous so a caller handling several languages can make
    a single pass over the document rather than one per language.
    """
    return Pou(
        name=pou_elem.get("name") or "<unnamed>",
        pou_type=pou_elem.get("pouType") or "program",
        language=LANGUAGE,
        variables=parse_interface(find_child(pou_elem, "interface")),
        declaration_text=declaration_text(pou_elem),
        networks=build_networks(parse_fbd_body(body_elem)),
    )


def parse_pous(source):
    """Parse every FBD POU in a PLCopen file. Other languages are skipped."""
    pous = []
    for pou_elem, language, body in iter_bodies(source):
        if language == LANGUAGE:
            pous.append(pou_from_body(pou_elem, body))
    return pous
