# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Text-grid composition shared by the graphical renderers.

A Block is a rectangle of text plus the row its wire enters and leaves on.
Renderers build small Blocks for leaves and compose them; nothing else needs
to know about absolute coordinates.
"""


class Block(object):
    def __init__(self, lines, connect_row):
        self.lines = lines
        self.connect_row = connect_row

    @property
    def width(self):
        if not self.lines:
            return 0
        return max(len(line) for line in self.lines)

    def padded(self, width, wire_rows=None):
        """Lines padded to ``width``, extending wires with dashes.

        Rows listed in ``wire_rows`` (defaulting to this Block's own connect
        row) are filled with dashes so a short branch still reaches the
        junction on its right. Every other row is filled with spaces.
        """
        if wire_rows is None:
            wire_rows = set([self.connect_row])
        out = []
        for index, line in enumerate(self.lines):
            fill = "-" if index in wire_rows else " "
            out.append(line + fill * (width - len(line)))
        return out


def stack(blocks):
    """Stack Blocks vertically. Returns (lines, absolute connect rows)."""
    lines = []
    connect_rows = []
    for block in blocks:
        connect_rows.append(len(lines) + block.connect_row)
        lines.extend(block.lines)
    return lines, connect_rows
