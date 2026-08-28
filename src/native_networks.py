# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Read the network list out of a native CODESYS export, and align it with
the networks parsed from PLCopen.

The .txt rendering is derived from a PLCopen export, but PLCopen is not what
a reviewer holds next to it - the native .xml is. The two disagree about
networks in one important way: a network that is out-commented in the editor
(Toggle Network Comment State) is simply absent from the PLCopen export, and
an empty network parses to nothing. Numbering the rendered networks 1..n in
survival order therefore drifts away from the numbering the reviewer sees in
CODESYS and in the native .xml - silently, and in both directions once
labelled networks are involved.

The native export, which codescribe writes immediately before rendering,
carries the full list: every network in editor order, each with its
OutCommented flag, its comment and its items. That list is the numbering
authority. Aligning the parsed networks against it lets the rendering keep
the editor's numbers and show a placeholder where a disabled or empty network
sits, instead of pretending it does not exist.

The native format is proprietary and undocumented, so everything here is
best-effort: any surprise degrades to returning None, and the caller falls
back to sequential numbering exactly as before.
"""

import os

import plcopen
import xmlbackend

# Notes attached to placeholder entries. Rendered inside a (* *) comment under
# the network header, so a reviewer reading only the .txt learns that logic
# exists here without executing - the two facts the silent drop hid.
NOTE_OUT_COMMENTED = "out-commented in CODESYS - does not execute; diagram not exported, see the native xml"
NOTE_EMPTY = "empty network"


class NativeNetwork(object):
    """One network as the native export records it."""

    def __init__(self, out_commented=False, empty=False, comment="", label=""):
        self.out_commented = out_commented
        self.empty = empty
        self.comment = comment
        # The jump-target label CODESYS stores on the network itself. PLCopen
        # exports it as a free-standing element that parses into a phantom
        # network, so alignment needs to know which real network owns it.
        self.label = label

    def __repr__(self):
        return "NativeNetwork(out_commented=%r, empty=%r, comment=%r, label=%r)" % (
            self.out_commented,
            self.empty,
            self.comment,
            self.label,
        )


def _named_children(elem):
    """(tag, Name attribute, child) for each child element."""
    for child in elem:
        yield plcopen.tag(child), child.get("Name"), child


def read_networks(path):
    """[NativeNetwork] in editor order, or None if the file yields none.

    Networks live under <List2 Name="NetworkList">; each entry carries its
    flags as <Single Name="..."> children. Entries without an OutCommented
    flag are not networks and are skipped. None rather than [] on any
    trouble: the caller treats None as "no authority available" and keeps
    the sequential numbering.
    """
    try:
        if not os.path.exists(path):
            return None
        root = xmlbackend.parse(plcopen.read_document(path))
    except Exception:
        return None

    networks = []
    for elem in root.iter():
        if plcopen.tag(elem) != "List2" or elem.get("Name") != "NetworkList":
            continue
        for net in elem:
            out_commented = None
            comment = ""
            title = ""
            label = ""
            items_elem = None
            for tag_name, name, child in _named_children(net):
                if tag_name == "Single" and name == "OutCommented":
                    out_commented = (child.text or "").strip() == "True"
                elif tag_name == "Single" and name == "Comment":
                    comment = (child.text or "").strip()
                elif tag_name == "Single" and name == "Title":
                    title = (child.text or "").strip()
                elif tag_name == "Single" and name == "Label":
                    label = (child.text or "").strip()
                elif tag_name == "List2" and name == "NetworkItems":
                    items_elem = child
            if out_commented is None:
                continue
            # The .NET element wrapper has no __len__, so emptiness is
            # probed by iterating.
            empty = items_elem is None or not any(True for _ in items_elem)
            networks.append(
                NativeNetwork(out_commented=out_commented, empty=empty, comment=comment or title, label=label)
            )
    return networks or None


def merge(native, items, is_label=None):
    """[(number, comment, note, [items])] aligned to the native list, or None.

    Each native network consumes the parsed items it accounts for, in order:
    one renderable item when it is neither out-commented nor empty, plus a
    leading label item when it carries a label - PLCopen exports the label
    as a free-standing element that parses into a phantom network of its
    own, sitting just before the network it belongs to. ``is_label``
    recognises those phantoms; without it no label item is ever consumed.

    Anything left over or missing at the end means an assumption broke (a
    network split into two components, or this CODESYS exports disabled
    networks after all), and misnumbering silently is worse than saying so:
    None sends the caller down the sequential fallback, flagged.
    """
    if not native:
        return None

    result = []
    index = 0
    for number, network in enumerate(native, 1):
        taken = []
        if network.label and is_label is not None and index < len(items) and is_label(items[index]):
            taken.append(items[index])
            index += 1
        if not network.out_commented and not network.empty:
            if index >= len(items):
                return None
            taken.append(items[index])
            index += 1
        if network.out_commented:
            note = NOTE_OUT_COMMENTED
        elif not taken:
            note = NOTE_EMPTY
        else:
            note = None
        comment = network.comment
        if note is not None and not comment:
            # Some projects put the network's description in its Label field
            # rather than its comment. For a placeholder that text is all
            # there is to say what the hidden network does.
            comment = network.label
        result.append((number, comment, note, taken))
    if index != len(items):
        return None
    return result


def entries_for(pou, items, is_label=None):
    """Numbered render entries for a POU's networks or rungs.

    Each entry is (number, comment, note, [items]). Native-aligned when a
    native list is attached to the pou and lines up; sequential otherwise -
    one item per entry, exactly the numbering the rendering always had. The
    outcome is flagged on the pou (``native_merged`` /
    ``native_merge_failed``) so the caller can warn about a failed alignment
    instead of shipping silently drifted numbers.
    """
    native = getattr(pou, "native_networks", None)
    if native:
        merged = merge(native, items, is_label)
        if merged is not None:
            pou.native_merged = True
            return merged
        pou.native_merge_failed = True

    entries = []
    for index, item in enumerate(items):
        comment = getattr(item, "comment", "") or ""
        entries.append((index + 1, comment, None, [item]))
    return entries
