"""A small BPMN 2.0 model: pools, lanes, events, tasks, gateways and flows on a grid,
written out as standard BPMN 2.0 XML with its diagram (BPMN DI).

The files open as they are in Camunda Modeler, bpmn.io or any tool that reads
BPMN 2.0: a collaboration with the company's pool split into lanes, black-box
pools for the outside parties (customer, mill), message flows between them,
and a layout for every shape and edge. Shapes sit on a grid (a column and a
lane, nudged up or down by `dy`), so a process is described in a few lines
and the coordinates follow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.sax.saxutils import escape, quoteattr

TASKS = {"task", "userTask", "serviceTask", "sendTask", "receiveTask", "manualTask", "businessRuleTask"}
EVENTS = {"startEvent", "endEvent", "intermediateCatchEvent", "intermediateThrowEvent", "boundaryEvent"}
GATEWAYS = {"exclusiveGateway", "parallelGateway", "eventBasedGateway"}

POOL_X = 120
BAND = 30  # label band of a pool and of a lane
FIRST_X = POOL_X + 2 * BAND + 70  # centre of column 0
COL_W = 132
BLACK_BOX_H = 64
POOL_GAP = 36

COLORS = {  # (fill, stroke)
    "task": ("#EEF3F9", "#0B2545"),
    "startEvent": ("#E3F4EF", "#0E7C69"),
    "endEvent": ("#FBE9E9", "#B33131"),
    "intermediateCatchEvent": ("#FFFFFF", "#134074"),
    "intermediateThrowEvent": ("#FFFFFF", "#134074"),
    "boundaryEvent": ("#FFF4EC", "#C0510F"),
    "gateway": ("#FFF4EC", "#C0510F"),
    "annotation": ("#FFFFFF", "#8A96A8"),
}


@dataclass
class Node:
    id: str
    type: str  # BPMN element: userTask, exclusiveGateway, startEvent, ...
    name: str
    lane: str
    col: float
    dy: float = 0  # offset from the lane's centre line, px
    trigger: str | None = None  # event definition: "message" or "timer"
    timer: str | None = None  # ISO 8601 duration of a timer event, e.g. "P3D"
    attached_to: str | None = None  # boundary events: the task they sit on
    doc: str = ""  # where the step lives in Dynamics 365
    table: str | None = None  # Dataverse table behind the step
    label: str = "below"  # where an event's or gateway's name goes: below, above, right


@dataclass
class Flow:
    source: str
    target: str
    name: str = ""
    kind: str = "sequence"  # or "message"
    route: str = "auto"  # auto, v, vh, hv, hvh, up:<px>, down:<px>
    dx: float = 0  # message flows: shift sideways so two flows on one task don't overlap
    id: str = ""


@dataclass
class Lane:
    id: str
    name: str
    height: int = 150


@dataclass
class Pool:
    id: str
    name: str
    lanes: list[Lane] = field(default_factory=list)  # empty: a black-box pool (outside party)


@dataclass
class Note:
    id: str
    text: str
    lane: str
    col: float
    dy: float
    target: str
    width: int = 190


@dataclass
class Process:
    id: str
    name: str
    description: str
    pools: list[Pool]
    nodes: list[Node]
    flows: list[Flow]
    notes: list[Note] = field(default_factory=list)

    def node(self, node_id: str) -> Node:
        return next(n for n in self.nodes if n.id == node_id)


def _size(node: Node) -> tuple[int, int]:
    if node.type in TASKS:
        return 100, 80
    if node.type in GATEWAYS:
        return 50, 50
    return 36, 36


class Layout:
    """Coordinates of every pool, lane, shape and edge of a process."""

    def __init__(self, proc: Process):
        self.proc = proc
        cols = [n.col for n in proc.nodes] + [n.col for n in proc.notes]
        self.width = int(FIRST_X + max(cols) * COL_W + 110 - POOL_X)
        self.pools: dict[str, tuple] = {}
        self.lanes: dict[str, tuple] = {}
        y = 60
        for pool in proc.pools:
            h = sum(lane.height for lane in pool.lanes) if pool.lanes else BLACK_BOX_H
            self.pools[pool.id] = (POOL_X, y, self.width, h)
            ly = y
            for lane in pool.lanes:
                self.lanes[lane.id] = (POOL_X + BAND, ly, self.width - BAND, lane.height)
                ly += lane.height
            y += h + POOL_GAP
        self.height = y - POOL_GAP + 60
        self.shapes: dict[str, tuple] = {}
        for n in proc.nodes:
            if n.attached_to:
                continue
            w, h = _size(n)
            lx, ly, lw, lh = self.lanes[n.lane]
            cx, cy = FIRST_X + n.col * COL_W, ly + lh / 2 + n.dy
            self.shapes[n.id] = (cx - w / 2, cy - h / 2, w, h)
        for n in proc.nodes:  # boundary events sit on the bottom edge of their task
            if n.attached_to:
                tx, ty, tw, th = self.shapes[n.attached_to]
                self.shapes[n.id] = (tx + tw - 36, ty + th - 18, 36, 36)
        self.notes: dict[str, tuple] = {}
        for note in proc.notes:
            lx, ly, lw, lh = self.lanes[note.lane]
            lines = max(1, round(len(note.text) / (note.width / 7.2) + 0.5))
            h = 14 * lines + 12
            cx, cy = FIRST_X + note.col * COL_W, ly + lh / 2 + note.dy
            self.notes[note.id] = (cx - note.width / 2, cy - h / 2, note.width, h)
        self.edges = {f.id: self._route(f) for f in proc.flows}

    # ---------------------------------------------------------------- edges
    @staticmethod
    def _c(b):
        x, y, w, h = b
        return x + w / 2, y + h / 2

    @staticmethod
    def _side(b, side):
        x, y, w, h = b
        return {"left": (x, y + h / 2), "right": (x + w, y + h / 2), "top": (x + w / 2, y),
                "bottom": (x + w / 2, y + h)}[side]

    def _route(self, f: Flow) -> list[tuple[float, float]]:
        if f.kind == "message":
            return self._message(f)
        s, t = self.shapes[f.source], self.shapes[f.target]
        (sx, sy), (tx, ty) = self._c(s), self._c(t)
        src_type = self.proc.node(f.source).type
        route = f.route
        if route == "auto":
            if abs(sy - ty) < 1:
                route = "straight"
            elif src_type in GATEWAYS or self.proc.node(f.source).attached_to:
                route = "vh"
            elif self.proc.node(f.target).type in GATEWAYS:
                route = "hv"
            else:
                route = "hvh"
        if route == "v":  # straight up or down to a shape in the same column
            return [self._side(s, "bottom" if ty > sy else "top"), self._side(t, "top" if ty > sy else "bottom")]
        if route == "straight":
            return [self._side(s, "right"), self._side(t, "left")] if tx > sx else \
                [self._side(s, "left"), self._side(t, "right")]
        if route == "vh":  # leave from the top or bottom, turn, enter from the side
            a = self._side(s, "bottom" if ty > sy else "top")
            b = self._side(t, "left" if tx > sx else "right")
            return [a, (a[0], b[1]), b]
        if route == "hv":  # leave from the side, turn, enter from the top or bottom
            a = self._side(s, "right" if tx > sx else "left")
            b = self._side(t, "top" if ty > sy else "bottom")
            return [a, (b[0], a[1]), b]
        if route == "hvh":
            a, b = self._side(s, "right"), self._side(t, "left")
            mx = (a[0] + b[0]) / 2
            return [a, (mx, a[1]), (mx, b[1]), b]
        direction, _, offset = route.partition(":")
        offset = float(offset or 30)
        if direction == "down":  # loop back underneath
            a, b = self._side(s, "bottom"), self._side(t, "bottom")
            y = max(a[1], b[1]) + offset
        else:  # loop back over the top
            a, b = self._side(s, "top"), self._side(t, "top")
            y = min(a[1], b[1]) - offset
        return [a, (a[0], y), (b[0], y), b]

    def _message(self, f: Flow) -> list[tuple[float, float]]:
        if f.source in self.pools:  # from an outside party into a shape
            px, py, pw, ph = self.pools[f.source]
            t = self.shapes[f.target]
            x = self._c(t)[0] + f.dx
            down = py < t[1]
            return [(x, py + ph if down else py), (x, t[1] if down else t[1] + t[3])]
        px, py, pw, ph = self.pools[f.target]
        s = self.shapes[f.source]
        x = self._c(s)[0] + f.dx
        up = py < s[1]
        return [(x, s[1] if up else s[1] + s[3]), (x, py + ph if up else py)]

    def label_bounds(self, n: Node) -> tuple[float, float, float, float]:
        x, y, w, h = self.shapes[n.id]
        cx = x + w / 2
        lw = 110
        lh = 14 * max(1, -(-len(n.name) // 15))
        if n.label == "right":
            return x + w + 6, y + h / 2 - lh / 2, lw, lh
        if n.label == "above":
            return cx - lw / 2, y - lh - 4, lw, lh
        return cx - lw / 2, y + h + 4, lw, lh

    def flow_label_bounds(self, f: Flow) -> tuple[float, float, float, float]:
        pts = self.edges[f.id]
        w, h = max(30, 6.6 * len(f.name)), 14
        vertical = abs(pts[0][0] - pts[1][0]) < 1
        if f.kind == "message" or (vertical and len(pts) == 2):  # beside the line, halfway
            (x1, y1), (x2, y2) = pts[0], pts[1]
            return (x1 - w - 5 if f.dx < 0 else x1 + 5), (y1 + y2) / 2 - h / 2, w, h
        if vertical:  # leaves up or down: name the branch just before the turn, left of the line
            xa, ya = pts[-2]
            return xa - w - 6, ya - h - 2, w, h
        (x1, y1), (x2, _) = pts[0], pts[1]
        return min(x1, x2) + 6, y1 - h - 3, w, h


# -------------------------------------------------------------------- XML
def _fmt(v: float) -> str:
    return str(int(round(v)))


def _bounds(b) -> str:
    x, y, w, h = b
    return f'<dc:Bounds x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(w)}" height="{_fmt(h)}" />'


def _color(kind: str) -> str:
    fill, stroke = COLORS.get(kind, COLORS["task"])
    return (f' bioc:stroke="{stroke}" bioc:fill="{fill}"'
            f' color:background-color="{fill}" color:border-color="{stroke}"')


def to_xml(proc: Process) -> str:
    """The process as a BPMN 2.0 XML document with diagram interchange."""
    lay = Layout(proc)
    for i, f in enumerate(proc.flows):
        if not f.id:
            f.id = f"{'Message' if f.kind == 'message' else 'Flow'}_{f.source}_{f.target}"
    ids = [f.id for f in proc.flows]
    assert len(ids) == len(set(ids)), f"duplicate flow ids in {proc.id}"
    lay.edges = {f.id: lay._route(f) for f in proc.flows}

    main = next(p for p in proc.pools if p.lanes)
    pid = f"Process_{proc.id}"
    seq = [f for f in proc.flows if f.kind == "sequence"]
    incoming = {n.id: [f.id for f in seq if f.target == n.id] for n in proc.nodes}
    outgoing = {n.id: [f.id for f in seq if f.source == n.id] for n in proc.nodes}

    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
           'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" '
           'xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" '
           'xmlns:di="http://www.omg.org/spec/DD/20100524/DI" '
           'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
           'xmlns:bioc="http://bpmn.io/schema/bpmn/biocolor/1.0" '
           'xmlns:color="http://www.omg.org/spec/BPMN/non-normative/color/1.0" '
           f'id="Definitions_{proc.id}" '
           'targetNamespace="https://github.com/Milad-Shabani/Steel-sales-crm-intelligence" '
           'exporter="steel_crm.processes" exporterVersion="1.0">']
    out.append(f'  <bpmn:collaboration id="Collaboration_{proc.id}">')
    out.append(f'    <bpmn:documentation>{escape(proc.description)}</bpmn:documentation>')
    for pool in proc.pools:
        ref = f' processRef="{pid}"' if pool is main else ""
        out.append(f'    <bpmn:participant id="{pool.id}" name={quoteattr(pool.name)}{ref} />')
    for f in proc.flows:
        if f.kind == "message":
            name = f" name={quoteattr(f.name)}" if f.name else ""
            out.append(f'    <bpmn:messageFlow id="{f.id}"{name} sourceRef="{f.source}" targetRef="{f.target}" />')
    out.append("  </bpmn:collaboration>")

    out.append(f'  <bpmn:process id="{pid}" name={quoteattr(proc.name)} isExecutable="false">')
    out.append(f'    <bpmn:laneSet id="LaneSet_{proc.id}">')
    for lane in main.lanes:
        out.append(f'      <bpmn:lane id="{lane.id}" name={quoteattr(lane.name)}>')
        for n in proc.nodes:
            if n.lane == lane.id:
                out.append(f"        <bpmn:flowNodeRef>{n.id}</bpmn:flowNodeRef>")
        out.append("      </bpmn:lane>")
    out.append("    </bpmn:laneSet>")
    for n in proc.nodes:
        attrs = f'id="{n.id}" name={quoteattr(n.name)}'
        if n.type == "boundaryEvent":
            attrs += f' cancelActivity="false" attachedToRef="{n.attached_to}"'
        out.append(f"    <bpmn:{n.type} {attrs}>")
        if n.doc:
            out.append(f"      <bpmn:documentation>{escape(n.doc)}</bpmn:documentation>")
        for fid in incoming[n.id]:
            out.append(f"      <bpmn:incoming>{fid}</bpmn:incoming>")
        for fid in outgoing[n.id]:
            out.append(f"      <bpmn:outgoing>{fid}</bpmn:outgoing>")
        if n.trigger == "message":
            out.append(f'      <bpmn:messageEventDefinition id="MessageDef_{n.id}" />')
        elif n.trigger == "timer":
            out.append(f'      <bpmn:timerEventDefinition id="TimerDef_{n.id}">')
            out.append(f'        <bpmn:timeDuration xsi:type="bpmn:tFormalExpression">{n.timer}</bpmn:timeDuration>')
            out.append("      </bpmn:timerEventDefinition>")
        out.append(f"    </bpmn:{n.type}>")
    for f in seq:
        name = f" name={quoteattr(f.name)}" if f.name else ""
        out.append(f'    <bpmn:sequenceFlow id="{f.id}"{name} sourceRef="{f.source}" targetRef="{f.target}" />')
    for note in proc.notes:
        out.append(f'    <bpmn:textAnnotation id="{note.id}"><bpmn:text>{escape(note.text)}</bpmn:text>'
                   "</bpmn:textAnnotation>")
        out.append(f'    <bpmn:association id="Association_{note.id}" associationDirection="None" '
                   f'sourceRef="{note.id}" targetRef="{note.target}" />')
    out.append("  </bpmn:process>")

    # ---------------------------------------------------------------- diagram
    out.append(f'  <bpmndi:BPMNDiagram id="Diagram_{proc.id}">')
    out.append(f'    <bpmndi:BPMNPlane id="Plane_{proc.id}" bpmnElement="Collaboration_{proc.id}">')
    for pool in proc.pools:
        out.append(f'      <bpmndi:BPMNShape id="{pool.id}_di" bpmnElement="{pool.id}" isHorizontal="true">'
                   f"{_bounds(lay.pools[pool.id])}</bpmndi:BPMNShape>")
        for lane in pool.lanes:
            out.append(f'      <bpmndi:BPMNShape id="{lane.id}_di" bpmnElement="{lane.id}" isHorizontal="true">'
                       f"{_bounds(lay.lanes[lane.id])}</bpmndi:BPMNShape>")
    for n in proc.nodes:
        kind = "task" if n.type in TASKS else "gateway" if n.type in GATEWAYS else n.type
        marker = ' isMarkerVisible="true"' if n.type == "exclusiveGateway" else ""
        label = ""
        if n.type not in TASKS and n.name:
            label = f"<bpmndi:BPMNLabel>{_bounds(lay.label_bounds(n))}</bpmndi:BPMNLabel>"
        out.append(f'      <bpmndi:BPMNShape id="{n.id}_di" bpmnElement="{n.id}"{marker}{_color(kind)}>'
                   f"{_bounds(lay.shapes[n.id])}{label}</bpmndi:BPMNShape>")
    for note in proc.notes:
        out.append(f'      <bpmndi:BPMNShape id="{note.id}_di" bpmnElement="{note.id}"{_color("annotation")}>'
                   f"{_bounds(lay.notes[note.id])}</bpmndi:BPMNShape>")
    for f in proc.flows:
        pts = "".join(f'<di:waypoint x="{_fmt(x)}" y="{_fmt(y)}" />' for x, y in lay.edges[f.id])
        label = f"<bpmndi:BPMNLabel>{_bounds(lay.flow_label_bounds(f))}</bpmndi:BPMNLabel>" if f.name else ""
        out.append(f'      <bpmndi:BPMNEdge id="{f.id}_di" bpmnElement="{f.id}">{pts}{label}</bpmndi:BPMNEdge>')
    for note in proc.notes:
        nx, ny, nw, nh = lay.notes[note.id]
        tx, ty, tw, th = lay.shapes[note.target]
        a = (nx + nw / 2, ny) if ny > ty else (nx + nw / 2, ny + nh)
        b = (tx + tw / 2, ty + th) if ny > ty else (tx + tw / 2, ty)
        out.append(f'      <bpmndi:BPMNEdge id="Association_{note.id}_di" bpmnElement="Association_{note.id}">'
                   f'<di:waypoint x="{_fmt(a[0])}" y="{_fmt(a[1])}" />'
                   f'<di:waypoint x="{_fmt(b[0])}" y="{_fmt(b[1])}" /></bpmndi:BPMNEdge>')
    out.append("    </bpmndi:BPMNPlane>")
    out.append("  </bpmndi:BPMNDiagram>")
    out.append("</bpmn:definitions>")
    return "\n".join(out) + "\n"


def process_payload(proc: Process, overlays: dict[str, dict]) -> dict:
    """What the dashboard needs: the XML to draw, and for each step its lane, table and numbers."""
    lanes = {lane.id: lane.name for pool in proc.pools for lane in pool.lanes}
    steps = {n.id: {"name": n.name, "type": n.type, "lane": lanes[n.lane], "table": n.table, "doc": n.doc,
                    "label": n.label, "kpi": overlays.get(n.id)} for n in proc.nodes}
    return {"id": proc.id, "name": proc.name, "description": proc.description, "xml": to_xml(proc),
            "steps": steps}


def write_bpmn(processes: list[Process], out_dir) -> list:
    from pathlib import Path

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for proc in processes:
        path = out_dir / f"{proc.id}.bpmn"
        path.write_text(to_xml(proc), encoding="utf-8")
        paths.append(path)
    return paths
