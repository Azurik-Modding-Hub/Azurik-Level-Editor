"""Native streaming regression using synthetic resources, no retail bytes."""
from bisect import bisect_left
import struct

from test_level_archive import make_archive
from event_graph import validate_archive
from event_volumes import add_resource_relocations


def test_new_node_reference_is_merged_before_later_resource_relocations():
    # The original footer has an existing NODE reference and a later LEVL
    # reference. The newly authored NODE word belongs between them. A5770
    # requires this order because it lower_bounds each resource start and
    # stops at that resource's end.
    data=make_archive([
        ('node',struct.pack('<4I',0,0,1,2)),
        ('levl',struct.pack('<4I',0,1,0,0)),
        ('tern',struct.pack('<2I',0,0)),
    ],[(0,'test/node'),(1,'levels/test')],relocations=[8,20])
    before=validate_archive(data)
    field=before.sections[0].offset+12
    rebuilt,report=add_resource_relocations(data,[field])
    after=validate_archive(rebuilt)
    assert after.relocations==((8,0,1),(12,0,2),(20,1,1))
    assert set(before.relocations).issubset(after.relocations)
    assert after.names==before.names
    assert data[before.header[4]:before.header[6]]==rebuilt[after.header[4]:after.header[6]]
    assert data[before.header[10]:]==rebuilt[after.header[10]:]
    locations=[row[0]for row in after.relocations]
    # Independent native lower-bound/range selection must include the new
    # reference in NODE and keep the existing LEVL word out of that batch.
    selected=locations[bisect_left(locations,0):bisect_left(locations,16)]
    assert selected==[8,12]
    assert report['relocationLocationsSorted'] is True


def test_batch_fields_are_sorted_independent_of_authoring_order():
    data=make_archive([
        ('node',struct.pack('<6I',0,1,2,1,2,0)),
        ('levl',struct.pack('<2I',0,1)),
        ('tern',struct.pack('<2I',0,0)),
    ],[(1,'levels/test')],relocations=[4,28])
    before=validate_archive(data)
    fields=[before.header[4]+p for p in [16,8,12]]
    result,_=add_resource_relocations(data,fields)
    after=validate_archive(result)
    assert [location for location,_,_ in after.relocations]==[4,8,12,16,28]
    assert set(before.relocations).issubset(after.relocations)
