# Dialogue events — 2.2.0 development preview

This preview adds editable player proximity zones to Azurik Level Studio. It is
intended for testing before a public release. Original game files remain read-only.

Open **Events → Import events** to load an `azurik-level-events` JSON file. The
dialogue restoration trial includes `zones-dialogues.azevents.json`, containing
19 areas around the barriers, air tube, zipline entrance and mirror controls.
The same definitions are included in `examples/restored-dialogue-zones-eu.azevents.json`.
Open the corresponding level to see its green boxes. Select a box and use the
move gizmo or position fields, or open its event form to change its dimensions.
Coordinates use the game's original world axes, with Z pointing upward.

An event can display an existing popup sequence, including previously unused
ones. The catalogue shows the first original panel in English, French or German.
Selecting a preview language does not change the game's language. Day/night
conditions use the game's original night curve. **Once per level load** resets
when that level is loaded again; it does not alter save files. **On each
activation** follows the native contact signal and can repeat.

**Events → Import text** accepts a UTF-8 TXT file. The text must fit one panel;
the editor checks this before saving. It appends a new panel in each available
language bank, using the same imported text, without replacing an original
conversation. This initial new-panel import is silent. Voice replacement remains
available separately in Dialogue Studio.

Events support individual and bulk locking, deletion, Ctrl+Z / Ctrl+Shift+Z,
project save/reopen and restoring a level to its source state. Importing a batch
across several levels is one undo operation. Deleting an authored zone removes
it from the next export; the source game's own event graph is retained.

Export the mod and use **Levels → Build mod ISO** with the matching original
ISO. The export includes the new native field, condition and popup nodes, along
with any new popup textures and their asset registry entries. It does not patch
the executable. The separate dialogue trial ISO also includes an experimental
diamond pickup queue; that executable patch is not part of a standard event export.

The supported collision template is read from EU W1 in the user's dump. The
native loader, spatial registration, world bounds, day/night curve and native
once latch have been checked. Complete gameplay and placements in Xemu still
require testing. The original game, voice recordings and save files are not
distributed with the editor.

If a changed ISO still shows old data, clear only Xbox X/Y/Z caches. Preserve
E and the save files.
