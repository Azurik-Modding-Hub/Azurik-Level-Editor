/** Native dialogue zones. The backend validates and writes the game resources. */
const coordinate = (value, size = false) => {
  if (!Array.isArray(value) || value.length !== 3 || value.some(v => typeof v !== 'number' || !Number.isFinite(v) || Math.abs(v) >= 50000 || (size && (v < 0.1 || v > 1000)))) {
    throw new Error(size ? 'Les dimensions doivent être comprises entre 0,1 et 1 000.' : 'La position doit contenir trois coordonnées finies, inférieures à 50 000 en valeur absolue.');
  }
  return [...value];
};

export function validateEventDraft(value, custom = false) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Évènement invalide.');
  const level = String(value.level || '').trim(), name = String(value.name || '').trim();
  if (!/^[a-z][a-z0-9_]{0,39}$/u.test(level)) throw new Error('Choisissez un niveau disponible.');
  if (!name || [...name].length > 80 || /[\u0000-\u001f\u007f]/u.test(name)) throw new Error('Le nom doit contenir de 1 à 80 caractères.');
  const condition = value.condition ?? 'any';
  if (!['any', 'day', 'night'].includes(condition) || (value.once !== undefined && typeof value.once !== 'boolean')) throw new Error('Choisissez une condition et une fréquence valides.');
  const result = { level, name, position: coordinate(value.position), size: coordinate(value.size, true), condition, once: value.once ?? true };
  if (value.activeNode !== undefined) {
    if (!Number.isInteger(value.activeNode) || value.activeNode < 0 || value.activeNode >= 50000) throw new Error('La condition d’origine de cet évènement est invalide.');
    result.activeNode = value.activeNode;
  }
  if (custom) {
    const text = String(value.text ?? '').replace(/\r\n?/gu, '\n').trim();
    if (!text || [...text].length > 1000 || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(text)) throw new Error('Le texte doit contenir de 1 à 1 000 caractères.');
    result.text = text;
  } else {
    const base = String(value.popupBase || '').trim();
    if (!/^[A-Za-z0-9_/-]{1,160}$/u.test(base) || base.includes('//') || base.startsWith('/') || base.endsWith('/')) throw new Error('Choisissez un dialogue disponible.');
    result.popupBase = base;
  }
  return result;
}

export function parseEventImport(text) {
  if (typeof text !== 'string' || text.length > 2 * 1024 * 1024) throw new Error('Choisissez un fichier JSON d’évènements de 2 Mo maximum.');
  let parsed;
  try { parsed = JSON.parse(text.replace(/^\ufeff/u, '')); } catch { throw new Error('Le fichier JSON est illisible.'); }
  if (parsed?.format !== 'azurik-level-events' || parsed.version !== 1 || !Array.isArray(parsed.events) || !parsed.events.length || parsed.events.length > 100) throw new Error('Le fichier doit utiliser le format azurik-level-events, version 1, avec 1 à 100 évènements.');
  return { format: parsed.format, version: 1, events: parsed.events.map(value => ({ ...validateEventDraft(value, value.resourceKind === 'custom' || value.text !== undefined), ...(value.locked === true ? { locked: true } : {}) })) };
}

export function createEventManager({ api, openDialog, closeDialog, getContext, translate = text => text, toast,
  onLoaded = () => {}, onChanged = () => {}, onSelect = () => {}, onPreview = () => {} }) {
  let data = { events: [], catalog: [] }, working = false, session = 0, read = 0;
  const context = () => getContext?.() || {};
  const events = () => (data.events || []).filter(event => event.level === context().level);
  async function refresh() {
    const token = ++read, level = context().level;
    if (!level) { data = { events: [], catalog: [] }; onLoaded([], data); return data; }
    const result = await api('/api/events');
    if (token !== read || context().level !== level) return data;
    if (!Array.isArray(result.events) || !Array.isArray(result.catalog)) throw new Error('Le catalogue des évènements est illisible.');
    data = result; onLoaded(events(), data); return data;
  }
  async function mutate(path, payload, method = 'POST') {
    if (working) return null;
    working = true;
    try {
      const result = await api(path, payload, { method });
      await refresh(); await onChanged(result); return result;
    } finally { working = false; }
  }
  const patch = (id, fields) => mutate(`/api/events/${encodeURIComponent(id)}`, fields, 'PATCH');
  function start(title) {
    const token = ++session, body = openDialog(translate(title), translate('ÉVÈNEMENTS DU NIVEAU'));
    const doc = body.ownerDocument || globalThis.document;
    const active = () => token === session && body.isConnected !== false && (body.closest?.('dialog')?.open ?? true);
    const node = (tag, text = '', cls = '', raw = false) => {
      const result = doc.createElement(tag); result.textContent = raw ? text : translate(text); result.className = cls;
      if (raw) result.setAttribute('data-i18n-ignore', '');
      return result;
    };
    const error = node('p', '', 'asset-import-error'); error.setAttribute('role', 'alert');
    const report = failure => { if (active()) { error.textContent = translate(String(failure?.message || failure)); toast?.(error.textContent, true); } };
    const button = (text, handler, primary = false) => {
      const result = node('button', text, `button ${primary ? 'primary' : 'secondary'}`); result.type = 'button'; result.addEventListener('click', handler); return result;
    };
    const field = (form, labelText, input, id) => {
      input.id = id; input.setAttribute('data-i18n-ignore', '');
      const label = node('label'); label.htmlFor = id; label.append(node('span', labelText), input); form.append(label); return input;
    };
    const cancel = button('Revenir au niveau', () => { ++session; closeDialog(); });
    return { body, doc, active, node, button, field, error, report, cancel };
  }
  async function ready(view) {
    if (!context().level) { view.body.append(view.node('p', 'Importez votre jeu et ouvrez un niveau pour créer un évènement.'), view.cancel); return false; }
    try { await refresh(); return view.active(); } catch (error) { view.report(error); view.body.append(view.error, view.cancel); return false; }
  }
  function option(view, select, value, text, raw = false) { const item = view.node('option', text, '', raw); item.value = value; select.append(item); }
  function vectors(view, form, title, prefix, values, isSize = false) {
    const label = view.node('span', title, 'field-label'), group = view.node('div', '', 'xyz-fields'); form.append(label, group);
    return ['X', 'Y', 'Z'].map((axis, i) => {
      const input = view.node('input'); input.type = 'number'; input.step = '0.1'; input.required = true; input.value = String(values[i]);
      input.min = isSize ? '0.1' : '-49999.9'; input.max = isSize ? '1000' : '49999.9';
      view.field(group, axis, input, `${prefix}${axis}`); return input;
    });
  }
  async function showForm(custom = false, id = null, initialText = '') {
    const view = start(id ? 'Modifier un évènement' : custom ? 'Importer un texte personnalisé' : 'Créer une zone de dialogue');
    if (!await ready(view)) return;
    const current = id ? events().find(event => event.id === id) : null;
    if (id && !current) { view.body.append(view.node('p', 'Cet évènement n’existe plus.'), view.cancel); return; }
    custom = current ? current.resourceKind === 'custom' : custom;
    const form = view.node('form', '', 'asset-import-form event-form');
    const name = view.node('input'); name.type = 'text'; name.maxLength = 80; name.required = true; name.value = current?.name || translate(custom ? 'Mon dialogue' : 'Nouvelle zone de dialogue');
    view.field(form, 'Nom de l’évènement', name, 'eventName');
    let popup, text;
    if (custom) {
      const file = view.node('input'); file.type = 'file'; file.accept = '.txt,text/plain'; view.field(form, 'Importer un fichier TXT UTF-8', file, 'eventTextFile');
      text = view.node('textarea'); text.rows = 6; text.maxLength = 1000; text.required = true; text.value = current?.text || initialText;
      view.field(form, 'Texte de la boîte de dialogue', text, 'eventText');
      file.addEventListener('change', async () => {
        const selected = file.files?.[0]; if (!selected) return;
        try {
          if (selected.size > 64 * 1024) throw new Error('Choisissez un fichier TXT de 64 Ko maximum.');
          const buffer = await selected.arrayBuffer(); const imported = new TextDecoder('utf-8', { fatal: true }).decode(buffer).replace(/^\ufeff/u, '');
          if (view.active()) text.value = imported;
        } catch (failure) { view.report(failure); }
      });
      form.append(view.node('p', 'Le texte est converti en panneau du jeu. Aucune voix n’est créée automatiquement.', 'event-note'));
    } else {
      const search = view.node('input'); search.type = 'search'; view.field(form, 'Rechercher un dialogue', search, 'eventPopupSearch');
      popup = view.node('select'); popup.required = true; view.field(form, 'Dialogue du jeu', popup, 'eventPopup');
      const language = view.node('select');
      for (const [value, label] of [['english', 'English'], ['french', 'Français'], ['german', 'Deutsch']]) option(view, language, value, label, true);
      language.value = 'english'; view.field(form, 'Langue du panneau · aperçu', language, 'eventPopupLanguage');
      const image = view.node('img', '', 'event-popup-preview'), imageStatus = view.node('p', '', 'event-note');
      image.alt = translate('Aperçu du panneau original'); image.setAttribute('data-i18n-ignore', '');
      const previewPopup = () => {
        image.hidden = !popup.value; imageStatus.textContent = '';
        if (popup.value) image.src = `/api/events/popup?base=${encodeURIComponent(popup.value)}&language=${encodeURIComponent(language.value)}`;
      };
      image.addEventListener('error', () => { image.hidden = true; imageStatus.textContent = translate('Aperçu du panneau indisponible dans cette langue.'); });
      image.addEventListener('load', () => { image.hidden = false; imageStatus.textContent = ''; });
      form.append(image, imageStatus); popup.addEventListener('change', previewPopup); language.addEventListener('change', previewPopup);
      const catalog = [...new Map(data.catalog.filter(row => typeof row.base === 'string').map(row => [row.base, row])).values()];
      const populate = () => {
        const selected = popup.value || current?.popupBase, query = search.value.toLocaleLowerCase(); popup.replaceChildren();
        const filtered = catalog.filter(row => `${row.base} ${row.label || ''}`.toLocaleLowerCase().includes(query));
        for (const row of filtered) option(view, popup, row.base, `${row.label || row.base} · ${row.pages ?? 1} ${translate('pages')} · ${row.voiceAvailable ? translate('Voix disponible') : translate('Sans voix')}`, true);
        popup.value = filtered.some(row => row.base === selected) ? selected : filtered[0]?.base || '';
        previewPopup();
      };
      search.addEventListener('input', populate); populate();
      form.append(view.node('p', 'Le catalogue utilise les textes et voix présents dans votre dump. La langue de l’interface ne change pas celle du jeu.', 'event-note'));
    }
    const center = current?.position || context().position || [0, 0, 0], dimensions = current?.size || [4, 4, 4];
    const position = vectors(view, form, 'Centre de la zone · monde Z ↑', 'eventPosition', center);
    const size = vectors(view, form, 'Dimensions de la zone', 'eventSize', dimensions, true);
    const location = view.button('Utiliser la sélection ou le centre de la vue', () => {
      const p = context().position || [0, 0, 0]; position.forEach((input, i) => { input.value = String(p[i]); }); preview();
    }); form.append(location);
    const condition = view.node('select'); for (const [value, label] of [['any', 'À toute heure'], ['day', 'Pendant le jour'], ['night', 'Pendant la nuit']]) option(view, condition, value, label);
    condition.value = current?.condition || 'any'; view.field(form, 'Quand déclencher le dialogue', condition, 'eventCondition');
    const once = view.node('select'); option(view, once, 'true', 'Une fois par chargement du niveau'); option(view, once, 'false', 'À chaque activation de la zone'); once.value = String(current?.once ?? true);
    view.field(form, 'Fréquence', once, 'eventOnce');
    form.append(view.node('p', 'Déclenchement par proximité : Azurik traverse une zone invisible, sans toucher l’obstacle. Le mode répété peut réactiver le panneau tant qu’il reste dans la zone.', 'event-note'));
    const numeric = inputs => inputs.map(input => input.value.trim() === '' ? NaN : Number(input.value));
    const draft = () => validateEventDraft({ level: current?.level || context().level, name: name.value, position: numeric(position), size: numeric(size), condition: condition.value, once: once.value === 'true', ...(custom ? { text: text.value } : { popupBase: popup.value }) }, custom);
    const preview = () => { try { onPreview({ position: coordinate(numeric(position)), size: coordinate(numeric(size), true) }); } catch { /* incomplete numeric edits keep the previous box */ } };
    [...position, ...size].forEach(input => input.addEventListener('input', preview)); preview();
    const actions = view.node('div', '', 'asset-import-actions');
    const save = view.button(id ? 'Enregistrer cet évènement' : 'Créer cet évènement', () => {}, true); save.type = 'submit'; actions.append(save, view.cancel);
    if (current) {
      const lock = view.button(current.locked ? 'Déverrouiller' : 'Verrouiller', async () => {
        if (working) return;
        lock.disabled = true;
        try { await patch(current.id, { locked: !current.locked }); if (view.active()) await showForm(custom, current.id); } catch (failure) { view.report(failure); lock.disabled = false; }
      }); actions.append(lock);
      const remove = view.button('Supprimer cet évènement', () => showDelete(current.id)); remove.disabled = !!current.locked; actions.append(remove);
    }
    if (current?.locked) {
      form.append(view.node('p', 'Cet évènement est verrouillé. Déverrouillez-le pour le modifier.', 'event-note'));
      save.disabled = true; [name, popup, text, condition, once, ...position, ...size].filter(Boolean).forEach(input => { input.disabled = true; }); location.disabled = true;
    }
    form.append(view.error, actions); view.body.append(form);
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (working || current?.locked) return;
      try {
        const payload = draft(); save.disabled = true;
        const result = id ? await patch(id, payload) : await mutate(custom ? '/api/events/custom' : '/api/events/add', payload);
        if (result && view.active()) { ++session; closeDialog(); onSelect(result.id || result.event?.id || id); toast?.(translate('Évènement enregistré dans le projet.')); }
      } catch (failure) { view.report(failure); } finally { if (view.active()) save.disabled = false; }
    });
  }
  async function showManage() {
    const view = start('Gérer les évènements'); if (!await ready(view)) return;
    const actions = view.node('div', '', 'asset-import-actions');
    actions.append(view.button('Créer une zone de dialogue', () => showForm()), view.button('Importer un texte personnalisé', () => showForm(true)), view.button('Importer des évènements JSON', showImport)); view.body.append(actions);
    const list = view.node('div', '', 'event-list');
    if (!events().length) list.append(view.node('p', 'Aucun évènement ajouté dans ce niveau.'));
    for (const event of events()) {
      const row = view.node('div', '', 'event-list-row'), text = view.node('div');
      text.append(view.node('strong', event.name || event.id, '', true), view.node('small', `${event.popupBase || translate('Texte personnalisé')} · ${event.condition === 'any' ? translate('À toute heure') : translate(event.condition === 'day' ? 'Pendant le jour' : 'Pendant la nuit')}`, '', true));
      const select = view.button('Voir dans le niveau', () => { ++session; closeDialog(); onSelect(event.id, true); });
      row.append(text, select, view.button('Modifier', () => showForm(event.resourceKind === 'custom', event.id))); list.append(row);
    }
    view.body.append(view.node('p', 'Les zones vertes sont des repères de l’éditeur. Elles restent invisibles et non bloquantes dans le jeu.', 'event-note'), list, view.error, view.cancel);
  }
  async function showDelete(id) {
    const view = start('Supprimer cet évènement'); if (!await ready(view)) return;
    const current = events().find(event => event.id === id);
    if (!current || current.locked) { view.body.append(view.node('p', current ? 'Cet évènement est verrouillé. Déverrouillez-le pour le modifier.' : 'Cet évènement n’existe plus.'), view.cancel); return; }
    view.body.append(view.node('p', current.name || current.id, '', true), view.node('p', 'Retirer cette zone de dialogue du projet ? Les fichiers du dump original restent intacts.'));
    const remove = view.button('Supprimer du projet', async () => {
      if (working) return; remove.disabled = true;
      try { const result = await mutate(`/api/events/${encodeURIComponent(id)}`, {}, 'DELETE'); if (result && view.active()) { ++session; closeDialog(); toast?.(translate('Évènement supprimé du projet.')); } }
      catch (failure) { view.report(failure); if (view.active()) remove.disabled = false; }
    }); view.body.append(view.error, remove, view.cancel);
  }
  async function showImport() {
    const view = start('Importer des évènements JSON'); if (!await ready(view)) return;
    const form = view.node('form', '', 'asset-import-form'), file = view.node('input'); file.type = 'file'; file.accept = '.json,application/json'; file.required = true;
    view.field(form, 'Fichier JSON d’évènements', file, 'eventImportFile');
    form.append(view.node('p', 'Format azurik-level-events, version 1. Les niveaux indiqués dans le fichier doivent exister dans votre projet.', 'event-note'));
    const submit = view.button('Importer les évènements', () => {}, true); submit.type = 'submit'; form.append(view.error, submit, view.cancel); view.body.append(form);
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (working) return;
      try {
        const selected = file.files?.[0]; if (!selected || selected.size > 2 * 1024 * 1024) throw new Error('Choisissez un fichier JSON d’évènements de 2 Mo maximum.');
        const payload = parseEventImport(await selected.text()); if (!view.active()) return; submit.disabled = true;
        const result = await mutate('/api/events/import', payload); if (result && view.active()) { ++session; closeDialog(); toast?.(translate('Évènements importés dans le projet.')); }
      } catch (failure) { view.report(failure); } finally { if (view.active()) submit.disabled = false; }
    });
  }
  return { refresh, patch, showManage, showCreate: () => showForm(), showCustom: () => showForm(true), showImport,
    showEdit: id => showForm(false, id), showDelete, get events() { return events(); }, get working() { return working; } };
}

export function createEventZone(THREE, event) {
  const object = new THREE.Group(), geometry = new THREE.BoxGeometry(1, 1, 1);
  const body = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: 0x63db9b, transparent: true, opacity: .055, depthWrite: false, side: THREE.DoubleSide }));
  const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geometry), new THREE.LineBasicMaterial({ color: event.locked ? 0xbeaa76 : 0x63db9b, transparent: true, opacity: .85 }));
  object.add(body, edges); object.position.fromArray(event.position); object.scale.fromArray(event.size); object.name = event.name || event.id;
  object.traverse(child => { child.userData.id = event.id; child.userData.eventZone = true; });
  return object;
}
