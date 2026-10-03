# Character Designer 0.70.1 — Refresh recovery

The live X session retained a registered 0.69.3 UI whose callback namespace differed from sys.modules, with no enabled flag on the cached module. addon_utils.disable skipped teardown, so enabling 0.70.0 failed with stale RNA classes and pointer properties.

Refresh now resolves the registered panel callback namespace, tears down that runtime directly, propagates teardown errors, captures actual enable exceptions, and unregisters a successfully enabled replacement before rollback on post-enable failure. A prior error permits retry without another disk change and is displayed below Refresh.

Four disposable Blender 5.2 checks pass: normal refresh, module replacement with a stale disabled flag, injected enable failure with rollback, and retry. Artist object records and Shape Key coordinates survive; the generated-name load handler remains singular. No user scene is opened or saved by the tests.
