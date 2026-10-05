# RR Helper 0.2.47: Standard model export

Standard pieces always need their model and do not need a generated icon.
Previously Standard and Modular shared Model / Icon choices, so a saved
Icon-only choice could skip Standard geometry or unnecessarily render thumbnails.

Standard's Export Queue now has one Export action without Model / Icon toggles.
Queue, Selected, Collection and direct package export enforce model export and
skip icon generation, including old saved flags and explicit Icon-only requests.
The Standard File > Export menu also omits redundant FBX-only / Icon-only choices.
The Icon panel's export-inclusion toggle is shown only for Modular.

Modular keeps Model / Icon choices, icon-only model-contract preservation and its
existing import workflow. Entering Standard or exporting there does not overwrite
the saved Modular choices. Existing icon files and their metadata are retained;
this change does not remove previously exported data. Explicit Render Icons and
Preview actions remain separate from Standard package export.

## Validation

- 31 Standard transaction / model-contract checks pass.
- 14 export feedback, operator and UI checks pass, including Standard's hidden
  toggles, retained Modular choices and mode-specific File > Export entries.
- Four isolated Blender 5.2 native checks pass: output route separation,
  mode switching, real FBX export despite saved/requested Icon-only flags with no
  icon-render call, model hash agreement, and preserved reference-layout matrices.
  Native evidence is under
  `D:/Blender/Projects/Build/WIP/Validation/standard_model_only_20261003/`.

Unity source compatibility review found no required C# changes.
`StandardAssetPrefabPublisher` and `StandardAssetImportService` require the model,
manifest and model hash, and do not consume iconFile or exportedResources.
The existing Standard publisher test also checks that icon.png does not trigger
publication. Modular's `BuilderGeneratedAssetImporter` and hotbar continue to use
their icon workflow. This is a source review, not a new Unity runtime test.

The 0.2.47 package was built locally. Both existing Blender 5.2 installation
copies (user addons and installation addons_core) passed deployment checks with
20 files and zero differences. The current Builder6 GUI refreshed successfully:
Standard showed no resource toggles, Modular retained both, and the mode returned
to Standard. Builder6 was saved at 23:24:30 +08:00 on 2026-10-03.
The existing machine ledger records the save hash and recovery locations.
No Git commit, push or remote release is included.
