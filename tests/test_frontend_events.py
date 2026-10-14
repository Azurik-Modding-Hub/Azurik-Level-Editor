"""Functional event-authoring UI contracts without copyrighted game resources."""
from test_frontend_renderer import run_node


SETUP = r"""
import assert from 'node:assert/strict';
import {createEventManager,validateEventDraft,parseEventImport,createEventZone} from './web/events.mjs';
class Element {
 constructor(tag,doc){this.tagName=tag;this.ownerDocument=doc;this.children=[];this.listeners={};this.attributes={};this.value='';this.textContent='';this.disabled=false;this.open=true;this.isConnected=true;}
 append(...children){for(const child of children){child.parentElement=this;this.children.push(child);}}
 replaceChildren(...children){this.children=[];this.append(...children);}
 setAttribute(key,value){this.attributes[key]=value;}
 addEventListener(type,callback){(this.listeners[type]??=[]).push(callback);}
 closest(tag){for(let node=this;node;node=node.parentElement)if(node.tagName===tag)return node;return null;}
 async emit(type){await Promise.all((this.listeners[type]||[]).map(callback=>callback({preventDefault(){}})));}
 async click(){if(!this.disabled)await this.emit('click');}
}
const doc={createElement(tag){return new Element(tag,doc);}},dialog=doc.createElement('dialog'),body=doc.createElement('div');dialog.append(body);
const calls=[],changes=[],loaded=[],selected=[],previews=[],toasts=[];let closed=0,context={level:'w1',position:[12,-8,3]};
let catalogue={events:[],catalog:[{base:'loc/english/popups/demo/demo_ice',label:'Ice barrier',pages:2,language:'en',voiceAvailable:true},{base:'e6daylight',label:'Mirrors in daylight',pages:1,voiceAvailable:false}]};
let handler=async(path,payload)=>payload===undefined?catalogue:{id:'event-new',pendingCount:1,history:{canUndo:true,canRedo:false}};
const api=async(path,payload,options)=>{calls.push({path,payload,options});return handler(path,payload,options);};
const manager=createEventManager({api,getContext:()=>context,openDialog(){dialog.open=true;body.replaceChildren();return body;},closeDialog(){closed++;dialog.open=false;},onLoaded:(events,data)=>loaded.push({events,data}),onChanged:result=>changes.push(result),onSelect:(id,frame)=>selected.push([id,frame]),onPreview:entry=>previews.push(entry),toast:(text,error)=>toasts.push({text,error})});
const nodes=(root=body)=>[root,...root.children.flatMap(child=>nodes(child))];
const field=id=>nodes().find(element=>element.id===id),button=text=>nodes().find(element=>element.tagName==='button'&&element.textContent===text);
const submit=()=>nodes().find(element=>element.tagName==='form').emit('submit');
const allText=()=>nodes().map(element=>element.textContent).join('\n');
const settle=async()=>{for(let i=0;i<24;i++)await Promise.resolve();};
const draft={level:'w1',name:'Barrier warning',popupBase:'demo/demo_ice',position:[1,2,3],size:[4,5,6],condition:'any',once:true};
"""


def test_event_draft_rejects_nonfinite_bounds_and_unsupported_modes():
    run_node(SETUP + r"""
assert.deepEqual(validateEventDraft(draft),draft);
for(const change of [{level:'../w1'},{name:''},{name:'a\nname'},{popupBase:'../test'},{popupBase:'/root'},{condition:'button'},{once:1},{position:[1,Infinity,3]},{position:[1,2]},{position:['1',2,3]},{position:[50000,0,0]},{size:[0,2,3]},{size:[2,1001,3]},{activeNode:-1},{activeNode:50000},{activeNode:1.5}])assert.throws(()=>validateEventDraft({...draft,...change}));
assert.equal(validateEventDraft({...draft,popupBase:'loc/english/popups/e6daylight'}).popupBase,'loc/english/popups/e6daylight');
assert.equal(validateEventDraft({...draft,text:'  Hello\r\nWorld  '},true).text,'Hello\nWorld');
for(const text of ['','a'.repeat(1001),'test\0'])assert.throws(()=>validateEventDraft({...draft,text},true));
assert.deepEqual(parseEventImport(JSON.stringify({format:'azurik-level-events',version:1,events:[{...draft,id:'old-id',locked:true}]})).events,[{...draft,locked:true}]);
assert.equal(parseEventImport(JSON.stringify({format:'azurik-level-events',version:1,events:[{...draft,activeNode:19}]})).events[0].activeNode,19);
assert.throws(()=>parseEventImport(JSON.stringify({format:'azurik-level-events',version:1,events:Array.from({length:101},()=>draft)})));
assert.equal(parseEventImport('\ufeff'+JSON.stringify({format:'azurik-level-events',version:1,events:[{...draft,resourceKind:'custom',text:'Custom',popupBase:undefined}]})).events[0].text,'Custom');
for(const input of ['{',JSON.stringify({format:'other',version:1,events:[draft]}),JSON.stringify({format:'azurik-level-events',version:2,events:[draft]}),JSON.stringify({format:'azurik-level-events',version:1,events:[]})])assert.throws(()=>parseEventImport(input));
""")


def test_create_real_catalogue_zone_uses_source_coordinates_and_explicit_conditions():
    run_node(SETUP + r"""
await manager.showCreate();assert.equal(field('eventPositionX').value,'12');assert.equal(field('eventOnce').value,'true');
assert.ok(allText().includes('Une fois par chargement du niveau'));
assert.equal(nodes().find(element=>element.tagName==='img').src,'/api/events/popup?base=loc%2Fenglish%2Fpopups%2Fdemo%2Fdemo_ice&language=english');
field('eventPopupLanguage').value='french';await field('eventPopupLanguage').emit('change');assert.match(nodes().find(element=>element.tagName==='img').src,/language=french$/);
field('eventPopupSearch').value='mirrors';await field('eventPopupSearch').emit('input');assert.equal(field('eventPopup').children.length,1);assert.equal(field('eventPopup').value,'e6daylight');
field('eventName').value='<script>Raw name</script>';field('eventCondition').value='night';field('eventOnce').value='false';field('eventSizeX').value='6';await field('eventSizeX').emit('input');
assert.deepEqual(previews.at(-1),{position:[12,-8,3],size:[6,4,4]});await submit();
const mutation=calls.find(call=>call.path==='/api/events/add');assert.deepEqual(mutation.payload,{level:'w1',name:'<script>Raw name</script>',position:[12,-8,3],size:[6,4,4],condition:'night',once:false,popupBase:'e6daylight'});
assert.equal(mutation.options.method,'POST');assert.equal(changes.length,1);assert.equal(closed,1);assert.deepEqual(selected[0],['event-new',undefined]);assert.equal(nodes().some(node=>node.tagName==='script'),false);
""")


def test_txt_import_is_silent_custom_panel_and_invalid_inputs_never_mutate():
    run_node(SETUP + r"""
await manager.showCustom();assert.match(allText(),/Aucune voix n’est créée automatiquement/);
const buffer=new TextEncoder().encode('\ufeffMy imported dialogue').buffer;
field('eventTextFile').files=[{size:buffer.byteLength,arrayBuffer:async()=>buffer}];await field('eventTextFile').emit('change');assert.equal(field('eventText').value,'My imported dialogue');
field('eventPositionX').value='';await submit();assert.equal(calls.filter(call=>call.payload).length,0);
field('eventPositionX').value='1';await submit();const request=calls.find(call=>call.path==='/api/events/custom');assert.equal(request.payload.text,'My imported dialogue');assert.equal('popupBase' in request.payload,false);
""")


def test_patch_delete_and_locks_use_native_event_endpoint_methods():
    run_node(SETUP + r"""
catalogue.events=[{...draft,id:'event/? special',resourceKind:'existing',locked:false}];
await manager.showEdit('event/? special');field('eventName').value='Renamed';await submit();
assert.equal(calls.find(call=>call.options?.method==='PATCH').path,'/api/events/event%2F%3F%20special');
await manager.showDelete('event/? special');await button('Supprimer du projet').click();assert.equal(calls.at(-2).options.method,'DELETE');
catalogue.events[0].locked=true;await manager.showEdit('event/? special');assert.equal(button('Enregistrer cet évènement').disabled,true);assert.equal(field('eventName').disabled,true);
const before=calls.filter(call=>call.options?.method==='PATCH').length;await submit();assert.equal(calls.filter(call=>call.options?.method==='PATCH').length,before);
await button('Déverrouiller').click();const lock=calls.filter(call=>call.options?.method==='PATCH').at(-1);assert.deepEqual(lock.payload,{locked:false});
""")


def test_json_import_discards_old_ids_and_errors_and_duplicate_submit_stay_controlled():
    run_node(SETUP + r"""
await manager.showImport();field('eventImportFile').files=[{size:120,text:async()=>JSON.stringify({format:'azurik-level-events',version:1,events:[{...draft,id:'will-be-regenerated'}]})}];
let resolve;handler=async(path,payload)=>payload?new Promise(done=>resolve=done):catalogue;
const first=submit();await settle();await submit();assert.equal(calls.filter(call=>call.path==='/api/events/import').length,1);assert.equal(manager.working,true);
resolve({pendingCount:1});await first;assert.equal(manager.working,false);assert.equal(calls.find(call=>call.path==='/api/events/import').payload.events[0].id,undefined);
await manager.showCreate();handler=async()=>{throw new Error('<img src=x onerror=bad> backend error');};await submit();assert.equal(dialog.open,true);assert.match(allText(),/backend error/);assert.equal(nodes().filter(node=>node.tagName==='img').length,1);assert.equal(button('Créer cet évènement').disabled,false);
""")


def test_stale_reads_and_closed_dialog_file_reads_do_not_replace_context():
    run_node(SETUP + r"""
let resolve;handler=async()=>new Promise(done=>resolve=done);const pending=manager.refresh();context={level:'a5'};resolve(catalogue);await pending;assert.equal(loaded.length,0);
handler=async()=>catalogue;await manager.showImport();let readFile;field('eventImportFile').files=[{size:12,text:()=>new Promise(done=>readFile=done)}];const importPending=submit();await settle();dialog.open=false;readFile(JSON.stringify({format:'azurik-level-events',version:1,events:[draft]}));await importPending;assert.equal(calls.some(call=>call.path==='/api/events/import'),false);
""")


def test_event_wirebox_preserves_source_z_up_bounds_and_raycast_ids():
    run_node(SETUP + r"""
const THREE=await import('./web/vendor/three.module.js');
const object=createEventZone(THREE,{...draft,id:'event-id'});object.updateMatrixWorld(true);
const bounds=new THREE.Box3().setFromObject(object);assert.deepEqual(bounds.getCenter(new THREE.Vector3()).toArray(),draft.position);assert.deepEqual(bounds.getSize(new THREE.Vector3()).toArray(),draft.size);
assert.equal(object.children.length,2);assert.equal(object.children[0].material.depthWrite,false);object.traverse(child=>assert.equal(child.userData.id,'event-id'));
""")


def test_event_translations_cover_the_main_editor_menu_and_dialogs():
    run_node(SETUP + r"""
const {translate,setLanguage}=await import('./web/i18n.mjs');setLanguage('en');
assert.equal(translate('Évènements'),'Events');assert.equal(translate('Une fois par chargement du niveau'),'Once per level load');
""")
