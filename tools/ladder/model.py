# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Data model for a Ladder Diagram network.

Two layers live here:

* ``Node`` - a single graphical element exactly as it appears in the PLCopen
  body, still wired by ``localId``. This is a faithful, dumb transcription of
  the XML.
* The ``Expr`` classes - the same logic rearranged into the series/parallel
  tree that a renderer can actually draw. Coordinates are deliberately dropped
  at this point: layout is derived from topology so that nudging a block in the
  CODESYS editor does not churn the diff.
"""

# Element kinds we understand. Anything else is carried through as an opaque
# element so unknown logic is visibly wrong rather than silently missing.
LEFT_RAIL = "leftPowerRail"
RIGHT_RAIL = "rightPowerRail"
CONTACT = "contact"
COIL = "coil"
BLOCK = "block"
IN_VARIABLE = "inVariable"
OUT_VARIABLE = "outVariable"
JUMP = "jump"
RETURN = "return"

RAILS = (LEFT_RAIL, RIGHT_RAIL)


class Connection(object):
    """One wire arriving at an element.

    ``source_pin`` is the formalParameter on the *upstream* element's output
    (CODESYS writes ``formalParameter="Q"`` on the connection itself), while
    ``target_pin`` is the input pin on *this* element. Only blocks have named
    pins; for contacts and coils both are None.
    """

    def __init__(self, ref_id, source_pin=None, target_pin=None):
        self.ref_id = ref_id
        self.source_pin = source_pin
        self.target_pin = target_pin

    def __repr__(self):
        return "Connection(%s, source_pin=%r, target_pin=%r)" % (self.ref_id, self.source_pin, self.target_pin)


class Node(object):
    """One graphical element from an LD body, still wired by localId."""

    def __init__(
        self,
        local_id,
        kind,
        label=None,
        negated=False,
        edge=None,
        storage=None,
        inputs=None,
        type_name=None,
        instance_name=None,
        outputs=None,
    ):
        self.local_id = local_id
        self.kind = kind
        self.label = label
        self.negated = negated
        self.edge = edge  # "rising" | "falling" | None
        self.storage = storage  # "set" | "reset" | None
        self.inputs = inputs if inputs is not None else []
        self.type_name = type_name  # blocks only
        self.instance_name = instance_name  # blocks only, absent for operators
        self.outputs = outputs if outputs is not None else []  # blocks only: (pin, assigned_var)

    def __repr__(self):
        return "Node(%s, %s, %r, inputs=%r)" % (self.local_id, self.kind, self.label, self.inputs)


class Variable(object):
    """One entry from the POU interface, for rendering the declaration block."""

    def __init__(self, name, type_name, initial_value=None, scope="VAR"):
        self.name = name
        self.type_name = type_name
        self.initial_value = initial_value
        self.scope = scope


class Pou(object):
    def __init__(self, name, pou_type, variables=None, rungs=None):
        self.name = name
        self.pou_type = pou_type
        self.variables = variables if variables is not None else []
        self.rungs = rungs if rungs is not None else []


# --- expression tree -------------------------------------------------------


class Empty(object):
    """A wire with nothing on it - an unconditional rung, or a bare rail."""

    def __eq__(self, other):
        return isinstance(other, Empty)

    def __repr__(self):
        return "Empty()"


class Element(object):
    """A drawable leaf: contact, coil, block call, jump.

    For blocks, ``input_pins`` and ``output_pins`` are lists of
    ``(pin_name, label)``. A label of None marks the pin carrying power flow -
    the one wired into the rung rather than fed from a literal or a side
    branch. That pin is always sorted first so the wire runs straight through.
    """

    def __init__(
        self,
        kind,
        label=None,
        negated=False,
        edge=None,
        storage=None,
        type_name=None,
        instance_name=None,
        input_pins=None,
        output_pins=None,
        active_output=None,
    ):
        self.kind = kind
        self.label = label
        self.negated = negated
        self.edge = edge
        self.storage = storage
        self.type_name = type_name
        self.instance_name = instance_name
        self.input_pins = input_pins if input_pins is not None else []
        self.output_pins = output_pins if output_pins is not None else []
        self.active_output = active_output

    @property
    def title(self):
        """Caption drawn above a block: 'TON_0 : TON', or just 'GT'."""
        if self.instance_name:
            return self.instance_name + " : " + (self.type_name or "?")
        return self.type_name or self.label or "?"

    def __repr__(self):
        return "Element(%s, %r)" % (self.kind, self.label)


class Series(object):
    """Elements wired left to right - logical AND."""

    def __init__(self, items):
        self.items = items

    def __repr__(self):
        return "Series(%r)" % (self.items,)


class Parallel(object):
    """Branches wired top to bottom - logical OR."""

    def __init__(self, branches):
        self.branches = branches

    def __repr__(self):
        return "Parallel(%r)" % (self.branches,)


def series(items):
    """Build a Series, flattening nested ones and dropping Empty legs."""
    flat = []
    for item in items:
        if isinstance(item, Empty):
            continue
        if isinstance(item, Series):
            flat.extend(item.items)
        else:
            flat.append(item)
    if not flat:
        return Empty()
    if len(flat) == 1:
        return flat[0]
    return Series(flat)


def parallel(branches):
    """Build a Parallel, collapsing the single-branch case."""
    if not branches:
        return Empty()
    if len(branches) == 1:
        return branches[0]
    return Parallel(branches)
