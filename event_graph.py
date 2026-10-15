"""Private, bounded EU retail graph appender; pure bytes in/out, no file writes.

New records, arrays and strings are allocated after the old resource extent.
    The only old NODE words changed are count/table and selected outgoing-list
count/table pairs. Old node IDs, records, parameter bytes and edges survive.
NDBG names receive the same append-only treatment. The compact native footer
is moved by whole pages; its resource-ID relocation *locations* are adjusted.
No game executable or game data is distributed with this research utility.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import math
import re
import struct
from dialogue_formats import read_sections, resource_names
from level_archive import _parse_archive
from scene_graph import read_graph

MAX_BYTES = 512 * 1024 * 1024
MAX_NODES = 50_000
MAX_LINKS = 20_000
_KEY = re.compile(r"loc/english/popups/(?:demo/)?[A-Za-z0-9_]+\Z")
_STATE_KEYS = {"water level", "fire level", "air level", "earth level",
               "emeralds", "diamonds", "rubies", "sapphires", "day night timer"}


def _u32(data, at):
    return struct.unpack_from("<I", data, at)[0]


def _relative(data, field):
    return field + struct.unpack_from("<i", data, field)[0]


def _bound(data, at, size, lo=0, hi=None):
    hi = len(data) if hi is None else hi
    if type(at) is not int or type(size) is not int or size < 0 or at < lo or at + size > hi:
        raise ValueError("Native graph pointer exceeds its allocation.")
    return at


def _set_relative(data, field, target):
    distance = target - field
    if not -(1 << 31) <= distance < (1 << 31):
        raise ValueError("Self-relative pointer exceeds signed 32-bit range.")
    struct.pack_into("<i", data, field, distance)


@dataclass(frozen=True)
class Footer:
    header: tuple
    sections: tuple
    relocations: tuple  # (payload-relative location, owner resource, resource ID)
    names: dict


def validate_archive(data: bytes) -> Footer:
    """Verify exact supported compact layout, including every relocation field."""
    if not isinstance(data, (bytes, bytearray)) or not 64 <= len(data) <= MAX_BYTES:
        raise ValueError("Unsupported native archive length.")
    parsed = _parse_archive(data)
    h, sections = parsed.header, parsed.resources
    if h[2] or h[9] or h[15]:
        raise ValueError("Unknown archive flags/type-record footer is unsupported.")
    loaders = [struct.unpack_from("<4sI", data, h[6] + i * 8) for i in range(h[5])]
    tags = [tag.decode("ascii") for tag, _flags in loaders]
    if len(set(tags)) != len(tags) or any(s.tag not in tags for s in sections):
        raise ValueError("Archive loader table contains missing or duplicate types.")
    starts = [s.offset for s in sections]
    seen, relocations, previous_location = set(), [], -1
    for i in range(h[7]):
        location = _u32(data, h[8] + i * 4)
        if location <= previous_location:
            raise ValueError("Les références des ressources ne sont pas dans l’ordre requis par le jeu. Recréez le mod depuis le dump original.")
        previous_location = location
        absolute = h[4] + location
        owner = bisect_right(starts, absolute) - 1
        if location in seen or location % 4 or owner < 0:
            raise ValueError("Duplicate, unaligned or unowned native relocation.")
        s = sections[owner]
        _bound(data, absolute, 4, s.offset, s.offset + s.size)
        target = _u32(data, absolute)
        if target >= len(sections):
            raise ValueError("Native relocation contains an invalid resource ID.")
        seen.add(location)
        relocations.append((location, owner, target))
    return Footer(h, tuple(sections), tuple(relocations), resource_names(data))


def resize_resource(data: bytes, index: int, payload: bytes) -> tuple[bytes, dict]:
    """Grow one complete resource without editing any other resource bytes."""
    old = validate_archive(data)
    if type(index) is not int or not 0 <= index < len(old.sections):
        raise ValueError("Invalid resource ID.")
    section = old.sections[index]
    if not isinstance(payload, (bytes, bytearray)) or len(payload) < section.size:
        raise ValueError("This appender supports resource growth only.")
    delta = len(payload) - section.size
    shift = (delta + 4095) // 4096 * 4096
    if len(data) + shift > MAX_BYTES:
        raise ValueError("Resulting archive exceeds bounded length.")
    result = bytearray(data[:section.offset])
    result += payload + b"\0" * (shift - delta)
    result += data[section.offset + section.size:]
    struct.pack_into("<I", result, 64 + index * 16, len(payload))
    for row in range(index, len(old.sections)):
        at = 64 + row * 16 + 12
        end = _u32(data, at)
        if end:
            struct.pack_into("<I", result, at, end + shift)
    for field in (6, 8, 10, 12, 13):
        struct.pack_into("<I", result, field * 4, old.header[field] + shift)
    for row, (location, owner, _target) in enumerate(old.relocations):
        struct.pack_into("<I", result, old.header[8] + shift + row * 4,
                         location + (shift if owner > index else 0))
    new = validate_archive(result)
    if new.names != old.names or len(new.sections) != len(old.sections):
        raise ValueError("Native resource names or IDs changed.")
    for before, after in zip(old.sections, new.sections):
        if (before.index, before.tag, before.flags) != (after.index, after.tag, after.flags):
            raise ValueError("Native resource identity changed.")
        if before.index != index and data[before.offset:before.offset + before.size] != result[after.offset:after.offset + after.size]:
            raise ValueError("An unselected resource payload changed.")
    if [(owner, target) for _loc, owner, target in old.relocations] != [(owner, target) for _loc, owner, target in new.relocations]:
        raise ValueError("A native relocation target or owner changed.")
    # Loader table and complete remaining metadata remain byte-identical;
    # relocation location words are the only footer contents that may differ.
    if data[old.header[6]:old.header[8]] != result[new.header[6]:new.header[8]] or data[old.header[10]:] != result[new.header[10]:]:
        raise ValueError("Native footer metadata changed unexpectedly.")
    return bytes(result), {"resourceIndex": index, "oldSize": section.size,
                           "newSize": len(payload), "pageShift": shift,
                           "relocationCount": len(old.relocations)}


def _append(buffer, raw, alignment=4):
    buffer += b"\0" * ((-len(buffer)) % alignment)
    at = len(buffer)
    buffer += raw
    return at


def _graph_records(data, section):
    base, stop = section.offset, section.offset + section.size
    count = _u32(data, base)
    if not 1 <= count <= MAX_NODES:
        raise ValueError("Invalid original graph node count.")
    table = _bound(data, _relative(data, base + 4), count * 4, base, stop)
    records = []
    for i in range(count):
        record = _bound(data, _relative(data, table + i * 4), 32, base, stop)
        length, param_count, link_count = (_u32(data, record + k) for k in (0, 8, 16))
        if not 1 <= length <= 80 or param_count > 20000 or link_count > MAX_LINKS:
            raise ValueError("Invalid native node record counts.")
        kind_at = _bound(data, _relative(data, record + 4), length, base, stop)
        kind = data[kind_at:kind_at + length].decode("ascii")
        if not re.fullmatch(r"[A-Za-z0-9_]+", kind):
            raise ValueError("Unsupported native node typename.")
        params = _bound(data, _relative(data, record + 12), param_count * 4, base, stop)
        links = _bound(data, _relative(data, record + 20), link_count * 24, base, stop)
        raw_links = data[links:links + link_count * 24]
        for at in range(links, links + link_count * 24, 24):
            source_param, destination_param = struct.unpack_from("<H2xH", data, at + 12)
            target = _u32(data, at + 20)
            if source_param >= param_count or target >= count:
                raise ValueError("Existing edge exceeds native graph bounds.")
            curve = struct.unpack_from("<i", data, at + 4)[0]
            if curve != 1:
                _validate_curve(data, at + 4 + curve, base, stop)
            # Destination count is checked after every node record is read.
        records.append({"record": record - base, "kind": kind, "paramCount": param_count,
                        "params": params - base, "linkCount": link_count,
                        "links": links - base, "rawLinks": bytes(raw_links)})
    for source in records:
        for at in range(0, len(source["rawLinks"]), 24):
            target = _u32(source["rawLinks"], at + 20)
            target_param = struct.unpack_from("<H", source["rawLinks"], at + 16)[0]
            if target_param >= records[target]["paramCount"]:
                raise ValueError("Existing edge destination parameter is invalid.")
    return records


def _validate_curve(data, at, lo=0, hi=None):
    """Retail D8AE0 curve: flags, key count, field-relative keyptr; 16B/key."""
    hi = len(data) if hi is None else hi
    _bound(data, at, 12, lo, hi)
    count = _u32(data, at + 4)
    if count > 20000:
        raise ValueError("Curve key count exceeds native bounds.")
    points = _bound(data, _relative(data, at + 8), count * 16, lo, hi)
    # Retail curves intentionally use +infinity tangent words as interpolation
    # sentinels. Only key time/value must be finite; preserve tangents verbatim.
    for row in range(count):
        values = struct.unpack_from("<4f", data, points + row * 16)
        if not all(math.isfinite(v) for v in values[:2]) or any(math.isnan(v) for v in values[2:]):
            raise ValueError("Native curve contains invalid time/value/tangent keys.")
    return at


def _same_edges(before, after):
    """Original edges may move; compare destinations/weights and curve targets."""
    if len(after["rawLinks"]) < len(before["rawLinks"]):
        return False
    for i in range(0, len(before["rawLinks"]), 24):
        a, b = before["rawLinks"][i:i+24], after["rawLinks"][i:i+24]
        if a[:4] != b[:4] or a[8:] != b[8:]:
            return False
        ca, cb = struct.unpack_from("<i", a, 4)[0], struct.unpack_from("<i", b, 4)[0]
        if (ca == 1) != (cb == 1):
            return False
        if ca != 1 and before["links"] + i + 4 + ca != after["links"] + i + 4 + cb:
            return False
    return True


def _name_payload(data, section, names):
    base, stop = section.offset, section.offset + section.size
    _bound(data, base, 24, base, stop)
    old_count = _u32(data, base + 8)
    table = _bound(data, _relative(data, base + 12), old_count * 4, base, stop)
    old_records = []
    for i in range(old_count):
        record = _bound(data, _relative(data, table + i * 4), 8, base, stop)
        length = _u32(data, record)
        text = _bound(data, _relative(data, record + 4), length, base, stop)
        if length > 400:
            raise ValueError("Oversized NDBG name.")
        old_records.append(record - base)
    result = bytearray(data[base:stop])
    new_records = []
    for name in names:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_: .-]{1,120}", name):
            raise ValueError("New debug names must be short readable ASCII.")
        raw = name.encode("ascii")
        record = _append(result, struct.pack("<2I", len(raw), 0))
        text = _append(result, raw + b"\0", 1)
        _set_relative(result, record + 4, text)
        new_records.append(record)
    table_at = _append(result, b"\0" * ((old_count + len(names)) * 4))
    for i, target in enumerate(old_records + new_records):
        _set_relative(result, table_at + i * 4, target)
    struct.pack_into("<I", result, 8, old_count + len(names))
    _set_relative(result, 12, table_at)
    _verify_unchanged_prefix(data[base:stop], result, {8, 9, 10, 11, 12, 13, 14, 15})
    return bytes(result), old_count


def _verify_unchanged_prefix(old, new, changed):
    if len(new) < len(old):
        raise ValueError("Original resource extent was lost.")
    if any(a != b and i not in changed for i, (a, b) in enumerate(zip(old, new))):
        raise ValueError("An unapproved original resource byte changed.")


def append_nodes(data: bytes, new_nodes: list[dict], edges: list[dict], *, node_index=None) -> tuple[bytes, dict]:
    """Append helpnode/combiner records and explicit new links.

    Node specs: {id, name, type:'helpnode', popup_base} or
    {id,name,type:'combiner',input_count,operation,params:[18 floats]} or
    {id,name,type:'gameState',state_key:<verified key>,params:[10 floats]}.
    Proximity-only native schemas also accept root transform(position,scale)
    and invisible platform(parent:new_transform_id,collision_resource:index).
    Edge specs: {source:old_node_index|new_id, source_parameter:int,
    target:old_node_index|new_id, target_parameter:int, weight:1.,
    curve:1, factor:1., curve_offset:<optional original absolute curve>}.
    Existing outgoing lists remain a semantic prefix; field-relative curve
    pointers are rebased. Existing bounded native curves may be reused.
    """
    original = bytes(data)
    footer = validate_archive(original)
    sections = read_sections(original)
    section, old_nodes = read_graph(original, sections, node_index=node_index, resolve_world=False)
    records = _graph_records(original, section)
    ndbg_index = _u32(original, section.offset + 28)
    if ndbg_index >= len(sections) or sections[ndbg_index].tag != "ndbg":
        raise ValueError("Main graph has no valid NDBG resource binding.")
    debug = sections[ndbg_index]
    if not new_nodes or len(new_nodes) > 200 or len(old_nodes) + len(new_nodes) > MAX_NODES:
        raise ValueError("Append between one and 200 nodes within the bounded graph count.")
    ids, specs, param_counts = {}, [], [record["paramCount"] for record in records]
    for spec in new_nodes:
        if not isinstance(spec, dict) or not isinstance(spec.get("id"), str) or spec["id"] in ids:
            raise ValueError("New node IDs must be unique string keys.")
        if spec.get("type") not in ("helpnode", "combiner", "gameState", "transform", "platform"):
            raise ValueError("Unsupported native graph allocation class.")
        ids[spec["id"]] = len(records) + len(specs)
        param_counts.append({"helpnode": 1, "combiner": 18, "gameState": 10, "transform": 10, "platform": 37}[spec["type"]])
        specs.append(spec)
    result = bytearray(original[section.offset:section.offset + section.size])
    new_records = []
    for spec in specs:
        kind = spec["type"]
        record_size = {"helpnode":32,"combiner":32,"gameState":104,"transform":120,"platform":72}[kind]
        record = _append(result, b"\0" * record_size)
        kind_at = _append(result, kind.encode("ascii") + b"\0", 1)
        struct.pack_into("<II", result, record, len(kind), 0)
        _set_relative(result, record + 4, kind_at)
        parameter_count = {"helpnode": 1, "combiner": 18, "gameState": 10, "transform":10,"platform":37}[kind]
        if kind == "transform":
            position, scale = spec.get("position"), spec.get("scale")
            if (not isinstance(position,(list,tuple)) or len(position)!=3
                    or not isinstance(scale,(list,tuple)) or len(scale)!=3
                    or any(not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>100000 for v in position)
                    or any(not isinstance(v,(int,float)) or not math.isfinite(v) or not 0.0001<=v<=10000 for v in scale)):
                raise ValueError("Invalid root proximity transform position or positive scale.")
            params = [1.,*position,0.,0.,0.,*scale]
        elif kind == "platform":
            params = [1.] + [0.] * 36
        else:
            params = [0.] if kind == "helpnode" else spec.get("params", [0.] * parameter_count)
        if (not isinstance(params, (list, tuple)) or len(params) != parameter_count
                or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in params)):
            raise ValueError("Invalid appended node parameter block.")
        params_at = _append(result, struct.pack("<" + str(len(params)) + "f", *params))
        struct.pack_into("<I", result, record + 8, len(params))
        _set_relative(result, record + 12, params_at)
        # A bounded zero-length outgoing list pointer is still authored.
        _set_relative(result, record + 20, params_at)
        if kind == "helpnode":
            key = spec.get("popup_base")
            if not isinstance(key, str) or not _KEY.fullmatch(key) or len(key) > 120:
                raise ValueError("Help popup base must be an existing English-authored popup key.")
            raw = key.encode("ascii")
            key_at = _append(result, raw + b"\0", 1)
            struct.pack_into("<I", result, record + 24, len(raw))
            _set_relative(result, record + 28, key_at)
        elif kind == "combiner":
            count, operation = spec.get("input_count"), spec.get("operation")
            if type(count) is not int or not 1 <= count <= 16 or type(operation) is not int or not 1 <= operation <= 15:
                raise ValueError("Combiner inputs/operation exceed verified native enum bounds.")
            struct.pack_into("<II", result, record + 24, count, operation)
        elif kind == "gameState":
            state_key = spec.get("state_key")
            if state_key not in _STATE_KEYS:
                raise ValueError("Unverified native game state key.")
            for slot in range(10):
                raw = state_key.encode("ascii") if slot == 0 else b""
                text = _append(result, raw + b"\0", 1)
                field = record + 24 + slot * 8
                struct.pack_into("<I", result, field, len(raw))
                _set_relative(result, field + 4, text)
        elif kind == "transform":
            # Retail67F30 accepts parent-1, zero pivots, identity orientation
            # and rotationorder0. No inherited motion/animation association.
            struct.pack_into("<I", result, record+24, 0xffffffff)
            empty_string = _append(result,b"\0",1)
            _set_relative(result,record+36,empty_string)
            struct.pack_into("<4f",result,record+92,0.,0.,0.,1.)
            # Serialized +112/+116 compiler hash entries are not read by the
            # native loader67F30. New unanimated transforms have zero entries.
            empty_hashes = _append(result,b"\0"*4)
            _set_relative(result,record+116,empty_hashes)
        else:  # an invisible, non-solid, player-only native field platform
            parent = spec.get("parent")
            parent_id = ids.get(parent) if isinstance(parent,str) else parent
            if type(parent_id) is not int or not len(records)<=parent_id<len(param_counts) or specs[parent_id-len(records)]["type"]!="transform":
                raise ValueError("Proximity platform must own a newly allocated root transform.")
            collision = spec.get("collision_resource")
            if type(collision) is not int or not 0<=collision<len(sections) or sections[collision].tag!="tern":
                raise ValueError("Proximity collision must reference a bounded native TERN resource.")
            struct.pack_into("<I",result,record+24,parent_id)
            empty_wrappers = _append(result,b"\0"*4)
            _set_relative(result,record+32,empty_wrappers)
            struct.pack_into("<I",result,record+36,collision)
            for field,qualifier in ((40,b"all"),(48,b"player"),(56,b"none")):
                text=_append(result,qualifier+b"\0",1)
                struct.pack_into("<I",result,record+field,len(qualifier))
                _set_relative(result,record+field+4,text)
        new_records.append(record)
    def resolve(value):
        index = ids.get(value) if isinstance(value, str) else value
        if type(index) is not int or not 0 <= index < len(param_counts):
            raise ValueError("New edge references an absent graph node.")
        return index
    added_edges = defaultdict(list)
    for edge in edges:
        source, target = resolve(edge["source"]), resolve(edge["target"])
        sp, tp = edge["source_parameter"], edge.get("target_parameter", 0)
        if type(sp) is not int or not 0 <= sp < param_counts[source] or type(tp) is not int or not 0 <= tp < param_counts[target]:
            raise ValueError("New edge parameter index is invalid.")
        weight, factor, curve = edge.get("weight", 1.), edge.get("factor", 1.), edge.get("curve", 1)
        curve_offset = edge.get("curve_offset")
        if curve != 1 or any(not isinstance(v, (float, int)) or not math.isfinite(v) for v in (weight, factor)):
            raise ValueError("New links require finite values and sentinel1 or explicit validated curve_offset.")
        if curve_offset is not None:
            _validate_curve(original, curve_offset, section.offset, section.offset + section.size)
        curve_keys = edge.get("curve_keys")
        curve_target = None if curve_offset is None else curve_offset - section.offset
        if curve_keys is not None:
            if curve_offset is not None or not isinstance(curve_keys, list) or not 1 <= len(curve_keys) <= 100:
                raise ValueError("Invalid standalone native condition curve.")
            points = bytearray()
            previous = -math.inf
            for key in curve_keys:
                if not isinstance(key, (list, tuple)) or len(key) != 4:
                    raise ValueError("Native curve keys require four values.")
                if any(not isinstance(v, (float, int)) for v in key) or not all(math.isfinite(v) for v in key[:2]) or any(math.isnan(v) for v in key[2:]) or key[0] < previous:
                    raise ValueError("Invalid native curve key values.")
                previous = key[0]
                points += struct.pack("<4f", *key)
            flags = edge.get("curve_flags", 0)
            if type(flags) is not int or not 0 <= flags <= 0xffffffff:
                raise ValueError("Native curve flags require a u32.")
            curve_target = _append(result, struct.pack("<3I", flags, len(curve_keys), 4) + points)
        added_edges[source].append((struct.pack("<fIfHHHHI", weight, 1, factor, sp, 0, tp, 0, target),
                                    curve_target))
    allowed = set(range(0, 8))
    all_records = [record["record"] for record in records] + new_records
    for source, appended in added_edges.items():
        record = all_records[source]
        old_links = records[source]["rawLinks"] if source < len(records) else b""
        count = len(old_links) // 24 + len(appended)
        if count > MAX_LINKS:
            raise ValueError("Appended outgoing list exceeds bounded native length.")
        links = _append(result, old_links + b"".join(raw for raw, _curve in appended))
        if source < len(records):
            old_at = records[source]["links"]
            for i in range(0, len(old_links), 24):
                curve = struct.unpack_from("<i", old_links, i + 4)[0]
                if curve != 1:
                    _set_relative(result, links + i + 4, old_at + i + 4 + curve)
        for i, (_raw, curve_target) in enumerate(appended):
            if curve_target is not None:
                _set_relative(result, links + len(old_links) + i * 24 + 4, curve_target)
        struct.pack_into("<I", result, record + 16, count)
        _set_relative(result, record + 20, links)
        if source < len(records):
            allowed.update(range(record + 16, record + 24))
    table = _append(result, b"\0" * (len(all_records) * 4))
    for i, target in enumerate(all_records):
        _set_relative(result, table + i * 4, target)
    struct.pack_into("<I", result, 0, len(all_records))
    _set_relative(result, 4, table)
    _verify_unchanged_prefix(original[section.offset:section.offset + section.size], result, allowed)
    debug_payload, debug_count = _name_payload(original, debug, [s["name"] for s in specs])
    if debug_count != len(records):
        raise ValueError("NDBG names count does not match the original graph.")
    rebuilt, shifts = original, []
    for index, payload in sorted(((section.index, bytes(result)), (debug.index, debug_payload))):
        rebuilt, report = resize_resource(rebuilt, index, payload)
        shifts.append(report)
    final_sections = read_sections(rebuilt)
    final_section, final_nodes = read_graph(rebuilt, final_sections, node_index=section.index, resolve_world=False)
    final_records = _graph_records(rebuilt, final_section)
    if len(final_records) != len(all_records):
        raise ValueError("Final graph lost appended nodes.")
    for index, (before, after) in enumerate(zip(records, final_records)):
        if before["kind"] != after["kind"] or before["paramCount"] != after["paramCount"]:
            raise ValueError("An original node type or parameter count changed.")
        old_params = original[section.offset + before["params"]:section.offset + before["params"] + before["paramCount"] * 4]
        new_params = rebuilt[final_section.offset + after["params"]:final_section.offset + after["params"] + after["paramCount"] * 4]
        if old_params != new_params or not _same_edges(before, after):
            raise ValueError("An original parameter or outgoing edge changed.")
        if final_nodes[index]["name"] != old_nodes[index]["name"]:
            raise ValueError("An original node debug name changed.")
    for spec in specs:
        index = ids[spec["id"]]
        if final_nodes[index]["name"] != spec["name"] or final_nodes[index]["type"] != spec["type"]:
            raise ValueError("Final appended node identity is incorrect.")
    report = {"inputSha256": hashlib.sha256(original).hexdigest(),
              "outputSha256": hashlib.sha256(rebuilt).hexdigest(),
              "nodeSection": section.index, "debugSection": debug.index,
              "originalNodeCount": len(records), "newNodeCount": len(all_records),
              "appendedNodeIds": ids, "appendedEdgeCount": len(edges),
              "editedExistingSources": sorted(i for i in added_edges if i < len(records)),
              "resources": shifts, "originalNodeParametersPreserved": True,
              "originalEdgesPreserved": True, "originalLinkCurveTargetsPreserved": True,
              "originalDebugNamesPreserved": True,
              "unchangedResourcePayloadsPreserved": True,
              "resourceRelocationTargetsPreserved": True,
              "nativePersistedNodeCount": _u32(original, section.offset + 12),
              "nativePublicExportCount": _u32(original, section.offset + 20),
              "newNodesAddedToPersistenceOrPublicExports": False,
              "runtimeTestPerformedByWriter": False}
    return rebuilt, report


def append_helpnodes(data: bytes, bindings: list[dict], *, node_index=None):
    """Convenience API: retain an existing event and attach a new native help."""
    specs, edges = [], []
    for i, binding in enumerate(bindings):
        key = binding.get("id", f"new_help_{i}")
        specs.append({"id": key, "type": "helpnode", "name": binding["name"],
                      "popup_base": binding["popup_base"]})
        edges.append({"source": binding["source_node"], "source_parameter": binding["source_parameter"],
                      "target": key, "target_parameter": 0,
                      "weight": binding.get("weight", 1.), "factor": binding.get("factor", 1.)})
    return append_nodes(data, specs, edges, node_index=node_index)
