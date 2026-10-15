"""Private native proximity-volume allocation, with bounded archive growth.

Pure bytes in/out. This module never opens an original dump or ISO for writes.
New resource IDs are appended. The complete existing payload and its padding
move together by whole pages, preserving all field-relative pointers. Existing
resource ID references remain valid because old IDs are never renumbered.
"""
from __future__ import annotations

import hashlib
import math
import struct

from event_graph import validate_archive, MAX_BYTES
from dialogue_formats import read_sections
from scene_graph import read_graph

_CUBE_SOURCE_SHA256 = "4a439e9278ee29b01961d1f43317c9e3ccec834f2ad7fcb6f2459c90c3b08c35"
_CUBE_PLATFORM = 566
_CUBE_TERN = 336
_CUBE_MIN = (-3.04296875, 15.51171875, -0.29296875)
_CUBE_MAX = (3.04296875, 21.77734375, 2.83984375)


def append_resources(data: bytes, resources: list[dict]) -> tuple[bytes, dict]:
    """Append unnamed native resources with an existing loader type.

    Every resource is {tag:<fourcc>,flags:<u32>,payload:<bytes>}. The header
    area is grown by whole pages instead of guessing that old alignment gaps
    are reusable allocation space. No new cross-resource ID fields are allowed
    in these payloads; register platform-owned ID fields separately below.
    """
    original = bytes(data)
    old = validate_archive(original)
    h = old.header
    if not resources or len(resources) > 100 or h[3] + len(resources) > 100000:
        raise ValueError("Unsupported new native resource count.")
    loader_tags = {original[h[6] + i * 8:h[6] + i * 8 + 4].decode("ascii") for i in range(h[5])}
    additions = []
    for resource in resources:
        tag, flags, payload = resource.get("tag"), resource.get("flags"), resource.get("payload")
        if (not isinstance(tag, str) or tag not in loader_tags or len(tag) != 4
                or type(flags) is not int or not 0 <= flags <= 0xffffffff
                or not isinstance(payload, (bytes, bytearray)) or not payload
                or len(payload) > MAX_BYTES):
            raise ValueError("New resource lacks an existing native loader or valid extent.")
        additions.append((tag.encode("ascii"), flags, bytes(payload)))
    toc_end = 64 + h[3] * 16
    if original[toc_end:toc_end + 16] != b"\0" * 16:
        raise ValueError("Native TOC terminator is not the expected zero row.")
    header_shift = ((len(additions) * 16 + 4095) // 4096) * 4096
    payload_extent = h[6] - h[4]
    payload_tail = bytearray()
    new_rows, added_ids = bytearray(), []
    cumulative = payload_extent
    for i, (tag, flags, payload) in enumerate(additions):
        added_ids.append(h[3] + i)
        padding = (-len(payload)) % 4096
        payload_tail += payload + b"\0" * padding
        cumulative += len(payload) + padding
        new_rows += struct.pack("<I4sII", len(payload), tag, flags,
                                0 if i == len(additions) - 1 else cumulative)
    if len(original) + header_shift + len(payload_tail) > MAX_BYTES:
        raise ValueError("Appended native archive would exceed bounded length.")
    result = bytearray(original[:toc_end])
    result += new_rows
    result += original[toc_end:h[4]]
    result += b"\0" * (header_shift - len(new_rows))
    result += original[h[4]:h[6]] + payload_tail + original[h[6]:]
    struct.pack_into("<I", result, 12, h[3] + len(additions))
    struct.pack_into("<I", result, 16, h[4] + header_shift)
    # The previous last row's zero cumulative END is replaced by its bounded
    # old payload extent, so the first appended resource starts at old footer.
    struct.pack_into("<I", result, 64 + (h[3] - 1) * 16 + 12, payload_extent)
    footer_shift = header_shift + len(payload_tail)
    for field in (6, 8, 10, 12, 13):
        struct.pack_into("<I", result, field * 4, h[field] + footer_shift)
    new = validate_archive(result)
    if new.names != old.names or len(new.sections) != h[3] + len(additions):
        raise ValueError("Original resource names or appended count changed.")
    for before, after in zip(old.sections, new.sections):
        if (before.index, before.tag, before.flags, before.size) != (after.index, after.tag, after.flags, after.size):
            raise ValueError("An original native resource identity changed.")
        if original[before.offset:before.offset + before.size] != result[after.offset:after.offset + after.size]:
            raise ValueError("An original native resource payload changed.")
    if result[new.header[4]:new.header[4] + payload_extent] != original[h[4]:h[6]]:
        raise ValueError("The original payload or alignment padding changed.")
    if result[new.header[6]:] != original[h[6]:]:
        raise ValueError("Original footer metadata changed during resource append.")
    if new.relocations != old.relocations:
        raise ValueError("Original payload-relative ID relocation fields changed.")
    for i, (_tag, _flags, payload) in enumerate(additions):
        section = new.sections[h[3] + i]
        if result[section.offset:section.offset + section.size] != payload:
            raise ValueError("An appended resource differs from its allocation.")
    return bytes(result), {"inputSha256": hashlib.sha256(original).hexdigest(),
                           "outputSha256": hashlib.sha256(result).hexdigest(),
                           "originalResourceCount": h[3], "newResourceCount": new.header[3],
                           "addedResourceIds": added_ids, "headerPageShift": header_shift,
                           "footerPageShift": footer_shift,
                           "originalResourceIdsPreserved": True,
                           "originalPayloadAndPaddingPreserved": True,
                           "originalFooterPreserved": True}


def add_resource_relocations(data: bytes, absolute_fields: list[int]) -> tuple[bytes, dict]:
    """Register newly appended resource-ID words in the compact native footer."""
    original = bytes(data)
    before = validate_archive(original)
    h = before.header
    if not absolute_fields or len(absolute_fields) > 200:
        raise ValueError("Unsupported new relocation count.")
    existing = {location for location, _owner, _target in before.relocations}
    new_locations = []
    for field in absolute_fields:
        if type(field) is not int or field % 4 or not h[4] <= field <= h[6] - 4:
            raise ValueError("New resource ID relocation field lies outside aligned payload.")
        owner = next((s for s in before.sections if s.offset <= field and field + 4 <= s.offset + s.size), None)
        if owner is None:
            raise ValueError("New relocation has no resource owner.")
        location = field - h[4]
        target = struct.unpack_from("<I", original, field)[0]
        if location in existing or location in new_locations or target >= len(before.sections):
            raise ValueError("New relocation is duplicate or references an invalid resource ID.")
        new_locations.append(location)
    # Retail A5770 uses binary lower_bound over this table. New NODE words
    # can precede existing words in later resources, so append-only ordering
    # leaves their local IDs unresolved during streaming.
    ordered = sorted(existing | set(new_locations))
    replacement = b"".join(struct.pack("<I", loc) for loc in ordered)
    inserted = b"\0" * (len(new_locations) * 4)
    if len(original) + len(inserted) > MAX_BYTES:
        raise ValueError("New relocation metadata would exceed bounded archive length.")
    result = bytearray(original[:h[8]] + replacement + original[h[10]:])
    struct.pack_into("<I", result, 28, h[7] + len(new_locations))
    for index in (10, 12, 13):
        struct.pack_into("<I", result, index * 4, h[index] + len(inserted))
    after = validate_archive(result)
    if after.names != before.names or not set(before.relocations).issubset(set(after.relocations)):
        raise ValueError("An original named resource or ID relocation changed.")
    if result[h[4]:h[6]] != original[h[4]:h[6]]:
        raise ValueError("Payload changed while adding its relocation metadata.")
    if result[after.header[10]:] != original[h[10]:]:
        raise ValueError("Original name/type footer bytes changed.")
    return bytes(result), {"addedRelocationLocations": new_locations,
                           "relocationLocationsSorted": True,
                           "originalRelocationCount": h[7], "newRelocationCount": after.header[7],
                           "originalPayloadPreserved": True, "originalFooterRecordsPreserved": True}


def prepare_fields(data: bytes, cube_source: bytes, fields: list[dict]) -> tuple[bytes, list[dict], dict]:
    """Append verified cube TERN copies and return new NODE specs for a recipe.

    ``cube_source`` must be the pristine supported EU W1 bank. ``fields`` are
    {id,name,minimum:[xyz],maximum:[xyz]}, all in original game world Z-up units.
    For each field, this returns <id>_transform and <id>_field NODE specs.
    Caller must add at least one output5 edge from each new field before
    exporting a playable bank; native registration depends on this link.
    Then call append_nodes with fields+logic and register_field_relocations.
    """
    source_sections = read_sections(cube_source)
    _section, source_nodes = read_graph(cube_source, source_sections, resolve_world=False)
    source = source_nodes[_CUBE_PLATFORM]
    if source["type"] != "platform" or source["name"] != "airshipShip1:domeDoor:MotionDetectorShape":
        raise ValueError("Verified native motion detector template differs.")
    record = source["recordOffset"]
    if (struct.unpack_from("<I",cube_source,record+8)[0] != 37
            or struct.unpack_from("<I",cube_source,record+28)[0] != 0
            or struct.unpack_from("<I",cube_source,record+36)[0] != _CUBE_TERN
            or list(struct.unpack_from("<37f",cube_source,source["paramsOffset"])) != [1.] + [0.] * 36):
        raise ValueError("Native cube template is not the original invisible non-solid field.")
    for field, expected in ((40,b"all"),(48,b"player"),(56,b"none")):
        length=struct.unpack_from("<I",cube_source,record+field)[0]
        text=record+field+4+struct.unpack_from("<i",cube_source,record+field+4)[0]
        if cube_source[text:text+length]!=expected:
            raise ValueError("Native motion detector qualifiers differ.")
    collision=source_sections[_CUBE_TERN]
    if collision.tag!="tern" or collision.size!=172:
        raise ValueError("Verified native cuboid collision extent differs.")
    payload=cube_source[collision.offset:collision.offset+collision.size]
    if hashlib.sha256(payload).hexdigest() != "b8253b56aabc98acf8a00d14d609e1ee1b2620ed643f48599299791a65fb7bea":
        raise ValueError("The EU native proximity cube collision template differs.")
    if not fields or len(fields)>100:
        raise ValueError("Allocate between one and 100 proximity volumes.")
    names=set()
    placements=[]
    for field in fields:
        identifier,name=field.get("id"),field.get("name")
        minimum,maximum=field.get("minimum"),field.get("maximum")
        if not isinstance(identifier,str) or not identifier or identifier in names or not isinstance(name,str):
            raise ValueError("Proximity volume identifiers must be unique.")
        names.add(identifier)
        if (not isinstance(minimum,(list,tuple)) or len(minimum)!=3
                or not isinstance(maximum,(list,tuple)) or len(maximum)!=3
                or any(not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>100000 for v in (*minimum,*maximum))
                or any(not 0.01<=b-a<=10000 for a,b in zip(minimum,maximum))):
            raise ValueError("Proximity volume world bounds are invalid or unreasonably large.")
        center=[(a+b)/2 for a,b in zip(minimum,maximum)]
        local_center=[(a+b)/2 for a,b in zip(_CUBE_MIN,_CUBE_MAX)]
        scale=[(b-a)/(d-c) for a,b,c,d in zip(minimum,maximum,_CUBE_MIN,_CUBE_MAX)]
        position=[world-local*s for world,local,s in zip(center,local_center,scale)]
        recovered_min=[p+a*s for p,a,s in zip(position,_CUBE_MIN,scale)]
        recovered_max=[p+a*s for p,a,s in zip(position,_CUBE_MAX,scale)]
        if any(abs(a-b)>1e-7 for a,b in zip((*minimum,*maximum),(*recovered_min,*recovered_max))):
            raise ValueError("Native cuboid transform does not recover the requested bounds.")
        placements.append({"id":identifier,"name":name,"minimum":list(minimum),"maximum":list(maximum),
                           "position":position,"scale":scale})
    rebuilt,container=append_resources(data,[dict(tag="tern",flags=collision.flags,payload=payload) for _ in fields])
    specs=[]
    for placement,resource in zip(placements,container["addedResourceIds"]):
        transform=placement["id"]+"_transform"
        platform=placement["id"]+"_field"
        specs.append(dict(id=transform,name=placement["name"]+" transform",type="transform",
                          position=placement["position"],scale=placement["scale"]))
        specs.append(dict(id=platform,name=placement["name"]+" field",type="platform",
                          parent=transform,collision_resource=resource))
        placement.update(transformId=transform,fieldId=platform,collisionResource=resource)
    return rebuilt,specs,{"container":container,"placements":placements,
                          "templateBankSha256":hashlib.sha256(cube_source).hexdigest(),"templatePlatform":_CUBE_PLATFORM,
                          "templateCollisionResource":_CUBE_TERN,"templateCollisionSha256":hashlib.sha256(payload).hexdigest(),
                          "templateLocalMinimum":list(_CUBE_MIN),"templateLocalMaximum":list(_CUBE_MAX),
                          "solid":False,"rendered":False,"requiredOutgoingContactParameter":5,
                          "fullGameTest":False}


def register_field_relocations(data: bytes, field_indices: list[int]) -> tuple[bytes, dict]:
    """Validate new non-blocking fields and register their TERN resource IDs."""
    sections=read_sections(data)
    section,nodes=read_graph(data,sections,resolve_world=False)
    fields=[]
    for index in field_indices:
        if type(index)is not int or not 0<=index<len(nodes) or nodes[index]["type"]!="platform":
            raise ValueError("Invalid newly allocated proximity field index.")
        node=nodes[index]
        record=node["recordOffset"]
        if struct.unpack_from("<I",data,record+8)[0]!=37 or list(struct.unpack_from("<37f",data,node["paramsOffset"]))!=[1.]+[0.]*36:
            raise ValueError("New proximity field must remain invisible and non-solid.")
        count=struct.unpack_from("<I",data,record+16)[0]
        links=record+20+struct.unpack_from("<i",data,record+20)[0]
        if not section.offset<=links<=links+count*24<=section.offset+section.size:
            raise ValueError("New proximity outgoing link table is out of bounds.")
        if not any(struct.unpack_from("<H",data,links+i*24+12)[0]==5 for i in range(count)):
            raise ValueError("New field needs an outgoing touch5 link for native spatial registration.")
        collision=struct.unpack_from("<I",data,record+36)[0]
        if collision>=len(sections) or sections[collision].tag!="tern":
            raise ValueError("New proximity collision reference is not native TERN.")
        fields.append(record+36)
    rebuilt,report=add_resource_relocations(data,fields)
    report.update(fieldNodeIndices=list(field_indices),nativeTouchParameter=5,
                  invisible=True,nonSolid=True,nativeSpatialRegistrationPath="global world+AC via7A890/B7FB0",
                  fullGameTest=False)
    return rebuilt,report
