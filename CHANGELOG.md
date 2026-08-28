# Changelog

## Unreleased

- The Library Manager exports its reference list as a read-only `<name>.libraries.txt` (name, version, vendor per line). Library behaviour itself is not exportable, but any bench check of a library needs to know exactly which version the project resolves.
- The Visualization Manager exports natively as a read-only `<name>.service.txt`, recursively, so the global hotkey (key configuration) mapping and target/web visualization settings become reviewable. It is still never imported - importing it raises interactive overwrite dialogs, which is why earlier versions dropped it from the export entirely - and the project template continues to carry the real object.

- Graphical action, transition and method renderings draw the member's own body. The PLCopen export of a sub-POU wraps it in its parent POU, parent body included, and earlier versions rendered that parent body - so `PLC_PRG.ACT_x.txt` carried a copy of the parent `PLC_PRG` dump and the action's own logic appeared in no `.txt` at all. If a ScriptEngine build exports no body for the member, no `.txt` is written and the summary says so, rather than writing a rendering of the wrong POU.
- `.txt` network numbering follows the native export's network list. Out-commented networks are absent from the PLCopen export, so earlier versions silently dropped them and renumbered everything after them away from the numbers in the CODESYS editor and the native `.xml`. Now every network keeps its editor number: out-commented networks appear as a placeholder marked `out-commented in CODESYS - does not execute`, empty networks as `empty network`, and a network's jump label no longer splits it into two numbered headers. Ladder network headers gain the network comment on the way. If the native list cannot be lined up with what was rendered, the `.txt` keeps sequential numbering and says so in a warning line, and the export summary counts the misalignment.

- Visualisations export as `<name>.vis.xml`. Earlier versions wrote `<name>.xml`, which silently collided with any POU of the same name (a `Main` program plus a `Main` visualisation is common). Old plain `.xml` exports still import correctly; re-exporting once migrates the tracked files.
- Visualisation service objects (visualisation manager and related service GUIDs) are recognised and no longer exported.
- `Export To Files` writes the export into a sibling folder named `<project_name>.codescribe_staging` and only swaps it into place once the export completes. Earlier versions deleted the target folder up front, so a locked folder (an open Explorer window, IDE, git client or antivirus) aborted the export immediately and a mid-export crash left the on-disk copy destroyed. If the target folder is locked and cannot be swapped, the staged files are synced into it instead and the export still succeeds; if that also fails, the error dialog reports the staging folder path, so the completed export is preserved.
- Device-tree sibling devices (Ethernet, Modbus and fieldbus devices that sit next to `Plc Logic` as direct children of the PLC device) are exported to `<device>/devices/<name>.xml`, one file per top-level device. On import, each tracked device is removed and recreated from its exported xml; devices fixed by the device package (for example `Local_IO` and `HMI` on IFM hardware) cannot be removed and are skipped with a message, with their configuration carried by the project template. A `_NO_EXPORT` folder added as a direct child of the PLC device disables the device-tree export.
- New `Export Lib To Files` script exports library projects, which keep their objects directly under the project root rather than under a Device. It writes the same on-disk format as `Export To Files`, without the device and application folder levels. Importing a library export back into a project is not yet supported.

## v0.2.0

- Each script reports its outcome in a dialog when it finishes. Failures show the full Python traceback, so problems no longer hide in the message view.
- `.st` files are written as UTF-8, so non-ASCII content (Cyrillic, accented characters, smart punctuation) exports and round-trips correctly.
- Actions and Transitions export as `.st` rather than native xml. The kind is encoded in the filename (`MyPou.MyAction.action.st`, `MyPou.MyTransition.transition.st`) and the file contains the implementation text only, as these objects have no textual declaration. Exports made with earlier versions (where these objects were `.xml`) still import correctly; re-exporting once after upgrading migrates the tracked files to the new format.
- Devices that have no `Communication` object at all are supported. The communication step is skipped for that device and no `communication` folder is created in the export. Previously this aborted the export, import or Save As Template for the whole device.
- Validated end-to-end on CODESYS V3.5 SP11 (32-bit) with an IFM CR711S compound (Standard + SIL2) project: export/import round-trip, code generation, simulation and a hardware download.

## v0.1.x

Initial releases. No detailed history was kept.
