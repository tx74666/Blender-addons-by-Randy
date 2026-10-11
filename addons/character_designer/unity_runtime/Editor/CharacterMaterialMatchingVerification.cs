// Explicit native acceptance only. Never runs on load, through TestRunner, or against production model imports.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace CharacterDesigner.Unity.Editor
{
    public static class CharacterMaterialMatchingVerification
    {
        const string SourceModel = "Assets/Art/Character/Cosha/Cosha.fbx";
        const string SourceMaterial = "Assets/Art/Materials/Crystal/M_CrystalBlue.mat";
        const string SourceName = "Crystal_Blue";
        const string MissingSource = "__MaterialMatchVerificationMissingSource__";
        const string VectorCatalogHash = "3bc91fc496d5c3bc262320da88f69edf860e1d62f8b0f7e5061d7d5eabb5fe0e";
        static readonly PropertyInfo DirtyId = typeof(Scene).GetProperty("dirtyID", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
        static bool running;

        sealed class Stamp
        {
            internal string path, hash;
            internal bool exists, assetDirty, importerDirty;
        }
        sealed class Run
        {
            internal string id, folder, model, manifest, root, directory, nativeUndoRecord;
            internal Material first, alternate;
            internal JObject document, report;
            internal CharacterMaterialMatching.VerificationSession session;
            internal int revision, nativeThreadId;
        }
        sealed class ExportVector
        {
            internal string stage, fbx, manifest, fbxHash, manifestHash;
            internal JObject document;
        }
        sealed class NativeUndoState
        {
            internal List<string> records;
            internal int cursor, group;
            internal bool redo;
        }

        [MenuItem("Tools/Character Designer/Validation/Verify Material Matching (Isolated)")]
        public static void Verify()
            => Verify(false);

        [MenuItem("Tools/Character Designer/Validation/Verify Material Matching With Export Vectors (Isolated)")]
        public static void VerifyWithExportVectors()
        {
            if (running) { Debug.LogWarning("Character material verification is already running or queued."); return; }
            running = true;
            EditorApplication.update += RunExportVectorVerification;
            Debug.Log("Character material export-vector verification queued. Results will be written after the isolated callback completes.");
        }

        static void RunExportVectorVerification()
        {
            EditorApplication.update -= RunExportVectorVerification;
            try { VerifyCore(true, false); }
            finally { running = false; }
        }

        [MenuItem("Tools/Character Designer/Validation/Verify Material Matching Undo (Isolated)")]
        public static void VerifyUndo()
        {
            if (running) { Debug.LogWarning("Character material verification is already running or queued."); return; }
            running = true;
            EditorApplication.update += RunUndoVerification;
            Debug.Log("Character material native Undo verification queued. Results will be written after the isolated callback completes.");
        }

        static void RunUndoVerification()
        {
            EditorApplication.update -= RunUndoVerification;
            try { VerifyCore(false, true); }
            finally { running = false; }
        }

        static void Verify(bool includeExportVectors, bool nativeUndo = false)
        {
            if (running) { Debug.LogWarning("Character material verification is already running."); return; }
            running = true;
            try { VerifyCore(includeExportVectors, nativeUndo); }
            finally { running = false; }
        }
        static void VerifyCore(bool includeExportVectors, bool nativeUndo)
        {
            string id = Guid.NewGuid().ToString("N");
            string root = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            var run = new Run { id = id, root = root, nativeThreadId = System.Threading.Thread.CurrentThread.ManagedThreadId, folder = "Assets/~QA_MaterialMatching_" + id,
                directory = Path.Combine(root, ".codex", "task-staging", "character2-material-match-20261010", "native-verification", id) };
            run.model = run.folder + "/Fixture.fbx";
            run.manifest = run.folder + "/Fixture.cdesigner.json";
            run.report = new JObject { ["schema"] = "cdesigner.material-match-verification/1", ["utc"] = DateTime.UtcNow.ToString("O"),
                ["unity_version"] = Application.unityVersion, ["run_id"] = id, ["fixture_folder"] = run.folder,
                ["cases"] = new JArray(), ["passed"] = false, ["undo_native_verified"] = false, ["include_export_vectors"] = includeExportVectors, ["native_undo_only"] = nativeUndo,
                ["limitations"] = new JArray(nativeUndo ? "Only the exact fresh fixture importer may register one native Undo snapshot and perform one owned Undo/Redo cycle." : "Global Undo/Redo is deliberately not invoked or registered for the fixture.",
                    "The native Material picker, preview, immediate Loading repaint and busy-editor recovery need separate UI evidence.",
                    includeExportVectors ? "The optional real exports are a factory-created four-vertex test body, not the full authored Cosha or an interactive Blender roundtrip." : "This uses copied current FBX bytes with fresh manifests; a genuine changed-FBX Blender re-export remains separate.",
                    "Runtime prefab/Forearm regeneration is outside this fixture; no forearm sidecar is copied.") };
            Object[] selection = Selection.objects;
            Object activeSelection = Selection.activeObject;
            string scenes = SceneState();
            int undoGroup = Undo.GetCurrentGroup();
            string undoName = Undo.GetCurrentGroupName();
            List<Stamp> originals = null, fixtureMaterials = null;
            Dictionary<string, string> receipts = null;
            string sourceRenderers = null;
            ExportVector[] vectors = null;
            List<Stamp> vectorInputs = null;
            NativeUndoState nativeHistory = null;
            try
            {
                Require(CharacterMaterialMatching.MainEditor && !CharacterMaterialMatching.EditorBusy,
                    "Use the idle main Editor with Play stopped and no build, compilation or import in progress.");
                if (nativeUndo)
                {
                    nativeHistory = CaptureNativeUndo();
                    Require(!nativeHistory.redo && nativeHistory.cursor == nativeHistory.records.Count, "Pending user Redo must remain intact; no fixture assets were created.");
                    run.report["native_undo_before"] = UndoEvidence(nativeHistory);
                }
                Require(AssetDatabase.LoadAssetAtPath<GameObject>(SourceModel) != null, "The current Cosha source model is missing.");
                var sourceMaterial = AssetDatabase.LoadAssetAtPath<Material>(SourceMaterial);
                Require(sourceMaterial != null && !EditorUtility.IsDirty(sourceMaterial), "The existing Crystal Material must have no pending asset edits.");
                var sourceImporter = AssetImporter.GetAtPath(SourceModel);
                Require(sourceImporter != null && !EditorUtility.IsDirty(sourceImporter), "The source model has pending Inspector edits.");
                if (includeExportVectors)
                {
                    vectors = ReadExportVectors(run, out string catalog);
                    vectorInputs = new[] { catalog }.Concat(vectors.SelectMany(value => new[] { value.fbx, value.manifest })).Select(CaptureFile).ToList();
                    run.report["export_vectors_catalog"] = new JObject { ["path"] = catalog, ["sha256"] = VectorCatalogHash, ["version"] = "0.77.20" };
                }
                run.session = CharacterMaterialMatching.BeginIsolatedVerification(run.model, run.id);
                originals = CaptureOriginals(run.session.ProtectedAssetPaths);
                receipts = ReceiptSnapshot(run.session.StorePath, run.id);
                sourceRenderers = RendererIdentity(SourceModel);
                run.report["scene_before"] = scenes;
                run.report["undo_group_before"] = undoGroup;
                run.report["undo_name_before"] = undoName;
                Require(!string.IsNullOrEmpty(AssetDatabase.CreateFolder("Assets", run.folder.Substring(7))), "Could not create the new fixture folder.");
                Require(AssetDatabase.CopyAsset(SourceModel, run.model), "Could not copy the source FBX.");
                string firstPath = run.folder + "/Matched.mat", alternatePath = run.folder + "/Alternate.mat";
                Require(AssetDatabase.CopyAsset(SourceMaterial, firstPath) && AssetDatabase.CopyAsset(SourceMaterial, alternatePath), "Could not copy the two Material fixtures.");
                AssetDatabase.ImportAsset(run.model, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                run.first = AssetDatabase.LoadAssetAtPath<Material>(firstPath);
                run.alternate = AssetDatabase.LoadAssetAtPath<Material>(alternatePath);
                Require(run.first != null && run.alternate != null && run.first != run.alternate, "The fixture Materials have no distinct persistent identities.");
                fixtureMaterials = new[] { firstPath, alternatePath }.SelectMany(path => new[] { path, path + ".meta" }).Select(Capture).ToList();
                var originalManifest = JObject.Parse(File.ReadAllText(Path.ChangeExtension(Full(run, SourceModel), ".cdesigner.json")));
                Require(originalManifest["materials"] is JArray inventory && inventory.OfType<JValue>().Any(value => value.Value<string>() == SourceName), "The current source manifest does not contain Crystal_Blue.");
                run.document = new JObject { ["schema"] = "cdesigner.unity-character.v1", ["ok"] = true,
                    ["asset_id"] = run.id, ["filename"] = "Fixture.fbx", ["files"] = new JObject { ["Fixture.fbx"] = Hash(Full(run, run.model)) },
                    ["materials"] = originalManifest["materials"].DeepClone(), ["simple_materials"] = new JArray(), ["unity_material_matches"] = new JArray() };
                run.session.SetFixtureRemap(SourceName, null);
                Publish(run, new JArray(), new JArray());

                if (nativeUndo) Case(run, "native_owned_importer_undo_redo", () => VerifyNativeUndo(run, nativeHistory));
                else
                {
                Case(run, "native_inventory_and_new_identities", () =>
                {
                    run.session.ValidateSource(SourceName);
                    Require(AssetDatabase.AssetPathToGUID(SourceModel) != AssetDatabase.AssetPathToGUID(run.model), "The fixture FBX reused the source GUID.");
                    Require(AssetDatabase.AssetPathToGUID(SourceMaterial) != AssetDatabase.AssetPathToGUID(firstPath), "The fixture Material reused the source GUID.");
                    Require(!Map(run).ContainsKey(Key(SourceName)), "The initial fixture remap was not cleared.");
                    Evidence(run)["source_inventory"] = NativeInventory(run.model);
                });
                Case(run, "cancel_and_timeout_leave_map_unchanged", () =>
                {
                    var before = Map(run);
                    var cancelled = run.session.CreateRequest(SourceName);
                    run.session.Cancel(cancelled); run.session.Enqueue(cancelled);
                    CheckResult(run, cancelled, "cancelled");
                    var expired = run.session.CreateRequest(SourceName, 181);
                    run.session.Enqueue(expired); CheckResult(run, expired, "failed");
                    Require(SameMap(before, Map(run)), "Cancelled or expired input changed a remap.");
                });
                Case(run, "duplicate_and_first_match", () =>
                {
                    var request = run.session.CreateRequest(SourceName);
                    run.session.Enqueue(request); run.session.Enqueue(request);
                    Require(run.session.QueuedCount(request) == 1 && run.session.Result(request) == null, "A duplicate request was not queued exactly once.");
                    var duplicateSource = run.session.CreateRequest(SourceName);
                    run.session.Enqueue(duplicateSource); CheckResult(run, duplicateSource, "failed");
                    var result = run.session.ApplyRequest(request, run.first);
                    Require(result != null && result.status == "matched", "The native first match failed: " + result?.error);
                    CheckResult(run, request, "matched"); Matched(run, run.first);
                    string resultHash = Hash(Path.Combine(run.session.StorePath, "requests", request.request_id + ".result.json"));
                    run.session.Enqueue(request);
                    Require(resultHash == Hash(Path.Combine(run.session.StorePath, "requests", request.request_id + ".result.json")), "A terminal duplicate rewrote the result.");
                });
                Case(run, "fresh_manifest_and_latest_manual_match", () =>
                {
                    Publish(run, Matches(run, run.first), new JArray()); run.session.Process(); Matched(run, run.first);
                    var request = run.session.CreateRequest(SourceName); run.session.Enqueue(request);
                    var result = run.session.ApplyRequest(request, run.alternate);
                    Require(result != null && result.status == "matched", "The alternate manual match failed: " + result?.error);
                    run.session.Process(); Matched(run, run.alternate);
                    Require(run.session.Receipt(SourceName).explicit_choice, "The latest manual receipt lost its explicit choice.");
                    Publish(run, Matches(run, run.first), new JArray()); run.session.Process(); Matched(run, run.first);
                });
                Case(run, "failed_manual_receipt_only_verifies_across_restart", () =>
                {
                    Publish(run, Matches(run, run.first), new JArray()); run.session.Process(); Matched(run, run.first);
                    var request = run.session.CreateRequest(SourceName); run.session.Enqueue(request);
                    var result = run.session.ApplyRequest(request, run.alternate);
                    Require(result != null && result.status == "matched", "The manual override precondition failed: " + result?.error);
                    Matched(run, run.alternate);
                    foreach (Material reverted in new[] { run.first, null })
                    {
                        // These private map changes simulate Undo/Redo state; they never touch the global Undo stack.
                        run.session.SetFixtureRemap(SourceName, reverted);
                        var before = Map(run);
                        run.session.Process(false); FailedManual(run, run.alternate);
                        Require(SameMap(before, Map(run)), "Verification repaired a manually reverted map.");
                        run.session.ForgetVerifiedSignature();
                        run.session.Process(); FailedManual(run, run.alternate);
                        Require(SameMap(before, Map(run)) && !RendererUses(run.model, run.alternate), "Restart-style verification replayed the failed latest choice or the older declared match.");
                        run.session.SetFixtureRemap(SourceName, run.alternate);
                        run.session.Process(false); Matched(run, run.alternate);
                        Require(CurrentReceipt(run, "matched").explicit_choice, "Restoring the exact map lost the explicit manual choice.");
                    }
                    // A manual match may be newer than an unchanged Simple report as well as an unchanged match report.
                    Publish(run, new JArray(), Simple()); run.session.Process();
                    var simpleRequest = run.session.CreateRequest(SourceName); run.session.Enqueue(simpleRequest);
                    var simpleResult = run.session.ApplyRequest(simpleRequest, run.alternate);
                    Require(simpleResult != null && simpleResult.status == "matched", "The manual-after-Simple precondition failed: " + simpleResult?.error);
                    run.session.SetFixtureRemap(SourceName, null);
                    var cleared = Map(run);
                    run.session.Process(false); FailedManual(run, run.alternate);
                    run.session.ForgetVerifiedSignature(); run.session.Process(); FailedManual(run, run.alternate);
                    Require(SameMap(cleared, Map(run)), "An old Simple report replayed a failed latest manual choice.");
                    run.session.SetFixtureRemap(SourceName, run.alternate); run.session.Process(false); Matched(run, run.alternate);
                    Require(CurrentReceipt(run, "matched").explicit_choice, "The old Simple report cleared the restored latest manual choice.");
                    Publish(run, Matches(run, run.first), new JArray()); run.session.Process(); Matched(run, run.first);
                    Require(!CurrentReceipt(run, "matched").explicit_choice, "The new report did not restore automatic binding intent.");
                    run.session.SetFixtureRemap(SourceName, null);
                    run.session.Process(false);
                    var automaticFailure = CurrentReceipt(run, "failed");
                    Require(!automaticFailure.explicit_choice && automaticFailure.material_guid == AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(run.first)) && !Map(run).ContainsKey(Key(SourceName)), "The automatic temporary-failure precondition is invalid.");
                    Evidence(run)["automatic_failure_receipt"] = JObject.FromObject(automaticFailure);
                    // A new event retries the unchanged valid report; no polling, new manifest or signature-forget helper is used here.
                    run.session.ProcessQueued(); Matched(run, run.first);
                    Require(!CurrentReceipt(run, "matched").explicit_choice, "Automatic report retry became a manual choice.");
                });
                Case(run, "fresh_simplified_then_original", () =>
                {
                    Publish(run, new JArray(), Simple()); run.session.Process();
                    Require(!Map(run).ContainsKey(Key(SourceName)), "A fresh Simplified export retained its owned remap.");
                    Require(!RendererUses(run.model, run.first) && !RendererUses(run.model, run.alternate), "Simplified renderers retained a copied target Material.");
                    var simple = CurrentReceipt(run, "failed");
                    Require(simple.error.StartsWith("Using Simplified:", StringComparison.Ordinal), "The Simplified receipt was not reported truthfully.");
                    Evidence(run)["simplified_receipt"] = JObject.FromObject(simple);
                    Publish(run, Matches(run, run.first), new JArray()); run.session.Process(); Matched(run, run.first);
                });
                Case(run, "foreign_remap_retained_for_simplified", () =>
                {
                    run.session.SetFixtureRemap(SourceName, run.alternate);
                    Publish(run, new JArray(), Simple()); run.session.Process();
                    Require(Map(run).TryGetValue(Key(SourceName), out var retained) && retained == run.alternate && RendererUses(run.model, run.alternate), "Simplified removed or replaced a user-changed remap.");
                    var review = CurrentReceipt(run, "failed");
                    Require(review.error.StartsWith("Review:", StringComparison.Ordinal), "A foreign remap did not request review.");
                    Evidence(run)["retained_receipt"] = JObject.FromObject(review);
                });
                Case(run, "missing_native_source_rejects_entire_map_batch", () =>
                {
                    var before = Map(run);
                    ((JArray)run.document["materials"]).Add(MissingSource);
                    var matches = Matches(run, run.first); matches.Add(Match(run, run.alternate, MissingSource));
                    Publish(run, matches, new JArray()); run.session.Process();
                    Require(SameMap(before, Map(run)) && !Map(run).ContainsKey(Key(MissingSource)), "Invalid source validation left a partial remap batch.");
                    var rejected = CurrentReceipt(run, "failed");
                    Require(rejected.material_guid == AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(run.first)) && rejected.error.Contains("source material"), "The missing native source did not reject the current valid binding batch.");
                    Evidence(run)["failure_receipt"] = JObject.FromObject(rejected);
                    var request = run.session.CreateRequest(MissingSource); run.session.Enqueue(request);
                    var result = run.session.ApplyRequest(request, run.first);
                    Require(result != null && result.status == "failed" && SameMap(before, Map(run)), "A request for a nonexistent native source altered a remap.");
                    CheckResult(run, request, "failed");
                    ((JArray)run.document["materials"]).Remove(((JArray)run.document["materials"]).First(value => value.Value<string>() == MissingSource));
                });
                Case(run, "missing_target_and_foreign_manifest_do_not_mutate", () =>
                {
                    var before = Map(run);
                    JObject missing = Match(run, run.first, SourceName); missing["material_guid"] = new string('0', 32);
                    Publish(run, new JArray(missing), new JArray()); run.session.Process();
                    Require(SameMap(before, Map(run)) && CurrentReceipt(run, "failed").material_guid == new string('0', 32), "A missing target changed the map or did not record this current failed target.");
                    Evidence(run)["missing_target_receipt"] = JObject.FromObject(run.session.Receipt(SourceName));
                    JObject foreign = Match(run, run.first, SourceName); foreign["export_asset_id"] = Guid.NewGuid().ToString("N");
                    Publish(run, new JArray(foreign), new JArray()); run.session.Process();
                    Require(SameMap(before, Map(run)), "A foreign export identity changed the fixture map.");
                    run.document["asset_id"] = Guid.NewGuid().ToString("N");
                    Publish(run, new JArray(), new JArray()); run.session.Process();
                    Require(SameMap(before, Map(run)), "A different profile changed the fixture map.");
                    var request = run.session.CreateRequest(SourceName); run.session.Enqueue(request);
                    var result = run.session.ApplyRequest(request, run.first);
                    Require(result != null && result.status == "failed" && result.error.Contains("export profile") && SameMap(before, Map(run)), "A request for a different export profile was not rejected before mutation.");
                    CheckResult(run, request, "failed");
                    run.document["asset_id"] = run.id;
                    Publish(run, Matches(run, run.first), new JArray()); run.session.Process(); Matched(run, run.first);
                });
                if (includeExportVectors) Case(run, "real_export_vectors_roundtrip", () => VerifyExportVectors(run, vectors));
                Case(run, "unrelated_model_preprocess_is_silent", () => VerifyUnrelatedPreprocess(run));
                }
            }
            catch (Exception error) { run.report["error"] = error.ToString(); }
            finally
            {
                try { run.session?.Dispose(); }
                catch (Exception error) { AddError(run, "Session recovery: " + error); }
                try
                {
                    if (!Selection.objects.SequenceEqual(selection)) Selection.objects = selection;
                    if (Selection.activeObject != activeSelection) Selection.activeObject = activeSelection;
                }
                catch (Exception error) { AddError(run, "Selection recovery: " + error); }
                try
                {
                    string after = SceneState();
                    run.report["scene_after"] = after;
                    bool scenesPreserved = scenes == after;
                    bool selectionPreserved = Selection.objects.SequenceEqual(selection) && Selection.activeObject == activeSelection;
                    bool originalsPreserved = originals != null && originals.All(Unchanged) && sourceRenderers == RendererIdentity(SourceModel);
                    bool materialsPreserved = fixtureMaterials != null && fixtureMaterials.All(Unchanged);
                    bool receiptsPreserved = receipts != null && SameSnapshot(receipts, ReceiptSnapshot(Path.Combine(root, ".codex", "character-material-matches"), id));
                    bool undoGroupPreserved = undoGroup == Undo.GetCurrentGroup() && undoName == Undo.GetCurrentGroupName();
                    NativeUndoState nativeAfter = nativeUndo ? CaptureNativeUndo() : null;
                    if (nativeAfter != null) run.report["native_undo_after"] = UndoEvidence(nativeAfter);
                    bool undoHistoryPreserved = !nativeUndo || nativeHistory != null && NativeHistoryPreserved(nativeHistory, nativeAfter, run.nativeUndoRecord);
                    bool vectorsPreserved = !includeExportVectors || vectorInputs != null && vectorInputs.All(UnchangedFile);
                    run.report["preservation"] = new JObject { ["scenes"] = scenesPreserved, ["selection"] = selectionPreserved,
                        ["original_assets_and_shared_materials"] = originalsPreserved, ["copied_material_contents"] = materialsPreserved,
                        ["other_profiles_receipts"] = receiptsPreserved, ["undo_group_and_name"] = undoGroupPreserved,
                        ["export_vector_inputs"] = vectorsPreserved,
                        ["undo_record_suppressed_for_exact_fixture"] = !nativeUndo, ["undo_full_history_verified"] = nativeUndo && undoHistoryPreserved,
                        ["original_undo_prefix_and_cursor_preserved"] = nativeUndo && undoHistoryPreserved };
                    run.report["original_file_stamps"] = originals == null ? new JArray() : new JArray(originals.Select(value => new JObject { ["path"] = value.path, ["exists"] = value.exists, ["sha256"] = value.hash, ["asset_dirty"] = value.assetDirty, ["importer_dirty"] = value.importerDirty }));
                    if (vectorInputs != null) run.report["export_vector_file_stamps"] = new JArray(vectorInputs.Select(value => new JObject { ["path"] = value.path, ["sha256"] = value.hash }));
                    if (!scenesPreserved || !selectionPreserved || !originalsPreserved || !materialsPreserved || !receiptsPreserved || (nativeUndo ? !undoHistoryPreserved : !undoGroupPreserved) || !vectorsPreserved)
                        AddError(run, "Scene, selection, asset, Material, receipt or Undo-group preservation failed. No production restoration was attempted.");
                    run.report["passed"] = run.report["error"] == null && ((JArray)run.report["cases"]).Count == (nativeUndo ? 1 : includeExportVectors ? 11 : 10) && ((JArray)run.report["cases"]).All(value => value["passed"].Value<bool>());
                }
                catch (Exception error) { AddError(run, "Preservation check: " + error); }
                try
                {
                    Directory.CreateDirectory(run.directory);
                    string path = Path.Combine(run.directory, "report.json");
                    File.WriteAllText(path, run.report.ToString(), new UTF8Encoding(false));
                    if (run.report["passed"].Value<bool>()) Debug.Log(nativeUndo ? "Character material native Undo/Redo verification passed. Original history was preserved with one owned fixture snapshot: " + path : "Character material isolated verification passed. Native Undo/UI and full authored-model roundtrip acceptance remain separate: " + path);
                    else Debug.LogError("Character material isolated verification failed: " + path + "\n" + run.report["error"]);
                }
                finally { running = false; }
            }
        }

        static void Case(Run run, string name, Action action)
        {
            var evidence = new JObject { ["name"] = name, ["passed"] = false };
            ((JArray)run.report["cases"]).Add(evidence);
            try { action(); evidence["passed"] = true; }
            catch (Exception error) { evidence["error"] = error.Message; throw; }
        }
        static JObject Evidence(Run run) => (JObject)((JArray)run.report["cases"]).Last;
        static NativeUndoState CaptureNativeUndo()
        {
            const BindingFlags flags = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
            MethodInfo records = typeof(Undo).GetMethod("GetRecords", flags, null, new[] { typeof(List<string>), typeof(int).MakeByRefType() }, null);
            MethodInfo redo = typeof(Undo).GetMethod("HasRedo", flags, null, Type.EmptyTypes, null);
            Require(records != null && records.ReturnType == typeof(void) && redo != null && redo.ReturnType == typeof(bool), "The pinned native Undo inspection API is unavailable; no Undo is permitted.");
            var values = new List<string>(); object[] args = { values, 0 };
            records.Invoke(null, args);
            Require(values.Count <= 8192 && values.All(value => value != null && value.Length <= 65536) && values.Sum(value => (long)value.Length) <= 8 * 1024 * 1024, "The native Undo history exceeds the bounded acceptance limit.");
            return new NativeUndoState { records = values, cursor = (int)args[1], redo = (bool)redo.Invoke(null, null), group = Undo.GetCurrentGroup() };
        }
        static JObject UndoEvidence(NativeUndoState state) => new JObject { ["records"] = new JArray(state.records), ["cursor"] = state.cursor, ["group"] = state.group, ["has_redo"] = state.redo };
        static bool PrefixEqual(NativeUndoState before, NativeUndoState after) => after.records.Count >= before.records.Count && before.records.SequenceEqual(after.records.Take(before.records.Count), StringComparer.Ordinal);
        static bool NativeHistoryPreserved(NativeUndoState before, NativeUndoState after, string ownRecord)
        {
            int added = ownRecord == null ? 0 : 1;
            return !before.redo && before.cursor == before.records.Count && !after.redo && after.cursor == before.cursor + added &&
                after.records.Count == before.records.Count + added && PrefixEqual(before, after) && (ownRecord == null || after.records.Last() == ownRecord);
        }
        static string NativeSnapshotIdentity(Run run, Object value)
        {
            Require(System.Threading.Thread.CurrentThread.ManagedThreadId == run.nativeThreadId && CharacterMaterialMatching.MainEditor && !CharacterMaterialMatching.EditorBusy,
                "Read native snapshot identities only on the owned Editor callback thread while idle.");
            MethodInfo legacy = typeof(Object).GetMethod("GetInstanceID", BindingFlags.Instance | BindingFlags.Public, null, Type.EmptyTypes, null);
            Require(value != null && legacy != null && legacy.DeclaringType == typeof(Object) && legacy.ReturnType == typeof(int) && !legacy.IsGenericMethod,
                "The pinned native Undo legacy instance-ID API is unavailable; no Undo is permitted.");
            int identity = (int)legacy.Invoke(value, null);
            Require(identity != 0, "The fixture has no valid native snapshot identity.");
            return identity.ToString(System.Globalization.CultureInfo.InvariantCulture);
        }
        static void RequireOwnedRecord(Run run, NativeUndoState before, NativeUndoState current, string ownRecord, string importerId, string importerEntityId, bool undone)
        {
            Require(current.records.Count == before.records.Count + 1 && PrefixEqual(before, current) && current.records.Last() == ownRecord &&
                current.cursor == before.cursor + (undone ? 0 : 1) && current.redo == undone, "The newest Undo/Redo operation is no longer this fixture alone; no foreign history is touched.");
            string normalized = ownRecord.Replace("\r\n", "\n");
            Require(Regex.IsMatch(normalized, @"\AObject Changed \(1\)\n[ \t]+Match Unity Material: \[[ \t]*0\] [^\r\n]*\((?:FBXImporter|ModelImporter)\) \[ID: " + Regex.Escape(importerId) + @"\]\n?\z"), "The native snapshot does not contain exactly one owned FBX importer.");
            var importer = AssetImporter.GetAtPath(run.model) as ModelImporter;
            Require(importer != null && importer.assetPath == run.model && NativeSnapshotIdentity(run, importer) == importerId && ObjectIdentity(importer) == importerEntityId,
                "The native Undo target is no longer the exact fixture importer with both original identities.");
        }
        static void VerifyNativeUndo(Run run, NativeUndoState original)
        {
            run.session.ValidateSource(SourceName);
            Publish(run, Matches(run, run.first), new JArray()); run.session.Process(); Matched(run, run.first);
            NativeUndoState before = CaptureNativeUndo();
            Require(NativeHistoryPreserved(original, before, null), "Fixture preparation changed user Undo history; no native Undo is permitted.");
            var importer = AssetImporter.GetAtPath(run.model) as ModelImporter;
            Require(importer != null && !EditorUtility.IsDirty(importer) && run.first != run.alternate, "The owned importer has pending edits or no distinct target.");
            string importerId = NativeSnapshotIdentity(run, importer), importerEntityId = ObjectIdentity(importer), modelGuid = AssetDatabase.AssetPathToGUID(run.model);
            string metaPath = Full(run, run.model) + ".meta", beforeMeta = Hash(metaPath), fbxHash = Hash(Full(run, run.model)), manifestHash = Hash(Full(run, run.manifest));
            var mapA = Map(run);
            Evidence(run)["initial_a"] = new JObject { ["map_target"] = AssetDatabase.GetAssetPath(run.first), ["renderer_uses_a"] = RendererUses(run.model, run.first), ["meta_sha256"] = beforeMeta };
            Undo.IncrementCurrentGroup(); int ownGroup = Undo.GetCurrentGroup();
            Require(ownGroup > before.group, "A separate owned Undo group was not established.");
            string ownRecord = null;
            bool undoAttempted = false, redone = false;
            try
            {
                run.session.EnableNativeUndo();
                CharacterMaterialMatching.Record result;
                CharacterMaterialMatching.Request request = null;
                try
                {
                    request = run.session.CreateRequest(SourceName); run.session.Enqueue(request);
                    result = run.session.ApplyRequest(request, run.alternate);
                }
                finally { run.session.DisableNativeUndo(); }
                NativeUndoState applied = CaptureNativeUndo();
                Evidence(run)["native_applied"] = UndoEvidence(applied);
                Evidence(run)["apply_result"] = result == null ? null : JObject.FromObject(result);
                Evidence(run)["owned_importer_id"] = importerId; Evidence(run)["owned_importer_entity_id"] = importerEntityId; Evidence(run)["owned_group"] = ownGroup;
                Require(applied.records.Count == before.records.Count + 1 && applied.group == ownGroup && Undo.GetCurrentGroupName() == "Match Unity Material", "Apply did not append one snapshot in the separately owned group.");
                ownRecord = applied.records.Last(); RequireOwnedRecord(run, before, applied, ownRecord, importerId, importerEntityId, false);
                run.nativeUndoRecord = ownRecord;
                Require(result != null && result.status == "matched", "The production Apply did not match Material B: " + result?.error);
                CheckResult(run, request, "matched"); Matched(run, run.alternate);
                var mapB = Map(run); string matchedMeta = Hash(metaPath);
                Require(matchedMeta != beforeMeta, "Material A to B did not change the owned importer metadata.");
                Require(CharacterMaterialMatching.MainEditor && !CharacterMaterialMatching.EditorBusy, "The Editor is not ready for owned Undo.");
                RequireOwnedRecord(run, before, CaptureNativeUndo(), ownRecord, importerId, importerEntityId, false);
                undoAttempted = true; Undo.PerformUndo();
                NativeUndoState undone = CaptureNativeUndo(); RequireOwnedRecord(run, before, undone, ownRecord, importerId, importerEntityId, true);
                // Observe native state before receipt verification. Never save or manually reimport to conceal a failed Undo.
                bool undoMap = SameMap(mapA, Map(run)); bool undoDisk = Hash(metaPath) == beforeMeta;
                bool undoRenderer = RendererUses(run.model, run.first) && !RendererUses(run.model, run.alternate);
                Evidence(run)["native_undone"] = UndoEvidence(undone);
                Evidence(run)["undo_state"] = new JObject { ["map_a"] = undoMap, ["disk_meta_a"] = undoDisk, ["renderer_a"] = undoRenderer, ["meta_sha256"] = Hash(metaPath) };
                var undoneMap = Map(run); string undoneMeta = Hash(metaPath);
                run.session.Process(false); FailedManual(run, run.alternate);
                Require(SameMap(undoneMap, Map(run)) && Hash(metaPath) == undoneMeta, "Verification changed the native Undo result or replayed a pairing.");
                Require(undoMap && undoDisk && undoRenderer, "Native Undo did not restore map, disk metadata and loaded renderer A independently.");
                Require(CharacterMaterialMatching.MainEditor && !CharacterMaterialMatching.EditorBusy, "The Editor is not ready for owned Redo.");
                RequireOwnedRecord(run, before, CaptureNativeUndo(), ownRecord, importerId, importerEntityId, true);
                Undo.PerformRedo(); redone = true;
                NativeUndoState restored = CaptureNativeUndo(); RequireOwnedRecord(run, before, restored, ownRecord, importerId, importerEntityId, false);
                bool redoMap = SameMap(mapB, Map(run)); bool redoDisk = Hash(metaPath) == matchedMeta;
                bool redoRenderer = RendererUses(run.model, run.alternate) && !RendererUses(run.model, run.first);
                Evidence(run)["native_redone"] = UndoEvidence(restored);
                Evidence(run)["redo_state"] = new JObject { ["map_b"] = redoMap, ["disk_meta_b"] = redoDisk, ["renderer_b"] = redoRenderer, ["meta_sha256"] = Hash(metaPath) };
                var restoredMap = Map(run); string restoredMeta = Hash(metaPath);
                run.session.Process(false); Matched(run, run.alternate);
                Require(SameMap(restoredMap, Map(run)) && Hash(metaPath) == restoredMeta, "Verification changed the native Redo result.");
                Require(redoMap && redoDisk && redoRenderer && modelGuid == AssetDatabase.AssetPathToGUID(run.model) && fbxHash == Hash(Full(run, run.model)) && manifestHash == Hash(Full(run, run.manifest)), "Native Redo changed the fixture identity/publication or failed to restore B.");
                run.report["undo_native_verified"] = true;
            }
            finally
            {
                run.session.DisableNativeUndo();
                if (undoAttempted && !redone && ownRecord != null)
                {
                    try
                    {
                        NativeUndoState recovery = CaptureNativeUndo();
                        RequireOwnedRecord(run, before, recovery, ownRecord, importerId, importerEntityId, true);
                        Require(CharacterMaterialMatching.MainEditor && !CharacterMaterialMatching.EditorBusy, "The Editor is not ready for owned Redo recovery.");
                        Undo.PerformRedo();
                        RequireOwnedRecord(run, before, CaptureNativeUndo(), ownRecord, importerId, importerEntityId, false);
                        Evidence(run)["owned_redo_recovery"] = true;
                        var recoveryMap = Map(run); string recoveryMeta = Hash(metaPath);
                        run.session.Process(false);
                        Evidence(run)["owned_redo_recovery_receipt"] = run.session.Receipt(SourceName)?.status;
                        Require(SameMap(recoveryMap, Map(run)) && Hash(metaPath) == recoveryMeta, "Receipt verification changed the owned Redo recovery result.");
                    }
                    catch (Exception error) { Evidence(run)["owned_redo_recovery_error"] = error.Message; }
                }
                // Seal the own snapshot without changing or collapsing any previous user operation.
                Undo.IncrementCurrentGroup();
            }
        }
        static void VerifyUnrelatedPreprocess(Run run)
        {
            string expected = "Assets/~QA_MaterialMatching_" + run.id + "/Fixture.fbx";
            Require(run.model == expected && run.session != null, "The Preprocess regression needs this exact private fixture session.");
            string noReport = run.folder + "/NoReport.fbx";
            Require(!File.Exists(Full(run, noReport)) && !File.Exists(Full(run, noReport) + ".meta") && !File.Exists(Path.ChangeExtension(Full(run, noReport), ".cdesigner.json")), "The new no-report fixture already exists.");
            Require(AssetDatabase.CopyAsset(run.model, noReport), "Could not create the private no-report FBX fixture.");
            Require(File.Exists(Full(run, noReport)) && !File.Exists(Path.ChangeExtension(Full(run, noReport), ".cdesigner.json")), "The no-report fixture is missing or has an unexpected report.");
            // Release the isolation gate first: otherwise foreign paths return before the defective manifest-path guard and the regression would falsely pass.
            run.session.Dispose();
            var importer = AssetImporter.GetAtPath(run.model) as ModelImporter;
            Require(importer != null && importer.assetPath == run.model && !EditorUtility.IsDirty(importer), "The owned fixture importer is unavailable for the direct callback regression.");
            var before = Map(run);
            var calls = new JArray(); var logs = new JArray();
            Evidence(run)["calls"] = calls; Evidence(run)["unexpected_logs"] = logs;
            int unexpectedLogCount = 0;
            Application.LogCallback listener = (message, stack, type) =>
            {
                if (type != LogType.Warning && type != LogType.Error && type != LogType.Exception && type != LogType.Assert) return;
                unexpectedLogCount++;
                if (logs.Count < 16) logs.Add(new JObject { ["type"] = type.ToString(), ["message"] = Limit(message, 4096), ["stack"] = Limit(stack, 1024) });
            };
            Application.logMessageReceived += listener;
            try
            {
                foreach (string path in new[] { run.folder + "/NotCharacter.obj", "Packages/com.character-designer.verification/NotCharacter.fbx", noReport, "Assets/../NotCharacter.fbx" })
                {
                    var call = new JObject { ["model_path"] = path, ["importer_path"] = run.model, ["returned_without_throw"] = false };
                    calls.Add(call);
                    CharacterMaterialMatching.Preprocess(path, importer);
                    call["returned_without_throw"] = true;
                    Require(SameMap(before, Map(run)), "An unrelated Preprocess input changed the owned fixture's remap: " + path);
                    call["map_unchanged"] = true;
                }
            }
            finally { Application.logMessageReceived -= listener; Evidence(run)["unexpected_log_count"] = unexpectedLogCount; }
            Require(unexpectedLogCount == 0, "An unrelated Preprocess input emitted a new warning, error, assertion or exception log.");
            Require(calls.Count == 4 && SameMap(before, Map(run)), "The four unrelated model callbacks did not preserve the native remap.");
            Evidence(run)["isolation_session_released_before_callbacks"] = true;
            Evidence(run)["no_report_fixture"] = noReport;
        }
        static string Limit(string value, int length) => string.IsNullOrEmpty(value) || value.Length <= length ? value : value.Substring(0, length);
        static void Publish(Run run, JArray matches, JArray simple)
        {
            run.document["unity_material_matches"] = matches;
            run.document["simple_materials"] = simple;
            run.document["verification_revision"] = ++run.revision;
            File.WriteAllText(Full(run, run.manifest), run.document.ToString(), new UTF8Encoding(false));
            AssetDatabase.ImportAsset(run.manifest, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
        }
        static ExportVector[] ReadExportVectors(Run run, out string catalog)
        {
            string directory = Full(run, ".codex/task-staging/character2-material-match-20261010/roundtrip-export-vectors");
            catalog = Path.Combine(directory, "vectors.json");
            ValidateVectorFile(directory, catalog, 1024 * 1024);
            Require(Hash(catalog) == VectorCatalogHash, "The exact task-owned export catalog changed; review the new artifact before native verification.");
            var json = JObject.Parse(File.ReadAllText(catalog));
            Require(json["schema"]?.Value<string>() == "cdesigner.material-match-real-export-vectors/1" && json["passed"]?.Value<bool>() == true &&
                json["version"]?.Value<string>() == "0.77.20" && SamePath(json["projection"]?.Value<string>(), Full(run, ".codex/task-staging/character2-material-match-20261010/release/addons")), "The export catalog is not the completed task-owned release projection.");
            var recipe = json["saved_recipe"] as JObject;
            Require(recipe != null && recipe["source_material"]?.Value<string>() == SourceName && recipe["export_asset_id"]?.Value<string>() == new string('a', 32) &&
                recipe["material_guid"]?.Value<string>() == new string('b', 32) && recipe["material_local_id"]?.Value<long>() == 2100000 &&
                SamePath(recipe["project_root"]?.Value<string>(), Path.Combine(directory, "FakeUnityProject")), "The vector recipe identity is unexpected.");
            string[] stages = { "01-original", "02-paired-changed-model", "03-simplified", "04-original-restored" };
            var records = json["records"] as JArray;
            Require(records != null && records.Count == stages.Length, "The export catalog must contain exactly the four reviewed stages.");
            var result = new ExportVector[stages.Length];
            for (int i = 0; i < result.Length; i++)
            {
                var record = records[i] as JObject;
                string fbx = Path.Combine(directory, "versions", stages[i], "Cosha.fbx");
                string manifest = Path.ChangeExtension(fbx, ".cdesigner.json");
                Require(record != null && record["stage"]?.Value<string>() == stages[i] && SamePath(record["fbx"]?.Value<string>(), fbx) && SamePath(record["report"]?.Value<string>(), manifest), "A vector stage or source path is outside the exact reviewed set.");
                ValidateVectorFile(directory, fbx, 16 * 1024 * 1024); ValidateVectorFile(directory, manifest, 1024 * 1024);
                string fbxHash = Hash(fbx), manifestHash = Hash(manifest);
                Require(fbxHash == record["fbx_sha256"]?.Value<string>() && manifestHash == record["manifest_sha256"]?.Value<string>(), "A vector file does not match its catalog checksum.");
                var document = JObject.Parse(File.ReadAllText(manifest));
                Require(document["schema"]?.Value<string>() == "cdesigner.unity-character.v1" && document["ok"]?.Value<bool>() == true && document["filename"]?.Value<string>() == "Cosha.fbx" &&
                    document["asset_id"]?.Value<string>() == recipe["export_asset_id"].Value<string>() && document["files"]?["Cosha.fbx"]?.Value<string>() == fbxHash &&
                    document["materials"] is JArray materials && materials.Count == 1 && materials[0].Value<string>() == SourceName, "The vector report has no exact owned model/source identity.");
                foreach (string field in new[] { "materials", "simple_materials", "unity_material_matches" })
                    Require(JToken.DeepEquals(document[field], record[field]), "The vector catalog and report disagree on " + field + ".");
                var matches = document["unity_material_matches"] as JArray; var simple = document["simple_materials"] as JArray;
                Require(matches != null && simple != null, "The vector report has no explicit match/Simple arrays.");
                if (i == 1 || i == 3)
                {
                    Require(matches.Count == 1 && simple.Count == 0, "An Original paired vector has an unexpected recipe.");
                    foreach (string field in new[] { "source_material", "material_guid", "material_local_id", "project_root", "export_asset_id" })
                        Require(matches[0][field]?.Value<string>() == recipe[field]?.Value<string>(), "A paired vector changed the saved recipe identity.");
                }
                else if (i == 2) Require(matches.Count == 0 && simple.Count == 1 && simple[0]["material"]?.Value<string>() == SourceName && simple[0]["method"]?.Value<string>() == "simple_principled", "The real Simplified vector is not an export-only simple_principled publication.");
                else Require(matches.Count == 0 && simple.Count == 0, "The initial Original vector unexpectedly contains a pairing or Simplified recipe.");
                result[i] = new ExportVector { stage = stages[i], fbx = fbx, manifest = manifest, fbxHash = fbxHash, manifestHash = manifestHash, document = document };
            }
            Require(result[0].fbxHash != result[1].fbxHash, "The changed-model vector did not change its FBX bytes.");
            return result;
        }
        static void ValidateVectorFile(string directory, string path, long limit)
        {
            string prefix = Path.GetFullPath(directory).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
            Require(Path.GetFullPath(path).StartsWith(prefix, StringComparison.OrdinalIgnoreCase) && File.Exists(path), "An exact vector input is missing or outside its owned directory.");
            var file = new FileInfo(path);
            Require(file.Length > 0 && file.Length <= limit && (file.Attributes & FileAttributes.ReparsePoint) == 0, "The vector input is empty, oversized or redirected.");
            for (var parent = file.Directory; parent != null && (SamePath(parent.FullName, directory) || parent.FullName.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)); parent = parent.Parent)
                Require((parent.Attributes & FileAttributes.ReparsePoint) == 0, "The vector input directory is redirected.");
        }
        static void VerifyExportVectors(Run run, ExportVector[] vectors)
        {
            Require(vectors != null && vectors.Length == 4, "The optional export vectors were not validated before fixture creation.");
            var importer = AssetImporter.GetAtPath(run.model) as ModelImporter;
            Require(importer != null && run.model == "Assets/~QA_MaterialMatching_" + run.id + "/Fixture.fbx", "The vector target is not this exact private fixture.");
            // The factory vectors contain one Root bone, so their own private importer must not inherit Cosha's Humanoid requirements.
            importer.animationType = ModelImporterAnimationType.Generic;
            importer.avatarSetup = ModelImporterAvatarSetup.NoAvatar;
            importer.sourceAvatar = null;
            importer.importAnimation = false;
            importer.isReadable = true;
            importer.SaveAndReimport();
            run.session.SetFixtureRemap(SourceName, null);
            var stages = new JArray(); Evidence(run)["stages"] = stages;
            CopyVectorModel(run, vectors[0]); PublishVector(run, vectors[0], new JArray()); run.session.Process();
            run.session.ValidateSource(SourceName);
            var request = run.session.CreateRequest(SourceName); run.session.Enqueue(request);
            var result = run.session.ApplyRequest(request, run.first);
            Require(result != null && result.status == "matched", "The real Original vector could not acquire its initial native pairing: " + result?.error);
            CheckResult(run, request, "matched"); Matched(run, run.first);
            JObject originalGeometry = Geometry(run.model);
            stages.Add(VectorEvidence(run, vectors[0], originalGeometry, "matched"));
            JObject nativeBinding = Match(run, run.first, SourceName);
            string modelGuid = AssetDatabase.AssetPathToGUID(run.model);

            // A different native target makes a false-positive defer check impossible even when the saved pairing is unchanged.
            run.document["unity_material_matches"] = Matches(run, run.alternate);
            run.document["verification_revision"] = ++run.revision;
            File.WriteAllText(Full(run, run.manifest), run.document.ToString(), new UTF8Encoding(false));
            var before = Map(run);
            string receiptPath = Path.Combine(run.session.StorePath, "receipts", run.id, HashText(SourceName) + ".json");
            string receiptBefore = Hash(receiptPath);
            CopyVectorModel(run, vectors[1]); run.session.ProcessQueued();
            Require(SameMap(before, Map(run)) && !RendererUses(run.model, run.alternate) && Hash(receiptPath) == receiptBefore, "The new FBX applied a stale report or rewrote the prior verified receipt before publication completed.");
            JObject changedGeometry = Geometry(run.model);
            Require(!JToken.DeepEquals(originalGeometry, changedGeometry), "The native imported mesh did not reflect the real exporter geometry change.");
            string staleManifestHash = Hash(Full(run, run.manifest));
            PublishVector(run, vectors[1], new JArray(nativeBinding.DeepClone())); run.session.Process(); Matched(run, run.first);
            var changed = VectorEvidence(run, vectors[1], changedGeometry, "deferred_then_matched");
            changed["old_report_deferred"] = true; changed["prior_receipt_bytes_preserved"] = true;
            changed["deferred_manifest_sha256"] = staleManifestHash; changed["prior_receipt_sha256"] = receiptBefore; stages.Add(changed);

            CopyVectorModel(run, vectors[2]); PublishVector(run, vectors[2], new JArray()); run.session.Process();
            run.session.ValidateSource(SourceName);
            Require(!Map(run).ContainsKey(Key(SourceName)) && !RendererUses(run.model, run.first) && !RendererUses(run.model, run.alternate), "The real Simplified FBX retained its owned external Material.");
            var simplified = CurrentReceipt(run, "failed");
            Require(simplified.error.StartsWith("Using Simplified:", StringComparison.Ordinal), "The real Simplified publication did not record its cleared ownership truthfully.");
            stages.Add(VectorEvidence(run, vectors[2], Geometry(run.model), "using_simplified"));

            CopyVectorModel(run, vectors[3]); PublishVector(run, vectors[3], new JArray(nativeBinding.DeepClone())); run.session.Process(); Matched(run, run.first);
            Require(modelGuid == AssetDatabase.AssetPathToGUID(run.model), "The vector replacement changed the private FBX GUID.");
            Require(JToken.DeepEquals(changedGeometry, Geometry(run.model)), "The Original restore changed the factory vector's already-edited mesh unexpectedly.");
            var restored = VectorEvidence(run, vectors[3], Geometry(run.model), "matched");
            restored["saved_native_binding_reused"] = nativeBinding.DeepClone(); restored["new_manual_request"] = false; stages.Add(restored);
        }
        static void CopyVectorModel(Run run, ExportVector vector)
        {
            Require(run.model == "Assets/~QA_MaterialMatching_" + run.id + "/Fixture.fbx" && File.Exists(Full(run, run.model) + ".meta") && Hash(vector.fbx) == vector.fbxHash && Hash(vector.manifest) == vector.manifestHash, "The private fixture or reviewed vector input changed.");
            File.Copy(vector.fbx, Full(run, run.model), true);
            AssetDatabase.ImportAsset(run.model, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
            Require(Hash(Full(run, run.model)) == vector.fbxHash, "The fixture did not receive the exact real FBX bytes.");
        }
        static void PublishVector(Run run, ExportVector vector, JArray nativeMatches)
        {
            // Preserve the real source inventory and Simplified summary, but replace every fake project/model/Material identity.
            run.document = new JObject { ["schema"] = "cdesigner.unity-character.v1", ["ok"] = true, ["asset_id"] = run.id,
                ["filename"] = "Fixture.fbx", ["files"] = new JObject { ["Fixture.fbx"] = vector.fbxHash },
                ["materials"] = vector.document["materials"].DeepClone(), ["verification_export_stage"] = vector.stage };
            Publish(run, nativeMatches, (JArray)vector.document["simple_materials"].DeepClone());
        }
        static JObject VectorEvidence(Run run, ExportVector vector, JObject geometry, string status)
            => new JObject { ["stage"] = vector.stage, ["source_fbx"] = vector.fbx, ["source_manifest"] = vector.manifest,
                ["fbx_sha256"] = vector.fbxHash, ["source_manifest_sha256"] = vector.manifestHash,
                ["fixture_manifest_sha256"] = Hash(Full(run, run.manifest)), ["geometry"] = geometry,
                ["native_source_inventory"] = NativeInventory(run.model), ["status"] = status,
                ["receipt"] = JObject.FromObject(run.session.Receipt(SourceName)) };
        static JObject Geometry(string model)
        {
            var meshes = AssetDatabase.LoadAllAssetsAtPath(model).OfType<Mesh>().OrderBy(mesh => mesh.name, StringComparer.Ordinal).ToArray();
            Require(meshes.Length > 0 && meshes.Length <= 8 && meshes.All(mesh => mesh.isReadable && mesh.vertexCount >= 4 && mesh.vertexCount <= 128), "The real exporter test body has no bounded readable native mesh.");
            return new JObject { ["meshes"] = new JArray(meshes.Select(mesh => new JObject { ["name"] = mesh.name,
                ["vertices"] = new JArray(mesh.vertices.Select(value => new JArray(Math.Round(value.x, 6), Math.Round(value.y, 6), Math.Round(value.z, 6)))),
                ["submesh_indices"] = new JArray(Enumerable.Range(0, mesh.subMeshCount).Select(index => new JArray(mesh.GetIndices(index)))) })) };
        }
        static Stamp CaptureFile(string path) => new Stamp { path = path, exists = File.Exists(path), hash = File.Exists(path) ? Hash(path) : "" };
        static bool UnchangedFile(Stamp value) => value.exists == File.Exists(value.path) && value.hash == (File.Exists(value.path) ? Hash(value.path) : "");
        static bool SamePath(string first, string second) => !string.IsNullOrEmpty(first) && !string.IsNullOrEmpty(second) &&
            string.Equals(Path.GetFullPath(first).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar), Path.GetFullPath(second).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar), StringComparison.OrdinalIgnoreCase);
        static string HashText(string value)
        {
            using (var digest = SHA256.Create()) return BitConverter.ToString(digest.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-", "").ToLowerInvariant();
        }
        static JArray Simple() => new JArray(new JObject { ["material"] = SourceName });
        static JArray Matches(Run run, Material material) => new JArray(Match(run, material, SourceName));
        static JObject Match(Run run, Material material, string source)
        {
            Require(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(material, out string guid, out long local), "A copied Material has no persistent identity.");
            return new JObject { ["source_material"] = source, ["material_guid"] = guid, ["material_local_id"] = local, ["project_root"] = run.root, ["export_asset_id"] = run.id };
        }
        static void Matched(Run run, Material material)
        {
            Require(Map(run).TryGetValue(Key(SourceName), out var mapped) && mapped == material && RendererUses(run.model, material), "The exact remap and imported renderer do not agree.");
            var receipt = CurrentReceipt(run, "matched");
            Require(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(material, out string guid, out long local), "The copied Material lost its identity.");
            Require(receipt.material_guid == guid && receipt.material_local_id == local, "The receipt does not describe the verified exact Material.");
            Evidence(run)["matched_receipt"] = JObject.FromObject(receipt);
        }
        static CharacterMaterialMatching.Record CurrentReceipt(Run run, string status)
        {
            var receipt = run.session.Receipt(SourceName);
            Require(receipt != null && receipt.schema == CharacterMaterialMatching.ReceiptSchema && receipt.status == status &&
                receipt.project_root == run.root && receipt.export_asset_id == run.id && receipt.model_path == run.model && receipt.source_material == SourceName &&
                receipt.fbx_sha256 == Hash(Full(run, run.model)) && receipt.manifest_sha256 == Hash(Full(run, run.manifest)), "The receipt does not identify this verified current publication and status.");
            return receipt;
        }
        static void FailedManual(Run run, Material wanted)
        {
            var receipt = CurrentReceipt(run, "failed");
            Require(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(wanted, out string guid, out long local), "The intended fixture Material lost its persistent identity.");
            Require(receipt.explicit_choice && receipt.material_guid == guid && receipt.material_local_id == local && receipt.error.Contains("exact source material remap"), "Failed verification lost the latest manual identity or its mismatch reason.");
            if (Evidence(run)["failed_manual_receipts"] == null) Evidence(run)["failed_manual_receipts"] = new JArray();
            ((JArray)Evidence(run)["failed_manual_receipts"]).Add(JObject.FromObject(receipt));
        }
        static void CheckResult(Run run, CharacterMaterialMatching.Request request, string status)
        {
            var result = run.session.Result(request);
            Require(result != null && result.schema == CharacterMaterialMatching.ResultSchema && result.status == status && result.request_id == request.request_id &&
                result.created_unix == request.created_unix && result.project_root == run.root && result.model_path == run.model &&
                result.export_asset_id == run.id && result.source_material == request.source_material, "The request did not publish its complete expected terminal identity.");
            if (Evidence(run)["results"] == null) Evidence(run)["results"] = new JArray();
            ((JArray)Evidence(run)["results"]).Add(JObject.FromObject(result));
        }
        static AssetImporter.SourceAssetIdentifier Key(string source) => new AssetImporter.SourceAssetIdentifier(typeof(Material), source);
        static Dictionary<AssetImporter.SourceAssetIdentifier, Object> Map(Run run)
        {
            var importer = AssetImporter.GetAtPath(run.model) as ModelImporter;
            Require(importer != null, "The fixture importer is missing.");
            return importer.GetExternalObjectMap();
        }
        static bool SameMap(Dictionary<AssetImporter.SourceAssetIdentifier, Object> first, Dictionary<AssetImporter.SourceAssetIdentifier, Object> second)
            => first.Count == second.Count && first.All(pair => second.TryGetValue(pair.Key, out var value) && value == pair.Value);
        static bool RendererUses(string model, Material material)
        {
            var asset = AssetDatabase.LoadAssetAtPath<GameObject>(model);
            return asset != null && asset.GetComponentsInChildren<Renderer>(true).Any(renderer => renderer.sharedMaterials.Contains(material));
        }
        static JArray NativeInventory(string model)
        {
            var result = new JArray();
            var serialized = new SerializedObject(AssetImporter.GetAtPath(model));
            var materials = serialized.FindProperty("m_Materials");
            Require(materials != null && materials.isArray, "Unity m_Materials is unavailable.");
            for (int i = 0; i < materials.arraySize; i++)
            {
                var item = materials.GetArrayElementAtIndex(i);
                result.Add(new JObject { ["name"] = item.FindPropertyRelative("name")?.stringValue,
                    ["type"] = item.FindPropertyRelative("type")?.stringValue, ["assembly"] = item.FindPropertyRelative("assembly")?.stringValue });
            }
            return result;
        }
        static List<Stamp> CaptureOriginals(IEnumerable<string> known)
        {
            string[] fixedPaths = { SourceModel, SourceMaterial, Path.ChangeExtension(SourceModel, ".cdesigner.json"),
                "Assets/Art/Character/Cosha/Cosha.Runtime.prefab", "Assets/Prefabs/Characters/Cosha/Cosha.Player.prefab" };
            var paths = new HashSet<string>(fixedPaths.Concat(known), StringComparer.Ordinal);
            foreach (string model in known.Where(value => value.EndsWith(".fbx", StringComparison.OrdinalIgnoreCase))) paths.Add(Path.ChangeExtension(model, ".cdesigner.json"));
            foreach (string path in AssetDatabase.GetDependencies(fixedPaths.Where(File.Exists).ToArray(), true))
                if (path.StartsWith("Assets/", StringComparison.Ordinal) && File.Exists(path)) paths.Add(path);
            foreach (string path in paths.ToArray()) paths.Add(path + ".meta");
            foreach (string path in LoadedScenePaths()) paths.Add(path);
            Require(paths.Count <= 4096, "The protected asset set is too large for this bounded acceptance run.");
            return paths.OrderBy(path => path, StringComparer.Ordinal).Select(Capture).ToList();
        }
        static Stamp Capture(string path)
        {
            var asset = AssetDatabase.LoadMainAssetAtPath(path);
            var importer = AssetImporter.GetAtPath(path);
            return new Stamp { path = path, exists = File.Exists(path), hash = File.Exists(path) ? Hash(path) : "", assetDirty = asset != null && EditorUtility.IsDirty(asset), importerDirty = importer != null && EditorUtility.IsDirty(importer) };
        }
        static bool Unchanged(Stamp before)
        {
            Stamp after = Capture(before.path);
            return before.exists == after.exists && before.hash == after.hash && before.assetDirty == after.assetDirty && before.importerDirty == after.importerDirty;
        }
        static Dictionary<string, string> ReceiptSnapshot(string store, string ownProfile)
        {
            string directory = Path.Combine(store, "receipts");
            if (!Directory.Exists(directory)) return new Dictionary<string, string>(StringComparer.Ordinal);
            string excluded = Path.Combine(directory, ownProfile) + Path.DirectorySeparatorChar;
            string[] paths = Directory.GetFiles(directory, "*.json", SearchOption.AllDirectories).Where(path => !path.StartsWith(excluded, StringComparison.OrdinalIgnoreCase)).ToArray();
            Require(paths.Length <= 8192, "The protected receipt set is too large for this bounded acceptance run.");
            return paths.ToDictionary(path => path, Hash, StringComparer.Ordinal);
        }
        static bool SameSnapshot(Dictionary<string, string> first, Dictionary<string, string> second)
            => first.Count == second.Count && first.All(pair => second.TryGetValue(pair.Key, out string hash) && hash == pair.Value);
        static string RendererIdentity(string model)
        {
            var asset = AssetDatabase.LoadAssetAtPath<GameObject>(model);
            Require(asset != null, "The protected source model is unavailable.");
            return string.Join("\n", asset.GetComponentsInChildren<Renderer>(true).Select(renderer => ObjectIdentity(renderer) + ":" +
                string.Join(",", renderer.sharedMaterials.Select(material => material == null ? "null" : ObjectIdentity(material) + ":" + AssetDatabase.GetAssetPath(material)))));
        }
        static IEnumerable<string> LoadedScenePaths()
        {
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                string path = SceneManager.GetSceneAt(i).path;
                if (!string.IsNullOrEmpty(path)) yield return path;
            }
        }
        static string SceneState()
        {
            var values = new List<string> { "active=" + SceneManager.GetActiveScene().handle, "count=" + SceneManager.sceneCount,
                "preview=" + EditorSceneManager.previewSceneCount };
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                Scene scene = SceneManager.GetSceneAt(i);
                values.Add(scene.handle + ":" + scene.path + ":" + scene.isLoaded + ":" + scene.isDirty + ":" + DirtyId?.GetValue(scene) + ":" +
                    (scene.isLoaded ? string.Join(",", scene.GetRootGameObjects().Select(ObjectIdentity)) : ""));
            }
            return string.Join("\n", values);
        }
        static string ObjectIdentity(Object value)
        {
#if UNITY_6000_5_OR_NEWER
            return value.GetEntityId().ToString();
#else
            return value.GetInstanceID().ToString();
#endif
        }
        static string Full(Run run, string path) => Path.GetFullPath(Path.Combine(run.root, path));
        static string Hash(string path)
        {
            using (var input = File.OpenRead(path)) using (var digest = SHA256.Create()) return BitConverter.ToString(digest.ComputeHash(input)).Replace("-", "").ToLowerInvariant();
        }
        static void AddError(Run run, string error) { run.report["error"] = (run.report["error"]?.Value<string>() ?? "") + "\n" + error; run.report["passed"] = false; }
        static void Require(bool condition, string message) { if (!condition) throw new InvalidOperationException(message); }
    }
}
