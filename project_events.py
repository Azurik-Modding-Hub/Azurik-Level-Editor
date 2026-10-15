"""Portable, reversible popup zone authoring, persisted in the Studio project."""
from __future__ import annotations
import copy
import hashlib
import re
from uuid import uuid4
from dialogue_formats import scan_dialogues, render_popup_text, decode_popup_png

LANGUAGES = {'english':'english.xbr','french':'French.xbr','german':'German.xbr'}
EVENT_FIELDS = {'id','level','name','position','size','popupBase','condition','once','locked','resourceKind','text','activeNode'}


class ProjectEvents:
    def _popup_banks(self):
        cache = getattr(self,'_popup_bank_cache',{})
        self._popup_bank_cache = cache
        result = {}
        for lang,filename in LANGUAGES.items():
            path = self.gamedata_dir/filename
            if not path.is_file():
                continue
            stamp = (path.stat().st_size,path.stat().st_mtime_ns)
            if lang not in cache or cache[lang][0] != stamp:
                cache[lang] = stamp,path.read_bytes()
            result[lang] = cache[lang][1]
        return result

    def _event_catalog(self):
        banks = self._popup_banks()
        key = tuple((k,self._popup_bank_cache[k][0]) for k in banks)
        if getattr(self,'_event_catalog_key',None)==key:
            return copy.deepcopy(self._event_catalog_cache)
        grouped = {}
        for lang,bank in banks.items():
            rows = scan_dialogues(bank)
            for row in rows:
                name = row['resource'].split('/popups/',1)[1][:-4]
                match = re.fullmatch(r'(.+)(\d\d)',name)
                if not match or match[2] != '00':
                    continue
                base = match[1]
                if base=='e6mirrors': base='e6mirror'
                item = grouped.setdefault(base,dict(base=base,label=base,pages=0,language='english',languages=[],voiceAvailable=False))
                item['languages'].append(lang)
                item['voiceAvailable'] |= bool(row['voiceResource']) or base=='e6mirror'
                item['pages'] = max(item['pages'],sum(1 for r in rows if re.fullmatch(re.escape(match[1])+r'\d\d\.tif',r['resource'].split('/popups/',1)[1])))
        result = sorted(grouped.values(),key=lambda r:r['base'])
        self._event_catalog_key,self._event_catalog_cache = key,result
        return copy.deepcopy(result)

    def _validate_event(self, record, *, identifier=None):
        from editor_backend import _position
        if not isinstance(record,dict) or set(record)-EVENT_FIELDS:
            raise ValueError('Champs du déclencheur inconnus.')
        level = record.get('level')
        self._source_path(level)
        if not self._level_active(level) or level not in {r['id'] for r in self.catalog()}:
            raise ValueError('Le déclencheur doit appartenir à un niveau actif.')
        name = record.get('name','Dialogue')
        if not isinstance(name,str) or not name.strip() or len(name)>100 or any(ord(c)<32 for c in name):
            raise ValueError('Nom du déclencheur invalide.')
        position,size = _position(record.get('position',[0,0,0])),_position(record.get('size',[4,4,4]))
        if any(not 0.1<=v<=1000 for v in size):
            raise ValueError('Les dimensions doivent être comprises entre 0,1 et 1 000 unités.')
        condition,once,locked = record.get('condition','any'),record.get('once',True),record.get('locked',False)
        if condition not in ('any','day','night') or type(once)is not bool or type(locked)is not bool:
            raise ValueError('Condition ou répétition invalide.')
        identifier = identifier or record.get('id') or 'evt_'+uuid4().hex[:16]
        if not isinstance(identifier,str) or not re.fullmatch(r'evt_[a-zA-Z0-9_]{1,40}',identifier):
            raise ValueError('Identifiant du déclencheur invalide.')
        kind = record.get('resourceKind','custom' if 'text' in record else 'existing')
        base = record.get('popupBase')
        if isinstance(base,str): base=base.removeprefix('loc/english/popups/')
        result = dict(id=identifier,level=level,name=name.strip(),position=position,size=size,condition=condition,once=once,locked=locked,resourceKind=kind)
        if kind=='custom':
            text = record.get('text')
            if not isinstance(text,str) or not text.strip() or len(text)>4000:
                raise ValueError('Le fichier texte doit contenir de 1 à 4 000 caractères.')
            base = 'studio_'+identifier
            # Validate the actual game-size panel before accepting an import.
            bank = self._popup_banks().get('english')
            if bank is None: raise ValueError('La banque anglaise est nécessaire pour importer du texte.')
            render_popup_text(bank,'loc/english/popups/training100.tif',text,{'fontSize':14})
            result['text']=text
        elif kind!='existing' or base not in {r['base'] for r in self._event_catalog()}:
            raise ValueError('Choisissez un dialogue existant dans le catalogue du jeu.')
        result['popupBase']=base
        if record.get('activeNode') is not None:
            if type(record['activeNode'])is not int or not 0<=record['activeNode']<50000:
                raise ValueError('Nœud associé invalide.')
            result['activeNode']=record['activeNode']
        return result

    def events(self):
        with self._lock:
            history = self._combined_history()
            return dict(events=[copy.deepcopy(e) for level,s in self._project['levels'].items() if self._level_active(level) for e in s.get('events',{}).values()],
                        catalog=self._event_catalog(),sourceLanguages=list(self._popup_banks()),pendingCount=self.pending_count(),history=history,**history)

    def popup_preview(self, base, language='english'):
        bank=self._popup_banks().get(language)
        if bank is None: raise ValueError('Langue absente du dump.')
        if base not in {r['base'] for r in self._event_catalog()}:
            raise ValueError('Dialogue absent du catalogue.')
        if base=='e6mirror':
            from event_export import bind_mirror_key
            bank=bind_mirror_key(bank,language)
        return decode_popup_png(bank,'loc/'+language+'/popups/'+base+'00.tif')

    def _find_event(self, identifier):
        for level,state in self._project['levels'].items():
            if identifier in state.get('events',{}):
                return level,state,state['events'][identifier]
        raise ValueError('Déclencheur introuvable.')

    def _event_commit(self, before, action, identifier=None):
        # One import can span several levels, but is one atomic undo operation.
        after={level:copy.deepcopy(self._state(level)['events']) for level in before}
        level=next(iter(before))
        state=self._state(level)
        previous_redo=copy.deepcopy(state['redo'])
        if before!=after:
            state['undo'].append(dict(operation='events',action=action,id=identifier,beforeEvents=before,afterEvents=after))
            state['redo'].clear()
            try: self.save()
            except Exception:
                for key,value in before.items(): self._state(key)['events']=value
                state['undo'].pop();state['redo']=previous_redo
                raise
        result=dict(id=identifier,pendingCount=self.pending_count(),history=self._combined_history(),**self._combined_history())
        if identifier:
            try: result['event']=copy.deepcopy(self._find_event(identifier)[2])
            except ValueError: pass
        return result

    def add_event(self, payload, custom=False):
        with self._lock:
            record=dict(payload)
            record['resourceKind']='custom' if custom else 'existing'
            record.pop('id',None)
            event=self._validate_event(record)
            level=event['level'];state=self._state(level)
            self._event_source_hash(level)
            before={level:copy.deepcopy(state['events'])}
            if len(state['events'])>=100: raise ValueError('Maximum 100 déclencheurs par niveau.')
            state['events'][event['id']]=event
            return self._event_commit(before,'add-event',event['id'])

    def update_event(self, identifier, payload):
        with self._lock:
            level,state,old=self._find_event(identifier)
            if old['locked'] and set(payload)!={'locked'}:
                raise ValueError('Déverrouillez la zone avant de la modifier.')
            if 'level' in payload and payload['level'] != level or set(payload)&{'id','resourceKind','activeNode'}:
                raise ValueError('La zone ne peut pas changer de niveau ou de type de ressource.')
            event=self._validate_event({**old,**payload})
            before={level:copy.deepcopy(state['events'])}
            state['events'][identifier]=event
            return self._event_commit(before,'update-event',identifier)

    def delete_event(self, identifier):
        with self._lock:
            level,state,event=self._find_event(identifier)
            if event['locked']: raise ValueError('Déverrouillez la zone avant de la supprimer.')
            before={level:copy.deepcopy(state['events'])};state['events'].pop(identifier)
            return self._event_commit(before,'delete-event',identifier)

    def import_events(self, payload):
        with self._lock:
            if not isinstance(payload,dict) or payload.get('format')!='azurik-level-events' or payload.get('version')!=1 or not isinstance(payload.get('events'),list) or not 1<=len(payload['events'])<=100:
                raise ValueError('Fichier de déclencheurs Azurik version 1 requis (1 à 100 zones).')
            records=[self._validate_event(row,identifier='evt_'+uuid4().hex[:16]) for row in payload['events']]
            levels={e['level'] for e in records}
            for level in levels:
                if len(self._state(level)['events'])+sum(e['level']==level for e in records)>100:
                    raise ValueError('Maximum 100 déclencheurs par niveau.')
                self._event_source_hash(level)
            before={level:copy.deepcopy(self._state(level)['events']) for level in sorted(levels)}
            for e in records:self._state(e['level'])['events'][e['id']]=e
            result=self._event_commit(before,'import-events');result['imported']=len(records)
            return result

    def _event_source_hash(self, level):
        state=self._state(level);digest=hashlib.sha256(self._source_path(level).read_bytes()).hexdigest()
        if state['sourceHash'] is not None and state['sourceHash']!=digest:
            raise ValueError('Le fichier source du niveau a changé.')
        state['sourceHash']=digest

    def _event_history(self, level, undo):
        state=self._state(level);source,target=(state['undo'],state['redo']) if undo else (state['redo'],state['undo'])
        row=source[-1];snapshot=copy.deepcopy(row['beforeEvents' if undo else 'afterEvents'])
        before={key:copy.deepcopy(self._state(key)['events']) for key in snapshot}
        # Bulk lock changes are deliberately outside the edit history. Do not
        # erase them by restoring a broader snapshot of another zone's edit.
        for key,events in snapshot.items():
            current=self._state(key)['events']
            for identifier,event in current.items():
                target_event=events.get(identifier)
                left=row['beforeEvents'].get(key,{}).get(identifier)
                right=row['afterEvents'].get(key,{}).get(identifier)
                explicit_lock=left is not None and right is not None and left['locked']!=right['locked']
                if event['locked'] and (target_event is None or
                        {k:v for k,v in event.items() if k!='locked'}!={k:v for k,v in target_event.items() if k!='locked'}):
                    raise ValueError('Déverrouillez la zone pour annuler cette modification.')
                if target_event is not None and not explicit_lock:
                    target_event['locked']=event['locked']
        for key,events in snapshot.items():
            for identifier,event in events.items():self._validate_event(event,identifier=identifier)
            self._state(key)['events']=copy.deepcopy(events)
        target.append(source.pop())
        try:self.save()
        except Exception:
            for key,events in before.items():self._state(key)['events']=events
            source.append(target.pop());raise
        return dict(changed=True,pendingCount=self.pending_count(),history=self._combined_history(),**self._combined_history())

    def _prepare_event_export(self, files):
        from event_export import compile_event_level,add_named_popup,bind_mirror_key
        from level_archive import update_index
        files=dict(files);reports={};custom={};mirror=False
        for level,state in self._project['levels'].items():
            if not self._level_active(level) or not state.get('events'):continue
            events=[self._validate_event(e) for e in state['events'].values()]
            self._event_source_hash(level)
            source=files.get(level,self._source_path(level).read_bytes())
            files[level],reports[level]=compile_event_level(source,self._source_path('w1').read_bytes(),events,self._source_path('e6').read_bytes())
            custom.update({e['id']:e for e in events if e['resourceKind']=='custom'})
            mirror |= any(e['popupBase']=='e6mirror' for e in events)
        registrations=[]
        if custom or mirror:
            for lang,bank in self._popup_banks().items():
                filename=LANGUAGES[lang][:-4]
                data=files.get(filename,bank)
                if mirror:
                    data=bind_mirror_key(data,lang)
                    registrations.append(dict(key='loc/'+lang+'/popups/e6mirror00.tif',tag='surf',archive=LANGUAGES[lang]))
                for e in custom.values():
                    data,record=add_named_popup(data,lang,e)
                    registrations.append(dict(key=record['key'],tag='surf',archive=LANGUAGES[lang]))
                files[filename]=data
        if registrations:
            original=self.gamedata_dir/'index/index.xbr'
            data=files.get('index/index',original.read_bytes())
            files['index/index']=update_index(data,add=registrations)
        return files,reports
