"""Zone import, history, locks and source preservation with synthetic levels."""
import hashlib
import json
import pytest
from test_project_assets import AssetBackend
from test_renderer_parser import fixture_level


class EventBackend(AssetBackend):
    def _event_catalog(self):
        return [dict(base='training1',label='Training',pages=1,languages=['english'])]

    def _popup_banks(self):
        return {}


@pytest.fixture
def events(tmp_path):
    source=tmp_path/'source/gamedata';source.mkdir(parents=True)
    payload=fixture_level()
    (source/'town.xbr').write_bytes(payload);(source/'w1.xbr').write_bytes(payload)
    return EventBackend(source.parent,tmp_path/'project',tmp_path/'exports',texture_dir=tmp_path/'textures')


def draft(level='town',**changes):
    return dict(level=level,name='Zone du gardien',position=[10,20,30],size=[4,5,6],popupBase='training1',once=True,condition='any') | changes


def test_event_position_lock_delete_and_persisted_history(events):
    source=events._source_path('town').read_bytes()
    identifier=events.add_event(draft())['id']
    events.update_event(identifier,dict(level='town',position=[15,22,30]))
    assert events.events()['events'][0]['position']==[15,22,30]
    events.undo('town')
    assert events.events()['events'][0]['position']==[10,20,30]
    events.redo('town')
    events.update_event(identifier,dict(locked=True))
    with pytest.raises(ValueError,match='Déverrouillez'):events.update_event(identifier,dict(position=[0,0,0]))
    with pytest.raises(ValueError,match='Déverrouillez'):events.delete_event(identifier)
    events.update_event(identifier,dict(locked=False));events.delete_event(identifier)
    assert events.events()['events']==[]
    events.undo('town')
    reopened=EventBackend(events.source_dir,events.project_dir,events.exports_dir,texture_dir=events.texture_dir)
    assert reopened.events()['events'][0]['position']==[15,22,30]
    assert events._source_path('town').read_bytes()==source


def test_multi_level_import_is_one_atomic_undo_and_retains_native_gate(events):
    payload=dict(format='azurik-level-events',version=1,events=[draft(activeNode=1),draft('w1')])
    result=events.import_events(payload)
    assert result['imported']==2
    assert events.pending_count()==2
    assert next(e for e in events.events()['events'] if e['level']=='town')['activeNode']==1
    events.undo('w1')
    assert events.events()['events']==[]
    events.redo('town')
    assert len(events.events()['events'])==2


def test_invalid_batch_never_partially_adds_and_existing_source_is_not_written(events):
    path=events._source_path('town');before=hashlib.sha256(path.read_bytes()).digest()
    payload=dict(format='azurik-level-events',version=1,events=[draft(),draft(popupBase='missing')])
    with pytest.raises(ValueError):events.import_events(payload)
    assert events.events()['events']==[]
    assert hashlib.sha256(path.read_bytes()).digest()==before
    assert not events.project_path.exists()


def test_global_undo_orders_event_and_asset_edits_and_reset_is_reversible(events):
    identifier=events.add_event(draft())['id']
    imported=events.import_model('town','Test model',dict(positions=[0,0,0,1,0,0,0,1,0],indices=[0,1,2]),[1,2,3])['id']
    events.update_event(identifier,dict(size=[8,8,8]))
    events.undo('town')
    assert events.events()['events'][0]['size']==[4,5,6]
    events.undo('town')
    assert imported not in events._state('town')['models']
    events.redo('town')
    events.reset_level('town')
    assert events.events()['events']==[]
    events.undo('town')
    assert events.events()['events'][0]['id']==identifier
    assert imported in events._state('town')['models']


def test_stale_source_blocks_new_events_without_mutation(events):
    events.add_event(draft())
    path=events._source_path('town');path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='source'):events.add_event(draft())
    assert len(events.events()['events'])==1


def test_bulk_lock_and_unlock_also_protect_authored_zones(events):
    identifier=events.add_event(draft())['id']
    events.set_lock('town',True,all_items=True)
    assert events.events()['events'][0]['locked']
    with pytest.raises(ValueError,match='Déverrouillez'):events.update_event(identifier,dict(size=[5,5,5]))
    events.set_lock('town',False,all_items=True)
    events.update_event(identifier,dict(size=[5,5,5]))


def test_bulk_locked_zone_cannot_be_moved_by_undo(events):
    identifier=events.add_event(draft())['id']
    events.update_event(identifier,dict(position=[15,20,30]))
    events.set_lock('town',True,all_items=True)
    with pytest.raises(ValueError,match='Déverrouillez'):events.undo('town')
    assert events.events()['events'][0]['position']==[15,20,30]
    events.set_lock('town',False,all_items=True)
    events.undo('town')
    assert events.events()['events'][0]['position']==[10,20,30]
