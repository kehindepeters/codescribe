# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Low-level PLCopen XML helpers shared by every language renderer.

Namespaces are stripped rather than matched. The exact URI varies between
schema revisions - the hand-authored fixture is tc6_0201, real CODESYS output
is tc6_0200 - and CODESYS layers proprietary extensions on top. Matching local
tag names survives all of it.
"""

import io
import xml.etree.ElementTree as ET

from model import Connection

TRUTHY = ("true", "1")

# Body element names, one of which wraps every POU implementation.
BODY_LANGUAGES = ("LD", "FBD", "SFC", "ST", "IL", "CFC")


def tag(elem):
    """Local tag name, with any namespace stripped."""
    return elem.tag.split("}")[-1]


def find_child(elem, name):
    for child in elem:
        if tag(child) == name:
            return child
    return None


def child_text(elem, name):
    child = find_child(elem, name)
    if child is None or child.text is None:
        return None
    return child.text.strip()


def is_true(elem, attr_name):
    return (elem.get(attr_name) or "").lower() in TRUTHY


def attr(elem, name):
    """An attribute, treating CODESYS's literal "none" as absent."""
    value = elem.get(name)
    if value in (None, "", "none"):
        return None
    return value


def direct_connections(elem):
    """Wires arriving at this element's own connectionPointIn children.

    Several <connection> under a single connectionPointIn is how PLCopen
    spells a parallel branch (a wired OR), so order and multiplicity matter.
    This deliberately does not recurse: a block's pins hang off
    <inputVariables> and are collected separately, with their pin names.
    """
    connections = []
    for point in elem:
        if tag(point) != "connectionPointIn":
            continue
        for child in point:
            if tag(child) != "connection":
                continue
            ref = child.get("refLocalId")
            if ref is not None:
                connections.append(Connection(ref, source_pin=attr(child, "formalParameter")))
    return connections


def block_connections(block_elem):
    """Wires arriving at a block, tagged with the pin they land on."""
    connections = []
    for group_name in ("inputVariables", "inOutVariables"):
        group = find_child(block_elem, group_name)
        if group is None:
            continue
        for var in group:
            if tag(var) != "variable":
                continue
            pin = var.get("formalParameter")
            for connection in direct_connections(var):
                connection.target_pin = pin
                connections.append(connection)
    return connections


def block_outputs(block_elem):
    """(pin, assigned variable) for each block output.

    CODESYS writes an assignment straight onto the output pin as
    <connectionPointOut><expression>uiCurrSupplyVolt</expression>.
    """
    outputs = []
    group = find_child(block_elem, "outputVariables")
    if group is None:
        return outputs
    for var in group:
        if tag(var) != "variable":
            continue
        assigned = None
        point = find_child(var, "connectionPointOut")
        if point is not None:
            expression = find_child(point, "expression")
            if expression is not None and expression.text:
                assigned = expression.text.strip()
        outputs.append((var.get("formalParameter"), assigned))
    return outputs


def comment_text(elem):
    """The text of a <comment>, which nests its content in an xhtml element."""
    content = find_child(elem, "content")
    if content is None:
        return ""
    xhtml = find_child(content, "xhtml")
    if xhtml is None or xhtml.text is None:
        return ""
    return xhtml.text.strip()


# --- interface -------------------------------------------------------------

SCOPE_TAGS = {
    "localVars": "VAR",
    "inputVars": "VAR_INPUT",
    "outputVars": "VAR_OUTPUT",
    "inOutVars": "VAR_IN_OUT",
    "tempVars": "VAR_TEMP",
    "globalVars": "VAR_GLOBAL",
}


def _type_name(var_elem):
    type_elem = find_child(var_elem, "type")
    if type_elem is None:
        return "BOOL"
    for child in type_elem:
        name = tag(child)
        if name == "derived":
            return child.get("name") or "UNKNOWN"
        return name
    return "BOOL"


def _initial_value(var_elem):
    value_elem = find_child(var_elem, "initialValue")
    if value_elem is None:
        return None
    simple = find_child(value_elem, "simpleValue")
    if simple is None:
        return None
    return simple.get("value")


def parse_interface(interface_elem):
    """Variables from a POU interface, in declaration order."""
    from model import Variable

    variables = []
    if interface_elem is None:
        return variables
    for group in interface_elem:
        scope = SCOPE_TAGS.get(tag(group))
        if scope is None:
            continue
        if is_true(group, "constant"):
            scope += " CONSTANT"
        for var_elem in group:
            if tag(var_elem) != "variable":
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


def read_document(source):
    """Document bytes, with anything before the first tag removed.

    CODESYS writes a UTF-8 BOM on every export_xml file, and the ElementTree
    that CODESYS ships in ScriptLib rejects it outright:

        Error('Syntax error at line 1: illegal data at start of file',)

    CPython's expat accepts a BOM silently, so this is invisible outside
    CODESYS. Slicing to the first "<" handles the BOM however it is
    represented, plus any stray leading whitespace, in one step - nothing
    before the first tag can be XML anyway.

    ``source`` is a path or a file object. io.open is used rather than the
    builtin so a binary read returns real bytes under IronPython too.
    """
    if hasattr(source, "read"):
        data = source.read()
    else:
        handle = io.open(source, "rb")
        try:
            data = handle.read()
        finally:
            handle.close()

    if not isinstance(data, bytes):
        data = data.encode("utf-8")

    start = data.find(b"<")
    if start > 0:
        data = data[start:]
    return _to_ascii(data)


def _to_ascii(data):
    """Replace non-ASCII characters with XML numeric character references.

    The ElementTree CODESYS ships works byte-wise and rejects UTF-8 multi-byte
    sequences outright:

        Error('Syntax error at line 216: illegal character in content',)

    A numeric reference is plain ASCII, and every parser expands it back to
    the same character, so the parsed result is identical while the bytes
    handed to the parser are safe. One degree sign in a comment is enough to
    lose a whole POU otherwise.

    Safe as a blanket transform because PLCopen exports contain no CDATA
    sections, which are the one place a numeric reference would stay literal
    text instead of being expanded.
    """
    if not any(byte > 0x7F for byte in bytearray(data)):
        return data

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        # Not valid UTF-8 despite the declaration. latin-1 cannot fail, and
        # preserves every byte as a character so nothing is lost.
        text = data.decode("latin-1")

    pieces = []
    for character in text:
        if ord(character) < 128:
            pieces.append(character)
        else:
            pieces.append("&#%d;" % ord(character))
    return "".join(pieces).encode("ascii")


# XML 1.0 forbids these outright - they cannot even be written as a numeric
# reference, so a document containing one is malformed at the source.
_LEGAL_CONTROL = (0x09, 0x0A, 0x0D)


def describe_suspect_characters(source, limit=5):
    """Characters likely to make a parser reject the document.

    Reported on a rendering failure so the next run explains itself, rather
    than needing another round of manual diagnosis.
    """
    try:
        handle = io.open(source, "rb")
        try:
            raw = handle.read()
        finally:
            handle.close()
    except (IOError, OSError) as error:
        return ["could not re-read the file: " + repr(error)]

    notes = []
    for line_number, line in enumerate(raw.split(b"\n"), 1):
        for column, byte in enumerate(bytearray(line), 1):
            if byte < 0x20 and byte not in _LEGAL_CONTROL:
                notes.append(
                    "line %d column %d: control character 0x%02X, illegal in XML 1.0" % (line_number, column, byte)
                )
            elif byte > 0x7F:
                notes.append("line %d column %d: non-ASCII byte 0x%02X" % (line_number, column, byte))
            if len(notes) >= limit:
                return notes
    return notes


def iter_bodies(source):
    """Yield (pou_elem, language, body_elem) for every POU with an implementation."""
    root = ET.fromstring(read_document(source))
    for elem in root.iter():
        if tag(elem) != "pou":
            continue
        body = find_child(elem, "body")
        if body is None:
            continue
        for child in body:
            if tag(child) in BODY_LANGUAGES:
                yield elem, tag(child), child
                break
