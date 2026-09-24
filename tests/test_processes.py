import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import pytest

from steel_crm.processes.bpmn import TASKS, Layout, to_xml
from steel_crm.processes.definitions import all_processes

MODEL = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
DI = "{http://www.omg.org/spec/BPMN/20100524/DI}"
DOCS = Path(__file__).resolve().parents[1] / "docs" / "bpmn"
PROCESSES = all_processes()


@pytest.mark.parametrize("proc", PROCESSES, ids=lambda p: p.id)
def test_process_is_well_formed_bpmn(proc):
    root = ET.fromstring(to_xml(proc))
    process = root.find(f"{MODEL}process")
    skip = {"laneSet", "sequenceFlow", "textAnnotation", "association", "documentation"}
    nodes = {el.get("id"): el.tag.replace(MODEL, "") for el in process if el.tag.replace(MODEL, "") not in skip}
    flows = [(f.get("sourceRef"), f.get("targetRef")) for f in process.findall(f"{MODEL}sequenceFlow")]
    assert all(s in nodes and t in nodes for s, t in flows)

    # every node sits in one lane and is drawn; every flow has an edge
    in_lanes = [ref.text for ref in process.iter(f"{MODEL}flowNodeRef")]
    assert sorted(in_lanes) == sorted(nodes)
    drawn = {s.get("bpmnElement") for s in root.iter(f"{DI}BPMNShape")}
    assert set(nodes) <= drawn
    edges = {e.get("bpmnElement") for e in root.iter(f"{DI}BPMNEdge")}
    assert {f.get("id") for f in process.findall(f"{MODEL}sequenceFlow")} <= edges

    # starts and ends: every step can be reached, and every path stops at an end event
    out = defaultdict(list)
    for s, t in flows:
        out[s].append(t)
    attached = {el.get("id"): el.get("attachedToRef") for el in process.findall(f"{MODEL}boundaryEvent")}
    for boundary, task in attached.items():
        out[task].append(boundary)
    seen, todo = set(), [n for n, kind in nodes.items() if kind == "startEvent"]
    while todo:
        n = todo.pop()
        if n not in seen:
            seen.add(n)
            todo += out[n]
    assert seen == set(nodes)
    for n, kind in nodes.items():
        leaves = [t for s, t in flows if s == n]
        assert (not leaves) == (kind == "endEvent"), n

    # message flows cross between pools
    pools = {p.get("id") for p in root.iter(f"{MODEL}participant")}
    for m in root.iter(f"{MODEL}messageFlow"):
        assert (m.get("sourceRef") in pools) != (m.get("targetRef") in pools)


@pytest.mark.parametrize("proc", PROCESSES, ids=lambda p: p.id)
def test_shapes_sit_in_their_lane_without_overlapping(proc):
    lay = Layout(proc)
    boxes = []
    for n in proc.nodes:
        if n.attached_to:
            continue
        x, y, w, h = lay.shapes[n.id]
        lx, ly, lw, lh = lay.lanes[n.lane]
        assert lx <= x and x + w <= lx + lw and ly <= y and y + h <= ly + lh, n.id
        if n.type in TASKS:
            boxes.append((n.id, x, y, w, h))
    for i, (a, ax, ay, aw, ah) in enumerate(boxes):
        for b, bx, by, bw, bh in boxes[i + 1:]:
            assert ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay, (a, b)


def test_committed_bpmn_files_match_the_definitions():
    for proc in PROCESSES:
        path = DOCS / f"{proc.id}.bpmn"
        assert path.read_text(encoding="utf-8") == to_xml(proc), f"run `python -m steel_crm.cli bpmn` ({path.name})"
