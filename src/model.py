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

import re

# A bare identifier, member access or literal - something safe to negate or
# nest without brackets. Anything else (operators, calls, spaces) must be
# parenthesised: expressions are free-form ST, often typed without spaces,
# and NOT binds above comparison in IEC 61131-3, so "NOT iCount>5" states
# "(NOT iCount)>5". The % covers direct addresses like %IX0.0.
_SIMPLE_TERM = re.compile(r"^[A-Za-z0-9_.#%]+$")


def is_simple_term(text):
    """True when text can be negated or nested without changing its grouping."""
    return _SIMPLE_TERM.match(text) is not None


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
LABEL = "label"

RAILS = (LEFT_RAIL, RIGHT_RAIL)


class Connection(object):
    """One wire arriving at an element.

    ``source_pin`` is the formalParameter on the *upstream* element's output
    (CODESYS writes ``formalParameter="Q"`` on the connection itself), while
    ``target_pin`` is the input pin on *this* element. Only blocks have named
    pins; for contacts and coils both are None.

    ``negated`` is the bubble CODESYS draws on the *pin itself* (negated="true"
    on the pin's variable element) - separate from a negated inVariable, and
    just as logic-inverting if dropped.
    """

    def __init__(self, ref_id, source_pin=None, target_pin=None, negated=False):
        self.ref_id = ref_id
        self.source_pin = source_pin
        self.target_pin = target_pin
        self.negated = negated

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
        st_code=None,
        negated_outputs=None,
    ):
        self.st_code = st_code if st_code is not None else []  # blocks only: inline ST
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
        # blocks only: output pins whose in-place negation bubble inverts the
        # value leaving them
        self.negated_outputs = negated_outputs if negated_outputs is not None else set()

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
    """A parsed POU. ``rungs`` is populated for LD, ``networks`` for FBD."""

    def __init__(self, name, pou_type, variables=None, rungs=None, networks=None, language=None):
        self.name = name
        self.pou_type = pou_type
        self.language = language
        self.variables = variables if variables is not None else []
        self.rungs = rungs if rungs is not None else []
        self.networks = networks if networks is not None else []


# --- FBD tree --------------------------------------------------------------
#
# FBD has no power rail, so there is no single wire to hang a series/parallel
# tree off. A network is instead a tree of calls: each block pin is fed either
# by a named value or by another block's output.


class Signal(object):
    """A named value entering a network: a variable, a literal, or nothing.

    CODESYS can negate an inVariable in place, which is easy to miss and
    inverts the logic if it is dropped.
    """

    def __init__(self, label, negated=False):
        self.label = label
        self.negated = negated

    @property
    def text(self):
        label = self.label or ""
        if not self.negated:
            return label
        # A compound expression must keep its parentheses or the logic
        # regroups - see is_simple_term for the precedence trap.
        if is_simple_term(label):
            return "NOT " + label
        return "NOT (" + label + ")"

    def __repr__(self):
        return "Signal(%r, negated=%r)" % (self.label, self.negated)


class Jump(object):
    """A conditional jump to a label. Terminates its network."""

    def __init__(self, target, condition=None):
        self.target = target
        self.condition = condition

    def __repr__(self):
        return "Jump(%r)" % (self.target,)


class Label(object):
    """A jump target. Marks a point in the network order, carries no logic."""

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return "Label(%r)" % (self.name,)


class Call(object):
    """An FBD block call - a box with named input and output pins.

    ``inputs`` is [(pin_name, source)] where source is a Call, a Signal or
    None. ``outputs`` is [(pin_name, assigned_variable)]. Pin order is kept
    exactly as exported; unlike LD there is no power pin to hoist.
    """

    def __init__(
        self,
        type_name=None,
        instance_name=None,
        inputs=None,
        outputs=None,
        active_output=None,
        output_wired=False,
        st_code=None,
        negated_outputs=None,
    ):
        self.type_name = type_name
        self.instance_name = instance_name
        self.inputs = inputs if inputs is not None else []
        self.outputs = outputs if outputs is not None else []
        self.active_output = active_output
        # Pins carrying CODESYS's in-place negation bubble: the value leaving
        # them is the inverse of the pin.
        self.negated_outputs = negated_outputs if negated_outputs is not None else set()
        # An EXECUTE box carries inline ST as its whole body. Dropping it loses
        # the logic entirely while still drawing a plausible-looking box.
        self.st_code = st_code if st_code is not None else []
        # True when something downstream consumes the active output. A network
        # sink has an active output but nothing to hand it to.
        self.output_wired = output_wired

    @property
    def title(self):
        if self.instance_name:
            return self.instance_name + " : " + (self.type_name or "?")
        return self.type_name or "?"

    @property
    def is_operator(self):
        """Operators and functions have no instance, so they inline as expressions."""
        return not self.instance_name

    def __repr__(self):
        return "Call(%r, %r)" % (self.type_name, self.instance_name)


class Network(object):
    """One FBD network: a comment, and the outputs its logic drives.

    A network can drive several outputs from shared logic - CODESYS draws that
    as one box with the wire branching. Treating each output as its own
    network duplicates the shared expression and makes the numbering disagree
    with the editor, which is what a reviewer compares against.
    """

    def __init__(self, comment="", outputs=None):
        self.comment = comment
        self.outputs = outputs if outputs is not None else []

    def __repr__(self):
        return "Network(%r, %d outputs)" % (self.comment, len(self.outputs))


class Assign(object):
    """An outVariable: a network whose result is stored into a variable.

    Like an inVariable, CODESYS can negate the pin in place - and dropping
    that inverts the stored value.
    """

    def __init__(self, label, source=None, negated=False):
        self.label = label
        self.source = source
        self.negated = negated

    def __repr__(self):
        return "Assign(%r, negated=%r)" % (self.label, self.negated)


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
        output_wired=False,
        power_negated=False,
        negated_outputs=None,
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
        # Blocks only: the negation bubble on the pin the rung's power enters
        # through, and the set of output pins carrying one. Both invert the
        # logic in place if dropped.
        self.power_negated = power_negated
        self.negated_outputs = negated_outputs if negated_outputs is not None else set()
        # True when something downstream actually consumes the active output,
        # so the renderer knows whether to break the box edge with a tee.
        self.output_wired = output_wired

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
