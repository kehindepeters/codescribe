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
from model import BLOCK, COIL, Assign, Call, Element, Jump, Label, Series, Signal
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
            args = []
            for pin, label in item.input_pins:
                # A label of None is the power pin, fed by the rung so far.
                value = condition if label is None else label
                if value:
                    args.append("%s := %s" % (pin, value))
            name = item.instance_name or item.type_name or "?"
            statements.append("%s(%s);" % (name, ", ".join(args)))
            condition = (name + "." + item.active_output) if item.active_output else name
        elif isinstance(item, Element) and item.kind == COIL:
            statements.append(_coil_statement(item, condition))
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
    but groups wrongly.
    """
    return ("(" + text + ")") if " " in text else text


def _operator_expression(node, values):
    symbol = INFIX_OPERATORS.get(node.type_name)
    if symbol and len(values) >= 2:
        return (" " + symbol + " ").join(_operand(value) for value in values)
    if node.type_name == "NOT" and len(values) == 1:
        return "NOT " + _operand(values[0])
    return "%s(%s)" % (node.type_name or "?", ", ".join(values))


def _fbd_value(node, statements):
    """Value of a node as ST text, appending any statements it needs first."""
    if node is None:
        return ""

    if isinstance(node, Signal):
        return node.text

    if isinstance(node, Label):
        statements.append("(* label: %s *)" % node.name)
        return ""

    if isinstance(node, Jump):
        condition = _fbd_value(node.condition, statements)
        if condition:
            statements.append("IF %s THEN (* JMP %s *) END_IF" % (condition, node.target))
        else:
            statements.append("(* JMP %s *)" % node.target)
        return ""

    if isinstance(node, Assign):
        value = _fbd_value(node.source, statements)
        statements.append("%s := %s;" % (node.label or "?", value or "FALSE"))
        return node.label or "?"

    if isinstance(node, Call):
        pairs = []
        for pin, source in node.inputs:
            value = _fbd_value(source, statements)
            if value:
                pairs.append((pin, value))

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
            return ""

        if node.is_operator:
            # Operators and functions have no instance to call, so they inline
            # as an expression rather than a statement.
            return _operator_expression(node, [value for _pin, value in pairs])

        name = node.instance_name
        statements.append("%s(%s);" % (name, ", ".join("%s := %s" % (pin, value) for pin, value in pairs)))
        for pin, assigned in node.outputs:
            if assigned:
                statements.append("%s := %s.%s;" % (assigned, name, pin))
        return (name + "." + node.active_output) if node.active_output else name

    return "?"


def network_to_statements(tree):
    statements = []
    value = _fbd_value(tree, statements)
    if not statements and value:
        # A bare expression with nothing to assign it to - keep it visible
        # rather than dropping the network entirely.
        statements.append("(* " + value + " *)")
    return statements


def _network_header(index, comment):
    header = "(* Network " + str(index + 1)
    if comment:
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
        comment, tree = network
        lines.append(_network_header(index, comment))
        lines.extend(network_to_statements(tree))
        lines.append("")

    if not pou.rungs and not pou.networks:
        lines.append("(* no networks *)")

    while lines and lines[-1] == "":
        lines.pop()

    return [line.rstrip() for line in lines]
