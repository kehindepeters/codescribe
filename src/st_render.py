# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Emit equivalent Structured Text for LD and FBD networks.

The ASCII renderers preserve the shape of the diagram; this preserves the
logic and throws the shape away. It is the better of the two for review: it
diffs line by line, it greps, and reviewers already read ST.

It is a rendering, not a translation - the output is not guaranteed to compile
and must never be fed back into CODESYS. Notably a coil is transparent to
power flow, so the condition carries on past it, which reads oddly in ST but
matches what the rung does.
"""

from ld_render import render_declaration
from model import (
    BLOCK,
    COIL,
    JUMP,
    LABEL,
    OUT_VARIABLE,
    RETURN,
    Assign,
    Call,
    Element,
    Jump,
    Label,
    OutputRef,
    Series,
    Signal,
    is_simple_term,
)
from parse_ld import expr_to_text


def _coil_statement(coil, condition):
    condition = condition or "TRUE"
    target = coil.label or "?"
    if coil.storage == "set":
        return "IF %s THEN %s := TRUE; END_IF" % (condition, target)
    if coil.storage == "reset":
        return "IF %s THEN %s := FALSE; END_IF" % (condition, target)
    if coil.negated:
        return "%s := NOT (%s);" % (target, condition)
    return "%s := %s;" % (target, condition)


def rung_to_statements(rung):
    """One rung to a list of ST statements, walking the power flow left to right."""
    items = rung.items if isinstance(rung, Series) else [rung]
    statements = []
    condition = None

    for item in items:
        if isinstance(item, Element) and item.kind == BLOCK:
            # A box wired into one of this box's side pins runs first and has
            # inputs of its own to state. It is a sub-rung with its own power
            # flow, so it walks the same way rather than folding into this
            # rung's condition.
            for pin_block in item.pin_blocks:
                statements.extend(rung_to_statements(pin_block))
            args = []
            for pin, label in item.input_pins:
                # A label of None is the power pin, fed by the rung so far.
                value = condition if label is None else label
                if label is None and item.power_negated:
                    # The negation bubble on the power pin itself. A bare
                    # rail feed has no condition, but the inversion must
                    # still be stated or the ST reads as un-negated.
                    value = "NOT " + _operand(value) if value else "NOT TRUE"
                if value:
                    args.append("%s := %s" % (pin, value))
            name = item.instance_name or item.type_name or "?"
            statements.append("%s(%s);" % (name, ", ".join(args)))
            # An assignment written straight onto an output pin executes every
            # scan; the diagram draws it, so the ST must say it too. A negated
            # pin stores its inverse.
            for pin, assigned in item.output_pins:
                if assigned:
                    value = "%s.%s" % (name, pin)
                    if pin in item.negated_outputs:
                        value = "NOT " + value
                    statements.append("%s := %s;" % (assigned, value))
            condition = (name + "." + item.active_output) if item.active_output else name
            if item.active_output in item.negated_outputs:
                condition = "NOT " + condition
        elif isinstance(item, Element) and item.kind == COIL:
            statements.append(_coil_statement(item, condition))
        elif isinstance(item, Element) and item.kind == OUT_VARIABLE:
            # A store through an outVariable element - the standard shape for
            # a non-boolean result. Power passes through, like a coil.
            value = condition or "TRUE"
            if item.negated:
                value = "NOT " + _operand(value)
            statements.append("%s := %s;" % (item.label or "?", value))
        elif isinstance(item, Element) and item.kind in (JUMP, RETURN):
            # A jump ends the rung; its guard is the rung condition so far.
            # Same comment form as the FBD path, so both grep alike.
            target = (item.label or "?") if item.kind == JUMP else "RETURN"
            if condition:
                statements.append("IF %s THEN (* JMP %s *) END_IF" % (condition, target))
            else:
                statements.append("(* JMP %s *)" % target)
        elif isinstance(item, Element) and item.kind == LABEL:
            statements.append("(* label: %s *)" % (item.label or "?"))
        else:
            text = expr_to_text(item)
            if text:
                condition = text if condition is None else condition + " AND " + text

    return statements


# Operators CODESYS draws as boxes but everyone reads as infix. A conversion
# like REAL_TO_UINT is left as a call, because that is how it reads in ST too.
INFIX_OPERATORS = {
    "AND": "AND",
    "OR": "OR",
    "XOR": "XOR",
    "ADD": "+",
    "SUB": "-",
    "MUL": "*",
    "DIV": "/",
    "MOD": "MOD",
    "GT": ">",
    "GE": ">=",
    "LT": "<",
    "LE": "<=",
    "EQ": "=",
    "NE": "<>",
}


def _operand(text):
    """Parenthesise anything that is not a single term.

    Redundant brackets are preferable to an expression that reads correctly
    but groups wrongly - and "iCount>5" is as compound as "xA OR xB", see
    is_simple_term.
    """
    return text if is_simple_term(text) else "(" + text + ")"


def _operator_expression(node, values):
    symbol = INFIX_OPERATORS.get(node.type_name)
    if symbol and len(values) >= 2:
        return (" " + symbol + " ").join(_operand(value) for value in values)
    if node.type_name == "NOT" and len(values) == 1:
        return "NOT " + _operand(values[0])
    return "%s(%s)" % (node.type_name or "?", ", ".join(values))


def _fbd_value(node, statements, emitted=None):
    """Value of a node as ST text, appending any statements it needs first.

    ``emitted`` maps an already-rendered node to its value, so a block
    feeding two outputs is called once rather than once per output.
    """
    if emitted is None:
        emitted = {}
    if node is None:
        return ""

    if isinstance(node, OutputRef):
        # The call is emitted once however many of its pins are read; only the
        # value differs per reader, so it is computed here rather than
        # memoised with the call.
        value = _fbd_value(node.call, statements, emitted)
        if node.call.is_operator or not node.pin:
            # An operator has no instance to take a pin from; it inlines as
            # the one expression whichever pin reads it.
            return value
        text = "%s.%s" % (node.call.instance_name, node.pin)
        if node.pin in node.call.negated_outputs:
            text = "NOT " + text
        return text

    if isinstance(node, Signal):
        return node.text

    if isinstance(node, Label):
        statements.append("(* label: %s *)" % node.name)
        return ""

    if isinstance(node, Jump):
        condition = _fbd_value(node.condition, statements, emitted)
        if condition:
            statements.append("IF %s THEN (* JMP %s *) END_IF" % (condition, node.target))
        else:
            statements.append("(* JMP %s *)" % node.target)
        return ""

    if isinstance(node, Assign):
        value = _fbd_value(node.source, statements, emitted) or "FALSE"
        if node.negated:
            value = "NOT " + _operand(value)
        statements.append("%s := %s;" % (node.label or "?", value))
        return node.label or "?"

    if isinstance(node, Call):
        if id(node) in emitted:
            return emitted[id(node)]
        pairs = []
        for pin, source in node.inputs:
            value = _fbd_value(source, statements, emitted)
            if value:
                pairs.append((pin, value))

        def remember(value):
            emitted[id(node)] = value
            return value

        if node.st_code:
            # An EXECUTE box is inline ST already, so emit it as itself rather
            # than as a call to a box that has no body.
            guard = dict(pairs).get("EN")
            if guard and guard != "TRUE":
                statements.append("IF %s THEN" % guard)
                statements.extend("    " + line for line in node.st_code)
                statements.append("END_IF")
            else:
                statements.extend(node.st_code)
            return remember("")

        if node.is_operator:
            # Operators and functions have no instance to call, so they inline
            # as an expression rather than a statement.
            expression = _operator_expression(node, [value for _pin, value in pairs])
            if node.active_output in node.negated_outputs:
                expression = "NOT " + _operand(expression)
            return remember(expression)

        name = node.instance_name
        statements.append("%s(%s);" % (name, ", ".join("%s := %s" % (pin, value) for pin, value in pairs)))
        for pin, assigned in node.outputs:
            if assigned:
                value = "%s.%s" % (name, pin)
                # A negated output pin stores its inverse.
                if pin in node.negated_outputs:
                    value = "NOT " + value
                statements.append("%s := %s;" % (assigned, value))
        result = (name + "." + node.active_output) if node.active_output else name
        if node.active_output in node.negated_outputs:
            result = "NOT " + result
        return remember(result)

    return "?"


def network_to_statements(network):
    """Statements for one network, which may drive several outputs.

    The shared logic is emitted once: a function block feeding two outputs is
    called once in the program, so calling it twice here would misrepresent
    it. Plain expressions still repeat, which is what ST would say anyway.
    """
    statements = []
    emitted = {}
    for tree in getattr(network, "outputs", [network]):
        before = len(statements)
        value = _fbd_value(tree, statements, emitted)
        if len(statements) == before and value:
            # A bare expression with nothing to assign it to - keep it visible
            # rather than dropping it entirely.
            statements.append("(* " + value + " *)")
    return statements


def _network_header(index, comment):
    header = "(* Network " + str(index + 1)
    if comment:
        comment = comment.replace("\r", " ").replace("\n", " ").replace("*)", "* )")
        header += ": " + comment.lstrip("/").strip()
    return header + " *)"


def render_pou(pou):
    """Render a POU as declaration plus ST statements, one block per network."""
    lines = render_declaration(pou)
    lines.append("")

    for index, rung in enumerate(pou.rungs):
        lines.append(_network_header(index, ""))
        lines.extend(rung_to_statements(rung))
        lines.append("")

    for index, network in enumerate(pou.networks):
        lines.append(_network_header(index, network.comment))
        lines.extend(network_to_statements(network))
        lines.append("")

    if not pou.rungs and not pou.networks:
        lines.append("(* no networks *)")

    while lines and lines[-1] == "":
        lines.pop()

    return [line.rstrip() for line in lines]
