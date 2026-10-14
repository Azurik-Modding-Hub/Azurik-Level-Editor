"""Compile authored popup areas into native retail nodes, without source writes.

Only native player proximity, optional day/night and a per-load latch are
supported. Source collision templates and popup frames are read from the user's
dump; no game resources are bundled with this module.
"""
from __future__ import annotations

import hashlib
import struct
import re
import unicodedata
from dialogue_formats import read_sections, resource_names, replace_popup_text
from event_graph import append_nodes, validate_archive, _graph_records, _relative
from event_volumes import prepare_fields, register_field_relocations, append_resources
from level_archive import _parse_archive, _write_names, named_resources
from scene_graph import read_graph


def native_night_curve(e6):
    section, nodes = read_graph(e6, read_sections(e6), resolve_world=False)
    night = next((i for i, n in enumerate(nodes) if n['name'] == 'timesComb_night'), None)
    if night is None:
        raise ValueError('La condition nocturne native de E6 est introuvable.')
    for row in _graph_records(e6, section):
        if row['kind'] != 'gameState':
            continue
        record = section.offset + row['record']
        length = struct.unpack_from('<I', e6, record + 24)[0]
        start = _relative(e6, record + 28)
        if e6[start:start+length] != b'day night timer':
            continue
        links = row['rawLinks']
        for i in range(0, len(links), 24):
            if struct.unpack_from('<I', links, i+20)[0] != night:
                continue
            curve = section.offset + row['links'] + i + 4 + struct.unpack_from('<i', links, i+4)[0]
            flags, count = struct.unpack_from('<II', e6, curve)
            at = _relative(e6, curve+8)
            return {'curve_flags': flags, 'curve_keys': [list(struct.unpack_from('<4f', e6, at+k*16)) for k in range(count)]}
    raise ValueError('La courbe nocturne du jeu est introuvable.')


def compile_event_level(data, cube_source, events, e6_source):
    if not events:
        return data, {}
    def native_name(value):
        value = unicodedata.normalize('NFKD',value).encode('ascii','ignore').decode('ascii')
        return re.sub(r'[^A-Za-z0-9_: .-]',' ',value).strip()[:90] or 'Studio event'
    fields = [dict(id=e['id'], name=native_name(e['name']), minimum=[p-s/2 for p,s in zip(e['position'],e['size'])],
                   maximum=[p+s/2 for p,s in zip(e['position'],e['size'])]) for e in events]
    data, nodes, volumes = prepare_fields(data, cube_source, fields)
    edges = []
    def edge(source, channel, target, target_channel=0, **extra):
        edges.append(dict(source=source, source_parameter=channel, target=target, target_parameter=target_channel, **extra))
    def comb(identifier, count, operation, params=None):
        nodes.append(dict(id=identifier,name='Studio '+identifier,type='combiner',input_count=count,operation=operation,
                          params=params or [0.]*18))
        return identifier
    needs_night = any(e['condition'] != 'any' for e in events)
    if needs_night:
        nodes.append(dict(id='studio_day_clock',name='Studio day night clock',type='gameState',state_key='day night timer'))
        comb('studio_night',1,4)
        edge('studio_day_clock',0,'studio_night',**native_night_curve(e6_source))
        comb('studio_day',1,13)
        edge('studio_night',17,'studio_day')
    _, originals = read_graph(data, read_sections(data), resolve_world=False)
    for e in events:
        identifier = e['id']
        source, channel = identifier+'_field', 5
        if e.get('activeNode') is not None:
            active = e['activeNode']
            if type(active) is not int or not 0 <= active < len(originals) or originals[active]['type'] != 'platform':
                raise ValueError('Le bloqueur associé à la zone a changé.')
            at = originals[active]['paramsOffset']
            solid = struct.unpack_from('<f',data,at+11*4)[0]
            params = [0.]*18
            params[1] = solid
            gate = comb(identifier+'_active',2,7,params)
            edge(source,channel,gate)
            edge(active,11,gate,1)
            source,channel = gate,17
        if e['condition'] != 'any':
            gate = comb(identifier+'_time',2,7)
            edge(source,channel,gate)
            edge('studio_'+e['condition'],17,gate,1)
            source,channel = gate,17
        if e['once']:
            latch = comb(identifier+'_once',2,4)
            edge(source,channel,latch)
            edge(latch,17,latch,1)
            source,channel = latch,17
        helper = identifier+'_help'
        nodes.append(dict(id=helper,name=native_name(e['name'])+' dialogue',type='helpnode',popup_base='loc/english/popups/'+e['popupBase']))
        edge(source,channel,helper)
    result, graph = append_nodes(data,nodes,edges)
    result, relocations = register_field_relocations(result,[graph['appendedNodeIds'][e['id']+'_field'] for e in events])
    return result, dict(volumes=volumes,graph=graph,relocations=relocations,eventCount=len(events),
                        gameplayTested=False,repeatScope='once per level load' if all(e['once'] for e in events) else 'authored')


def add_named_popup(bank, language, event):
    """Append a new SURF panel instead of replacing an existing conversation."""
    template = 'loc/'+language+'/popups/training100.tif'
    key = 'loc/'+language+'/popups/'+event['popupBase']+'00.tif'
    if key in resource_names(bank).values():
        raise ValueError('Le nom du nouveau dialogue existe déjà dans le dump.')
    edited = replace_popup_text(bank,template,event['text'],{'fontSize':14})
    sections, names = read_sections(edited), resource_names(edited)
    index = next(i for i,name in names.items() if name==template)
    section = sections[index]
    payload = edited[section.offset:section.offset+section.size]
    appended, info = append_resources(bank,[dict(tag='surf',flags=section.flags,payload=payload)])
    rows = named_resources(appended)
    rows.append(dict(key=key,tag='surf',resourceIndex=info['addedResourceIds'][0]))
    result = _write_names(appended,_parse_archive(appended),rows)
    validate_archive(result)
    # Appended SURF pointers are field-relative. No cross-resource references.
    for before, after in zip(read_sections(bank),read_sections(result)):
        if bank[before.offset:before.offset+before.size] != result[after.offset:after.offset+after.size]:
            raise ValueError('Un panneau original a changé pendant l’ajout de texte.')
    return result, dict(key=key,tag='surf',sha256=hashlib.sha256(payload).hexdigest())


def bind_mirror_key(bank, language):
    rows = named_resources(bank)
    old = 'loc/'+language+'/popups/e6mirrors00.tif'
    new = 'loc/'+language+'/popups/e6mirror00.tif'
    if any(row['key']==new for row in rows):
        return bank
    match = next((row for row in rows if row['key']==old),None)
    if match is None:
        raise ValueError('Panneau original des miroirs introuvable.')
    match['key'] = new
    return _write_names(bank,_parse_archive(bank),rows)
