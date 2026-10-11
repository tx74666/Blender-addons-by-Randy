using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace CharacterDesigner.Unity.Editor
{
    /// <summary>Project-local material requests; Unity confirms the imported binding, never the artist's shader graph.</summary>
    [InitializeOnLoad]
    public static class CharacterMaterialMatching
    {
        internal const string RequestSchema = "cdesigner.material-match-request/1";
        internal const string ResultSchema = "cdesigner.material-match-result/1";
        internal const string ReceiptSchema = "cdesigner.material-match-receipt/1";
        const string ManifestSchema = "cdesigner.unity-character.v1";
        const long TimeoutSeconds = 180;
        static readonly string Root = Path.GetFullPath(Path.Combine(Application.dataPath, "..")).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        static readonly string Store = Path.Combine(Root, ".codex", "character-material-matches");
        static readonly string Requests = Path.Combine(Store, "requests");
        static readonly ConcurrentQueue<string> Events = new ConcurrentQueue<string>();
        static readonly Dictionary<string, Request> Waiting = new Dictionary<string, Request>(StringComparer.Ordinal);
        static readonly Dictionary<string, bool> Models = new Dictionary<string, bool>(StringComparer.Ordinal);
        static readonly HashSet<string> KnownModels = new HashSet<string>(StringComparer.Ordinal);
        static readonly HashSet<string> KnownMaterialPaths = new HashSet<string>(StringComparer.Ordinal);
        static readonly HashSet<string> InFlight = new HashSet<string>(StringComparer.Ordinal);
        static readonly Dictionary<string, Delivery> TerminalRetries = new Dictionary<string, Delivery>(StringComparer.Ordinal);
        static readonly Dictionary<string, UndoImporterScope> UndoScopes = new Dictionary<string, UndoImporterScope>(StringComparer.Ordinal);
        static readonly Dictionary<string, string> UndoRestores = new Dictionary<string, string>(StringComparer.Ordinal);
        static readonly string UndoScopeKey = "CharacterDesigner.MaterialMatch.UndoScopes/1/" + HashBytes(Encoding.UTF8.GetBytes(Root));
        static FileSystemWatcher watcher;
        static Request active;
        static CharacterMaterialMatchWindow window;
        static bool draining, stopped, restoringUndo;
        static bool? mainEditor;
        static VerificationSession verification;

        [Serializable]
        internal class Request
        {
            public string schema, project_root, model_path, export_asset_id, source_material, request_id;
            public long created_unix;
        }
        [Serializable]
        internal sealed class Record : Request
        {
            public string status, material_guid, material_path, material_name, fbx_sha256, manifest_sha256, error;
            public long material_local_id;
            public bool explicit_choice;
        }
        internal sealed class Binding
        {
            public string source, guid, path, name;
            public long local;
            public Material material;
            public bool manual;
        }
        internal sealed class Context
        {
            public string model, assetId, hash, manifestHash, signature;
            public HashSet<string> sources;
            public List<Binding> bindings;
            public List<Binding> simplified;
            public List<Binding> verificationOnly;
        }
        sealed class PublicationPendingException : Exception
        {
            internal PublicationPendingException() : base("The new FBX is waiting for its completed Character Designer manifest.") { }
        }
        sealed class SourceInventoryException : Exception
        {
            internal SourceInventoryException(string message) : base(message) { }
        }
        sealed class Delivery { internal Record record; internal double retryAfter; }
        [Serializable]
        sealed class UndoImporterScope
        {
            public string model, guid, importerIdentity, assetId, source, hash, manifestHash, mapSignature, expectedMapSignature;
            public bool needsReimport, preserveRestoredMap;
            public List<int> groups = new List<int>();
        }

        static CharacterMaterialMatching()
        {
            if (!MainEditor) return;
            LoadUndoScopes();
            EditorApplication.delayCall += Start;
            EditorApplication.update += Update;
            AssemblyReloadEvents.beforeAssemblyReload += Stop;
            EditorApplication.quitting += Stop;
            Undo.undoRedoEvent += VerifyAfterUndo;
        }
        static void Start()
        {
            if (stopped || !MainEditor) return;
            try
            {
                GuardPath(Requests);
                Directory.CreateDirectory(Requests);
                watcher = new FileSystemWatcher(Requests) { NotifyFilter = NotifyFilters.FileName | NotifyFilters.LastWrite, IncludeSubdirectories = false };
                watcher.Created += Changed;
                watcher.Changed += Changed;
                watcher.Renamed += Renamed;
                watcher.Error += WatcherError;
                watcher.EnableRaisingEvents = true;
                ScanOnce();
            }
            catch (Exception error) { Debug.LogWarning("Character Designer material requests: " + error.Message); }
        }
        static void Changed(object sender, FileSystemEventArgs args) => Events.Enqueue(args.FullPath);
        static void Renamed(object sender, RenamedEventArgs args) => Events.Enqueue(args.FullPath);
        static void WatcherError(object sender, ErrorEventArgs args) => Events.Enqueue("!rescan");
        static void ScanOnce()
        {
            foreach (string path in Directory.EnumerateFiles(Requests, "*.json", SearchOption.TopDirectoryOnly))
                if (!path.EndsWith(".result.json", StringComparison.OrdinalIgnoreCase)) Events.Enqueue(path);
        }
        static void Stop()
        {
            stopped = true;
            watcher?.Dispose(); watcher = null;
            // Persisted requests remain resumable until their original deadline; a reload is not a user cancellation.
            CharacterMaterialMatchWindow owned = window;
            active = null; window = null;
            if (owned != null) { owned.Terminal = true; owned.Close(); }
            EditorApplication.update -= Update;
            Undo.undoRedoEvent -= VerifyAfterUndo;
        }
        internal static bool EditorBusy => EditorApplication.isPlayingOrWillChangePlaymode || EditorApplication.isCompiling || EditorApplication.isUpdating || BuildPipeline.isBuildingPlayer;
        internal static bool MainEditor
        {
            get
            {
                if (AssetDatabase.IsAssetImportWorkerProcess()) return false;
                if (mainEditor.HasValue) return mainEditor.Value;
                Type player = AppDomain.CurrentDomain.GetAssemblies().Select(assembly => assembly.GetType("Unity.Multiplayer.PlayMode.CurrentPlayer", false)).FirstOrDefault(type => type != null);
                try { mainEditor = player == null || player.GetProperty("IsMainEditor", BindingFlags.Public | BindingFlags.Static)?.GetValue(null) is true; }
                catch (Exception) { mainEditor = false; }
                return mainEditor.Value;
            }
        }
        static long Now => DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        static string RequestPath(Request request) => Path.Combine(Requests, request.request_id + ".json");
        static string ResultPath(Request request) => Path.Combine(Requests, request.request_id + ".result.json");
        static bool Cancelled(Request request) => File.Exists(Path.Combine(Requests, request.request_id + ".cancel"));
        static bool Expired(Request request) => Now - request.created_unix >= TimeoutSeconds;
        static void VerifyAfterUndo(in UndoRedoInfo undo)
        {
            if (stopped || !MainEditor || undo.undoName != "Match Unity Material") return;
            foreach (UndoImporterScope scope in UndoScopes.Values)
            {
                if (!scope.groups.Contains(undo.undoGroup) || verification != null && verification.Model != scope.model) continue;
                // Capture the restored map before any normal publication processing can replay its previous pairing.
                try
                {
                    scope.expectedMapSignature = ImporterMapSignature(scope.model);
                    scope.needsReimport |= scope.expectedMapSignature != scope.mapSignature;
                    scope.preserveRestoredMap = true;
                    SaveUndoScopes();
                    UndoRestores[scope.model] = scope.expectedMapSignature;
                }
                catch (Exception error) { Debug.LogWarning("Character Designer material Undo needs review: " + error.Message); }
            }
            if (!EditorBusy && !draining && !restoringUndo) RestoreRecordedImporters();
        }
        static string ImporterIdentity(ModelImporter importer)
        {
#if UNITY_6000_5_OR_NEWER
            return importer.GetEntityId().ToString();
#else
            return importer.GetInstanceID().ToString();
#endif
        }
        static string ImporterMapSignature(string model)
        {
            var importer = AssetImporter.GetAtPath(model) as ModelImporter;
            Need(importer != null, "The recorded material-match importer is unavailable.");
            var map = importer.GetExternalObjectMap();
            Need(map.Count <= 4096, "The recorded importer remap inventory exceeds the supported bound.");
            var entries = new JArray();
            foreach (var pair in map.OrderBy(value => value.Key.type.AssemblyQualifiedName, StringComparer.Ordinal).ThenBy(value => value.Key.name, StringComparer.Ordinal))
            {
                string target = "null";
                if (pair.Value != null)
                {
                    Need(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(pair.Value, out string guid, out long local), "A recorded remap target has no persistent Unity identity.");
                    target = guid + ":" + local.ToString(System.Globalization.CultureInfo.InvariantCulture);
                }
                entries.Add(new JArray(pair.Key.type.AssemblyQualifiedName, pair.Key.name, target));
            }
            return HashBytes(Encoding.UTF8.GetBytes(entries.ToString(Formatting.None)));
        }
        static void LoadUndoScopes()
        {
            try
            {
                string saved = SessionState.GetString(UndoScopeKey, "");
                if (saved.Length == 0) return;
                Need(saved.Length <= 1024 * 1024 && Encoding.UTF8.GetByteCount(saved) <= 1024 * 1024, "The material-match Undo scope exceeds the supported bound.");
                var entries = JArray.Parse(saved);
                Need(entries.Count <= 64, "Too many recorded material-match importers.");
                foreach (JToken entry in entries)
                {
                    var scope = entry.ToObject<UndoImporterScope>();
                    Need(scope != null && Hex(scope.guid, 32) && Hex(scope.assetId, 32) && Hex(scope.hash, 64) && Hex(scope.manifestHash, 64) && Hex(scope.mapSignature, 64) &&
                        !string.IsNullOrEmpty(scope.importerIdentity) && !string.IsNullOrEmpty(scope.source) && scope.groups != null && scope.groups.Count <= 1024 && scope.groups.All(value => value >= 0) &&
                        (string.IsNullOrEmpty(scope.expectedMapSignature) || Hex(scope.expectedMapSignature, 64)), "Invalid recorded material-match Undo scope.");
                    ModelPath(scope.model);
                    UndoScopes.Add(scope.model, scope);
                    if (scope.needsReimport && Hex(scope.expectedMapSignature, 64)) UndoRestores.Add(scope.model, scope.expectedMapSignature);
                }
            }
            catch (Exception error) { UndoScopes.Clear(); UndoRestores.Clear(); Debug.LogWarning("Character Designer material Undo scope: " + error.Message); }
        }
        static string SerializeUndoScopes(UndoImporterScope replacement = null)
        {
            var scopes = replacement == null ? UndoScopes.Values.ToArray() : UndoScopes.Values.Where(value => value.model != replacement.model).Concat(new[] { replacement }).ToArray();
            Need(scopes.Length <= 64 && scopes.All(value => value.groups != null && value.groups.Count <= 1024), "The recorded material-match Undo scope exceeds the supported bound.");
            string saved = JArray.FromObject(scopes).ToString(Formatting.None);
            Need(saved.Length <= 1024 * 1024 && Encoding.UTF8.GetByteCount(saved) <= 1024 * 1024, "The material-match Undo scope exceeds the supported bound.");
            return saved;
        }
        static void SaveUndoScopes() => SessionState.SetString(UndoScopeKey, SerializeUndoScopes());
        static UndoImporterScope PrepareRecordedImporter(Context context, string source, int group)
        {
            var importer = AssetImporter.GetAtPath(context.model) as ModelImporter;
            Need(importer != null, "The matched importer is unavailable for Undo tracking.");
            if (!UndoScopes.TryGetValue(context.model, out var scope) || scope.guid != AssetDatabase.AssetPathToGUID(context.model) || scope.importerIdentity != ImporterIdentity(importer) ||
                scope.assetId != context.assetId || scope.hash != context.hash || scope.manifestHash != context.manifestHash)
                scope = new UndoImporterScope { model = context.model };
            else scope = new UndoImporterScope { model = context.model, groups = scope.groups.ToList() };
            scope.guid = AssetDatabase.AssetPathToGUID(context.model); scope.importerIdentity = ImporterIdentity(importer);
            scope.assetId = context.assetId; scope.source = source; scope.hash = context.hash; scope.manifestHash = context.manifestHash;
            scope.mapSignature = ImporterMapSignature(context.model);
            scope.expectedMapSignature = scope.mapSignature; // Reserve the bounded persisted restore signature before the first Undo.
            if (!scope.groups.Contains(group)) scope.groups.Add(group);
            SerializeUndoScopes(scope); // Validate the complete bounded projection before native remap mutation.
            return scope;
        }
        static void TrackRecordedImporter(Context context, string source, int group)
        {
            UndoScopes[context.model] = PrepareRecordedImporter(context, source, group);
            SaveUndoScopes();
        }
        static void RestoreRecordedImporters()
        {
            if (stopped || !MainEditor || EditorBusy || restoringUndo) return;
            restoringUndo = true;
            try
            {
                foreach (var pending in UndoRestores.ToArray())
                {
                    if (EditorBusy) break;
                    if (verification != null && verification.Model != pending.Key) continue;
                    UndoImporterScope scope = null;
                    bool ownedPublication = false;
                    try
                    {
                        Need(UndoScopes.TryGetValue(pending.Key, out scope), "The native Undo importer no longer has a recorded material-match scope.");
                        string expected = pending.Value ?? ImporterMapSignature(scope.model);
                        if (expected == scope.mapSignature && !scope.needsReimport) { UndoRestores.Remove(scope.model); continue; }
                        var importer = AssetImporter.GetAtPath(scope.model) as ModelImporter;
                        Need(importer != null && AssetDatabase.AssetPathToGUID(scope.model) == scope.guid && ImporterIdentity(importer) == scope.importerIdentity,
                            "The native Undo target is no longer the recorded material-match importer.");
                        Context context = ReadContext(scope.model, false);
                        Need(context.assetId == scope.assetId && context.hash == scope.hash && context.manifestHash == scope.manifestHash && context.sources.Contains(scope.source),
                            "The character publication changed after matching; the native remap was preserved for review.");
                        ownedPublication = true;
                        RequireImportedSource(scope.model, scope.source);
                        Need(ImporterMapSignature(scope.model) == expected, "The native remap changed while its Undo import was queued; it was preserved for review.");
                        scope.needsReimport = true; scope.expectedMapSignature = expected; scope.preserveRestoredMap = true; SaveUndoScopes();
                        InFlight.Add(scope.model);
                        try { importer.SaveAndReimport(); }
                        finally { InFlight.Remove(scope.model); }
                        Need(ImporterMapSignature(scope.model) == expected, "Reimport changed the restored native remap; review the recorded importer.");
                        UndoRestores.Remove(scope.model); Models.Remove(scope.model);
                        // Verify the restored result, never replay a declared or previous manual pairing here.
                        ProcessModel(scope.model, false, true, true);
                        scope.mapSignature = expected; scope.needsReimport = false; SaveUndoScopes();
                    }
                    catch (Exception error)
                    {
                        UndoRestores.Remove(pending.Key); Models.Remove(pending.Key);
                        if (ownedPublication)
                        {
                            // A partial native import must not make a later Redo look already synchronized.
                            scope.needsReimport = true; scope.preserveRestoredMap = true;
                            try { SaveUndoScopes(); ProcessModel(scope.model, false, true, true); }
                            catch (Exception failure) { Debug.LogWarning("Character Designer material Undo verification: " + failure.Message); }
                        }
                        Debug.LogWarning("Character Designer material Undo needs review: " + error.Message);
                    }
                }
            }
            finally { restoringUndo = false; }
        }
        static bool PreserveRestoredPublication(Context context)
        {
            if (!UndoScopes.TryGetValue(context.model, out var scope) || !scope.preserveRestoredMap || scope.assetId != context.assetId || scope.hash != context.hash || scope.manifestHash != context.manifestHash) return false;
            var importer = AssetImporter.GetAtPath(context.model) as ModelImporter;
            return importer != null && AssetDatabase.AssetPathToGUID(context.model) == scope.guid && ImporterIdentity(importer) == scope.importerIdentity;
        }
        static void Update()
        {
            if (stopped || draining || verification != null || !MainEditor) return;
            draining = true;
            try
            {
                var changed = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                while (Events.TryDequeue(out string path)) changed.Add(path);
                foreach (string path in changed)
                {
                    if (path == "!rescan") { ScanOnce(); continue; }
                    if (path.EndsWith(".cancel", StringComparison.OrdinalIgnoreCase)) continue;
                    if (!path.EndsWith(".json", StringComparison.OrdinalIgnoreCase) || path.EndsWith(".result.json", StringComparison.OrdinalIgnoreCase)) continue;
                    ReadRequest(path);
                }
                foreach (var pair in TerminalRetries.ToArray())
                {
                    if (EditorApplication.timeSinceStartup < pair.Value.retryAfter) continue;
                    try { WriteJson(ResultPath(pair.Value.record), pair.Value.record, false); TerminalRetries.Remove(pair.Key); }
                    catch (Exception) { pair.Value.retryAfter = EditorApplication.timeSinceStartup + 1; }
                }
                foreach (Request request in Waiting.Values.ToArray())
                    if (Cancelled(request) || Expired(request))
                    { Finish(request, null, Cancelled(request) ? "cancelled" : "failed", Expired(request) ? "Material matching timed out after 180 seconds." : ""); Waiting.Remove(request.request_id); }
                if (active != null && (Cancelled(active) || Expired(active)))
                    CompleteActive(null, Cancelled(active) ? "cancelled" : "failed", Expired(active) ? "Material matching timed out after 180 seconds." : "");
                if (EditorBusy) { window?.Repaint(); return; }
                RestoreRecordedImporters();
                if (active != null && window != null && window.ApplyReady) Apply(active, window.SelectedMaterial);
                if (active == null && Waiting.Count > 0)
                {
                    Request next = Waiting.Values.OrderBy(value => value.created_unix).ThenBy(value => value.request_id, StringComparer.Ordinal).First();
                    Waiting.Remove(next.request_id);
                    try
                    {
                        ValidateRequest(next, true);
                        active = next;
                        window = ScriptableObject.CreateInstance<CharacterMaterialMatchWindow>();
                        window.Begin(next);
                        window.Show();
                    }
                    catch (Exception error)
                    {
                        if (active != null) CompleteActive(null, "failed", error.Message);
                        else Finish(next, null, "failed", error.Message);
                    }
                }
                foreach (var pair in Models.ToArray())
                {
                    Models.Remove(pair.Key);
                    ProcessModel(pair.Key, pair.Value);
                }
            }
            catch (Exception error) { Debug.LogWarning("Character Designer material matching: " + error.Message); }
            finally { draining = false; }
        }
        static void ReadRequest(string path)
        {
            Request request = null;
            try
            {
                GuardPath(path);
                string id = Path.GetFileNameWithoutExtension(path);
                Need(Hex(id, 32) && SamePath(Path.GetDirectoryName(path), Requests), "Invalid material request filename.");
                JObject json = ReadJson(path, 16384);
                request = new Request { schema = Text(json, "schema"), request_id = Text(json, "request_id"), project_root = Text(json, "project_root"),
                    model_path = Text(json, "model_path"), export_asset_id = Text(json, "export_asset_id"), source_material = Text(json, "source_material"), created_unix = Integer(json, "created_unix") };
                Need(request.request_id == id && request.schema == RequestSchema, "Unsupported material request identity.");
                ValidateIdentity(request);
                if (File.Exists(ResultPath(request)) || TerminalRetries.ContainsKey(id) || Waiting.ContainsKey(id) || active?.request_id == id) return;
                if (Cancelled(request) || Expired(request)) { Finish(request, null, Cancelled(request) ? "cancelled" : "failed", "Material request expired or was cancelled."); return; }
                Need(Now + 30 >= request.created_unix, "The material request timestamp is in the future.");
                Need(Waiting.Count < 32, "The material request queue is full; retry after the current requests finish.");
                Need(!Waiting.Values.Concat(active == null ? Array.Empty<Request>() : new[] { active }).Any(value => value.model_path == request.model_path && value.source_material == request.source_material), "A material request for this source is already open.");
                Waiting.Add(id, request);
            }
            catch (IOException) { /* Atomic request publication will produce another watcher event; do not reject a partial write. */ }
            catch (Exception error)
            {
                if (request != null && Hex(request.request_id, 32) && SamePath(request.project_root, Root)) Finish(request, null, "failed", error.Message);
                else Debug.LogWarning("Character Designer rejected a material request: " + error.Message);
            }
        }
        static void ValidateIdentity(Request request)
        {
            Need(SamePath(request.project_root, Root), "The material request belongs to another Unity project.");
            Need(Hex(request.request_id, 32) && Hex(request.export_asset_id, 32), "Invalid request or export profile ID.");
            Need(request.created_unix > 0 && !string.IsNullOrEmpty(request.source_material) && request.source_material.Length <= 4096 && !request.source_material.Any(char.IsControl), "Invalid source material or timestamp.");
            ModelPath(request.model_path);
        }
        static Context ValidateRequest(Request request, bool checkSource)
        {
            ValidateIdentity(request);
            Request current = JsonUtility.FromJson<Request>(File.ReadAllText(RequestPath(request)));
            Need(current != null && current.schema == request.schema && current.request_id == request.request_id && current.project_root == request.project_root && current.model_path == request.model_path && current.export_asset_id == request.export_asset_id && current.source_material == request.source_material && current.created_unix == request.created_unix, "The material request changed; send a new request.");
            Context context = ReadContext(request.model_path, false);
            Need(context.assetId == request.export_asset_id && context.sources.Contains(request.source_material), "The source material or export profile is absent from this owned model.");
            if (checkSource) RequireImportedSource(request.model_path, request.source_material);
            return context;
        }
        static void Apply(Request request, Material material)
        {
            ModelImporter importer = null;
            UnityEngine.Object previous = null;
            bool hadPrevious = false, changed = false;
            bool receiptCommitted = false, terminalPublished = false;
            byte[] previousReceipt = null;
            string receiptPath = ReceiptPath(request.export_asset_id, request.source_material);
            var key = new AssetImporter.SourceAssetIdentifier(typeof(Material), request.source_material);
            Binding binding = null;
            int recordedUndoGroup = -1;
            try
            {
                Need(!Cancelled(request) && !Expired(request) && !File.Exists(ResultPath(request)), "The material request was cancelled, expired or already completed.");
                Context before = ValidateRequest(request, true);
                if (File.Exists(receiptPath)) previousReceipt = File.ReadAllBytes(receiptPath);
                binding = Identify(material, request.source_material);
                importer = AssetImporter.GetAtPath(request.model_path) as ModelImporter;
                Need(importer != null && !EditorUtility.IsDirty(importer), "Apply or revert the model's pending Inspector changes before matching a material.");
                hadPrevious = importer.GetExternalObjectMap().TryGetValue(key, out previous);
                InFlight.Add(request.model_path);
                // An explicit isolated acceptance run must not consume the user's global Redo history.
                if (verification == null || verification.Model != request.model_path || verification.NativeUndoEnabled)
                {
                    recordedUndoGroup = Undo.GetCurrentGroup();
                    PrepareRecordedImporter(before, request.source_material, recordedUndoGroup); // Includes all foreign remaps and bounded persistence.
                    Undo.RegisterCompleteObjectUndo(importer, "Match Unity Material");
                }
                importer.AddRemap(key, material); changed = true;
                importer.SaveAndReimport();
                Context after = ValidateRequest(request, true);
                Need(after.hash == before.hash && after.manifestHash == before.manifestHash, "The model export changed during matching; retry with its current manifest.");
                VerifyBinding(after, binding);
                Need(!Cancelled(request) && !Expired(request), "The material request was cancelled or expired during import.");
                WriteReceipt(request, binding, after.hash, "matched", "", after.manifestHash);
                receiptCommitted = true;
                Need(!File.Exists(ResultPath(request)), "The request obtained another terminal result during import.");
                Finish(request, binding, "matched", "", after.hash, after.manifestHash);
                terminalPublished = true;
                SessionState.SetString(SignatureKey(after.model), after.signature);
                KnownModels.Add(after.model);
                if (recordedUndoGroup >= 0) TrackRecordedImporter(after, request.source_material, recordedUndoGroup);
                CompleteActive(null, "", "");
            }
            catch (Exception error)
            {
                if (terminalPublished)
                {
                    Debug.LogWarning("Character Designer material match completed; window cleanup: " + error.Message);
                    CompleteActive(null, "", ""); return;
                }
                string failure = error.Message;
                if (changed)
                {
                    try
                    {
                        importer = AssetImporter.GetAtPath(request.model_path) as ModelImporter;
                        Need(importer != null, "The model importer is unavailable for rollback.");
                        if (hadPrevious) importer.AddRemap(key, previous); else importer.RemoveRemap(key);
                        importer.SaveAndReimport();
                        var restored = importer.GetExternalObjectMap();
                        Need(hadPrevious ? restored.TryGetValue(key, out var value) && value == previous : !restored.ContainsKey(key), "The previous material binding could not be restored.");
                    }
                    catch (Exception rollback) { failure += " Rollback failed: " + rollback.Message; }
                }
                if (receiptCommitted)
                {
                    try
                    {
                        if (previousReceipt != null) WriteBytes(receiptPath, previousReceipt, true);
                        else if (File.Exists(receiptPath)) File.Delete(receiptPath);
                    }
                    catch (Exception restore) { failure += " Receipt rollback failed: " + restore.Message; }
                }
                CompleteActive(binding, Cancelled(request) ? "cancelled" : "failed", failure);
            }
            finally { InFlight.Remove(request.model_path); }
        }
        internal static void CancelWindow(CharacterMaterialMatchWindow candidate)
        {
            if (candidate != window || active == null || stopped) return;
            CompleteActive(null, "cancelled", "");
        }
        static void CompleteActive(Binding binding, string status, string error)
        {
            Request request = active;
            CharacterMaterialMatchWindow owned = window;
            active = null; window = null;
            if (request != null && status.Length > 0)
            {
                try { Finish(request, binding, status, error); }
                catch (Exception failure)
                {
                    TerminalRetries[request.request_id] = new Delivery { record = MakeRecord(request, binding, ResultSchema, status, error, null, null), retryAfter = EditorApplication.timeSinceStartup + 1 };
                    Debug.LogWarning("Character Designer material result is waiting for disk access: " + failure.Message);
                }
            }
            if (owned != null) { owned.Terminal = true; owned.Close(); }
        }
        static Record MakeRecord(Request request, Binding binding, string schema, string status, string error, string hash, string manifestHash)
        {
            return new Record { schema = schema, project_root = Root, model_path = request.model_path, export_asset_id = request.export_asset_id,
                source_material = request.source_material, request_id = request.request_id, created_unix = request.created_unix, status = status,
                material_guid = binding?.guid ?? "", material_local_id = binding?.local ?? 0, material_path = binding?.path ?? "", material_name = binding?.material != null ? binding.material.name : binding?.name ?? "",
                fbx_sha256 = hash ?? "", manifest_sha256 = manifestHash ?? "", explicit_choice = binding?.manual ?? false, error = error ?? "" };
        }
        static void Finish(Request request, Binding binding, string status, string error, string hash = null, string manifestHash = null)
        {
            if (File.Exists(ResultPath(request))) return;
            WriteJson(ResultPath(request), MakeRecord(request, binding, ResultSchema, status, error, hash, manifestHash), false);
        }
        static string ReceiptPath(string assetId, string source) => Path.Combine(Store, "receipts", assetId, HashBytes(Encoding.UTF8.GetBytes(source)) + ".json");
        static void WriteReceipt(Request request, Binding binding, string hash, string status, string error, string manifestHash)
        {
            WriteJson(ReceiptPath(request.export_asset_id, request.source_material), MakeRecord(request, binding, ReceiptSchema, status, error, hash, manifestHash), true);
        }
        static string SignatureKey(string model) => "CharacterDesigner.MaterialMatch.Verified/1/" + HashBytes(Encoding.UTF8.GetBytes(Root + "\n" + model));

        // Only the explicit acceptance menu can own this fresh fixture. Incoming real work stays queued.
        internal static VerificationSession BeginIsolatedVerification(string model, string assetId)
        {
            Need(MainEditor && !EditorBusy && !stopped && !draining && verification == null, "Use the idle main Editor for isolated material verification.");
            Need(active == null && window == null && Waiting.Count == 0 && TerminalRetries.Count == 0, "Finish or cancel current material requests before isolated verification.");
            const string prefix = "Assets/~QA_MaterialMatching_";
            Need(Hex(assetId, 32) && model == prefix + assetId + "/Fixture.fbx", "Verification needs its own new fixture/profile identity.");
            string folder = prefix + assetId;
            ModelPath(model);
            Need(!Directory.Exists(Path.Combine(Root, folder)) && !File.Exists(Path.Combine(Root, folder + ".meta")) && !AssetDatabase.IsValidFolder(folder), "The isolated fixture already exists; existing results are retained.");
            Need(!Directory.Exists(Path.Combine(Store, "receipts", assetId)), "The isolated receipt profile already exists.");
            verification = new VerificationSession(model, folder, assetId);
            return verification;
        }
        internal sealed class VerificationSession : IDisposable
        {
            internal readonly string Model, Folder, AssetId;
            internal string ProjectRoot => Root;
            internal string StorePath => Store;
            internal string[] ProtectedAssetPaths => KnownModels.Concat(KnownMaterialPaths).Where(path => !path.StartsWith(Folder + "/", StringComparison.Ordinal)).Distinct(StringComparer.Ordinal).ToArray();
            readonly Dictionary<string, Request> owned = new Dictionary<string, Request>(StringComparer.Ordinal);
            bool disposed;
            internal bool NativeUndoEnabled { get; private set; }
            internal VerificationSession(string model, string folder, string assetId) { Model = model; Folder = folder; AssetId = assetId; }
            void Check() => Need(!disposed && verification == this && MainEditor && !EditorBusy, "The isolated verification session is unavailable or Unity is busy.");
            void Own(Request request)
            {
                Check();
                Need(request != null && owned.TryGetValue(request.request_id, out var original) && ReferenceEquals(original, request) && request.model_path == Model && request.export_asset_id == AssetId, "Verification can only use its own fixture requests.");
            }
            internal void EnableNativeUndo()
            {
                Check();
                Need(!NativeUndoEnabled && Model == "Assets/~QA_MaterialMatching_" + AssetId + "/Fixture.fbx", "Native Undo belongs only to this exact fresh fixture.");
                MethodInfo hasRedo = typeof(Undo).GetMethod("HasRedo", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic, null, Type.EmptyTypes, null);
                Need(hasRedo != null && hasRedo.ReturnType == typeof(bool) && !(bool)hasRedo.Invoke(null, null), "Pending user Redo must remain intact; native fixture Undo is unavailable.");
                NativeUndoEnabled = true;
            }
            internal void DisableNativeUndo() { NativeUndoEnabled = false; }
            internal Request CreateRequest(string source, long ageSeconds = 0)
            {
                Check();
                var request = new Request { schema = RequestSchema, project_root = Root, model_path = Model, export_asset_id = AssetId, source_material = source, request_id = Guid.NewGuid().ToString("N"), created_unix = Now - ageSeconds };
                owned.Add(request.request_id, request);
                WriteBytes(RequestPath(request), new UTF8Encoding(false).GetBytes(JsonUtility.ToJson(request, true)), false);
                return request;
            }
            internal void Enqueue(Request request) { Own(request); ReadRequest(RequestPath(request)); }
            internal int QueuedCount(Request request) { Own(request); return Waiting.Values.Count(value => value.request_id == request.request_id); }
            internal void Cancel(Request request) { Own(request); WriteBytes(Path.Combine(Requests, request.request_id + ".cancel"), Array.Empty<byte>(), false); }
            internal Record Result(Request request)
            {
                Own(request);
                return File.Exists(ResultPath(request)) ? ReadJson(ResultPath(request), 65536).ToObject<Record>() : null;
            }
            internal Record ApplyRequest(Request request, Material material)
            {
                Own(request);
                Need(AssetDatabase.GetAssetPath(material).StartsWith(Folder + "/", StringComparison.Ordinal), "Use a copied fixture Material for verification.");
                Need(active == null && Waiting.Remove(request.request_id), "The fixture request is not queued.");
                active = request;
                Apply(request, material);
                Need(active == null, "The fixture request did not recover its active state.");
                return Result(request);
            }
            internal Record Receipt(string source)
            {
                Check();
                string path = ReceiptPath(AssetId, source);
                return File.Exists(path) ? ReadJson(path, 65536).ToObject<Record>() : null;
            }
            internal void ValidateSource(string source) { Check(); RequireImportedSource(Model, source); }
            internal void Process(bool ensureImport = true) { Check(); Models.Remove(Model); ProcessModel(Model, ensureImport); }
            internal void ProcessQueued() { Check(); QueueModel(Model, false); Need(Models.TryGetValue(Model, out bool import), "The fixture event did not queue its model."); Models.Remove(Model); ProcessModel(Model, import); }
            internal void ForgetVerifiedSignature() { Check(); SessionState.EraseString(SignatureKey(Model)); }
            internal void SetFixtureRemap(string source, Material material)
            {
                Check();
                Need(material == null || AssetDatabase.GetAssetPath(material).StartsWith(Folder + "/", StringComparison.Ordinal), "Verification may only map copied Materials.");
                var importer = AssetImporter.GetAtPath(Model) as ModelImporter;
                Need(importer != null && !EditorUtility.IsDirty(importer), "The fixture importer has pending Inspector edits.");
                var key = new AssetImporter.SourceAssetIdentifier(typeof(Material), source);
                var before = importer.GetExternalObjectMap();
                if (material == null ? !before.ContainsKey(key) : before.TryGetValue(key, out var value) && value == material) return;
                InFlight.Add(Model);
                try
                {
                    if (material == null) importer.RemoveRemap(key); else importer.AddRemap(key, material);
                    importer.SaveAndReimport();
                }
                finally { InFlight.Remove(Model); }
            }
            public void Dispose()
            {
                if (disposed) return;
                try
                {
                    if (active != null && owned.ContainsKey(active.request_id)) CompleteActive(null, "cancelled", "Isolated verification ended.");
                    foreach (Request request in owned.Values)
                    {
                        Waiting.Remove(request.request_id);
                        if (!File.Exists(ResultPath(request)) && !TerminalRetries.ContainsKey(request.request_id))
                        {
                            try { Finish(request, null, "cancelled", "Isolated verification ended."); }
                            catch (Exception) { TerminalRetries[request.request_id] = new Delivery { record = MakeRecord(request, null, ResultSchema, "cancelled", "Isolated verification ended.", null, null), retryAfter = EditorApplication.timeSinceStartup + 1 }; }
                        }
                    }
                }
                finally
                {
                    NativeUndoEnabled = false;
                    UndoRestores.Remove(Model); UndoScopes.Remove(Model);
                    try { SaveUndoScopes(); }
                    finally
                    {
                        Models.Remove(Model); KnownModels.Remove(Model); InFlight.Remove(Model);
                        KnownMaterialPaths.RemoveWhere(path => path.StartsWith(Folder + "/", StringComparison.Ordinal));
                        SessionState.EraseString(SignatureKey(Model));
                        disposed = true; verification = null;
                    }
                }
            }
        }

        internal static void QueueModel(string model, bool ensureImport)
        {
            if (stopped || !MainEditor) return;
            try { ModelPath(model); if (!KnownModels.Contains(model) && !File.Exists(ManifestPath(model))) return; }
            catch (Exception) { return; }
            Models[model] = ensureImport || (Models.TryGetValue(model, out bool old) && old);
            KnownModels.Add(model);
        }
        internal static void MaterialAssetsChanged(IEnumerable<string> paths)
        {
            if (verification != null) paths = paths.Where(path => !path.StartsWith(verification.Folder + "/", StringComparison.Ordinal));
            if (!MainEditor || !paths.Any(path => path.EndsWith(".mat", StringComparison.OrdinalIgnoreCase) || KnownMaterialPaths.Contains(path))) return;
            foreach (string model in KnownModels) QueueModel(model, false);
        }
        internal static void Preprocess(string model, ModelImporter importer)
        {
            if (!MainEditor || InFlight.Contains(model)) return; // Workers inherit the .meta already committed by the main Editor.
            string manifest;
            try { manifest = ManifestPath(model); }
            catch (Exception) { return; } // Unity invokes this callback for non-FBX and package models too.
            if (!File.Exists(manifest)) return;
            if (UndoRestores.ContainsKey(model)) { QueueModel(model, false); return; }
            if (verification != null && verification.Model != model) { QueueModel(model, false); return; }
            var saved = new Dictionary<AssetImporter.SourceAssetIdentifier, UnityEngine.Object>();
            var added = new HashSet<AssetImporter.SourceAssetIdentifier>();
            try
            {
                Context context = ReadContext(model, true);
                if (PreserveRestoredPublication(context)) { QueueModel(model, false); return; }
                if (context.bindings.Count + context.simplified.Count == 0 || SessionState.GetString(SignatureKey(model), "") == context.signature) return;
                ValidateImportedBindings(context);
                // ReadContext validates every desired binding before any map is touched.
                var old = importer.GetExternalObjectMap();
                foreach (Binding binding in context.bindings.Concat(context.simplified))
                {
                    var key = new AssetImporter.SourceAssetIdentifier(typeof(Material), binding.source);
                    if (old.TryGetValue(key, out var previous)) saved.Add(key, previous); else added.Add(key);
                }
                foreach (Binding binding in context.bindings)
                    importer.AddRemap(new AssetImporter.SourceAssetIdentifier(typeof(Material), binding.source), binding.material);
                foreach (Binding binding in context.simplified)
                {
                    var key = new AssetImporter.SourceAssetIdentifier(typeof(Material), binding.source);
                    if (binding.material != null && old.TryGetValue(key, out var previous) && previous == binding.material) importer.RemoveRemap(key);
                }
                QueueModel(model, false);
            }
            catch (PublicationPendingException) { /* FBX is published before its manifest; the manifest event will schedule the current import. */ }
            catch (SourceInventoryException) { QueueModel(model, false); /* First/new-source imports are checked after Unity publishes its native inventory. */ }
            catch (Exception error)
            {
                foreach (var pair in saved) importer.AddRemap(pair.Key, pair.Value);
                foreach (var key in added) importer.RemoveRemap(key);
                if (File.Exists(manifest)) Debug.LogWarning("Character Designer material import: " + error.Message);
            }
        }
        static void ProcessModel(string model, bool ensureImport, bool verifyOnly = false, bool throwOnFailure = false)
        {
            if (UndoRestores.ContainsKey(model) && !verifyOnly) { QueueModel(model, false); return; }
            if (verification != null && verification.Model != model) { QueueModel(model, ensureImport); return; }
            if (InFlight.Contains(model)) { QueueModel(model, ensureImport); return; }
            Context context;
            try { context = ReadContext(model, true); ValidateImportedBindings(context); verifyOnly |= PreserveRestoredPublication(context); }
            catch (PublicationPendingException) { if (throwOnFailure) throw; return; }
            catch (Exception error) { if (throwOnFailure) throw; WriteDeclaredFailures(model, error.Message); return; }
            try
            {
                if (!verifyOnly && context.bindings.Count + context.simplified.Count > 0 && SessionState.GetString(SignatureKey(model), "") != context.signature)
                {
                    var importer = AssetImporter.GetAtPath(model) as ModelImporter;
                    Need(importer != null && !EditorUtility.IsDirty(importer), "The model has pending Inspector changes; apply or revert them before matching.");
                    var previous = new Dictionary<AssetImporter.SourceAssetIdentifier, UnityEngine.Object>();
                    var added = new HashSet<AssetImporter.SourceAssetIdentifier>();
                    bool changed = false;
                    InFlight.Add(model);
                    try
                    {
                        var map = importer.GetExternalObjectMap();
                        foreach (Binding binding in context.bindings.Concat(context.simplified))
                        {
                            var key = new AssetImporter.SourceAssetIdentifier(typeof(Material), binding.source);
                            if (map.TryGetValue(key, out var value)) previous[key] = value; else added.Add(key);
                        }
                        foreach (Binding binding in context.bindings)
                        {
                            var key = new AssetImporter.SourceAssetIdentifier(typeof(Material), binding.source);
                            if (!map.TryGetValue(key, out var value) || value != binding.material) { importer.AddRemap(key, binding.material); changed = true; }
                        }
                        foreach (Binding binding in context.simplified)
                        {
                            var key = new AssetImporter.SourceAssetIdentifier(typeof(Material), binding.source);
                            if (binding.material != null && map.TryGetValue(key, out var value) && value == binding.material) { importer.RemoveRemap(key); changed = true; }
                        }
                        if (changed) importer.SaveAndReimport();
                        else if (ensureImport) AssetDatabase.ImportAsset(model, ImportAssetOptions.ForceUpdate |
                            (verification != null && verification.Model == model ? ImportAssetOptions.ForceSynchronousImport : ImportAssetOptions.Default));
                    }
                    catch
                    {
                        if (changed)
                        {
                            importer = AssetImporter.GetAtPath(model) as ModelImporter;
                            if (importer != null)
                            {
                                foreach (var pair in previous) importer.AddRemap(pair.Key, pair.Value);
                                foreach (var key in added) importer.RemoveRemap(key);
                                importer.SaveAndReimport();
                            }
                        }
                        throw;
                    }
                    finally { InFlight.Remove(model); }
                    Context current = ReadContext(model, true);
                    Need(current.hash == context.hash && current.manifestHash == context.manifestHash, "The character publication changed during material import.");
                }
                bool allMatched = true;
                foreach (Binding binding in context.bindings.Concat(context.verificationOnly))
                {
                    Request identity = ReceiptIdentity(context, binding.source);
                    string bindingError = null;
                    try { VerifyBinding(context, binding); }
                    catch (Exception error) { bindingError = error.Message; }
                    if (bindingError == null) WriteReceipt(identity, binding, context.hash, "matched", "", context.manifestHash);
                    else
                    {
                        allMatched = false; WriteReceipt(identity, binding, context.hash, "failed", bindingError, context.manifestHash);
                        if (!context.verificationOnly.Contains(binding))
                        {
                            if (!binding.manual) SessionState.EraseString(SignatureKey(model)); // A later model/material event can retry the still-authoritative report.
                            Debug.LogWarning("Character Designer material verification: " + bindingError);
                        }
                    }
                }
                foreach (Binding binding in context.simplified)
                {
                    var importer = AssetImporter.GetAtPath(model) as ModelImporter;
                    bool cleared = importer != null && !importer.GetExternalObjectMap().ContainsKey(new AssetImporter.SourceAssetIdentifier(typeof(Material), binding.source));
                    if (!cleared) allMatched = false;
                    WriteReceipt(ReceiptIdentity(context, binding.source), binding, context.hash, "failed", cleared ? "Using Simplified: the owned Unity material remap is cleared for this export." : "Review: this source has a different or unavailable material remap; it was preserved.", context.manifestHash);
                }
                if (allMatched && context.bindings.Count + context.simplified.Count + context.verificationOnly.Count > 0) SessionState.SetString(SignatureKey(model), context.signature);
            }
            catch (Exception error)
            {
                if (throwOnFailure) throw;
                Debug.LogWarning("Character Designer material verification: " + error.Message);
            }
        }
        static Request ReceiptIdentity(Context context, string source)
        {
            string path = ReceiptPath(context.assetId, source);
            if (File.Exists(path))
            {
                try
                {
                    var old = ReadJson(path, 65536).ToObject<Record>();
                    if (old.schema == ReceiptSchema && SamePath(old.project_root, Root) && old.model_path == context.model && old.export_asset_id == context.assetId && old.source_material == source) return old;
                }
                catch (Exception) { }
            }
            return new Request { project_root = Root, model_path = context.model, export_asset_id = context.assetId, source_material = source, request_id = "", created_unix = Now };
        }
        static Context ReadContext(string model, bool withBindings)
        {
            string full = ModelPath(model);
            JObject json = ReadJson(ManifestPath(model), 32 * 1024 * 1024, out string digest);
            Need(Text(json, "schema") == ManifestSchema && json["ok"]?.Type == JTokenType.Boolean && json["ok"].Value<bool>(), "The adjacent Character Designer manifest is unsupported or incomplete.");
            string id = Text(json, "asset_id"), filename = Text(json, "filename");
            Need(Hex(id, 32) && filename == Path.GetFileName(model), "The model filename or export profile does not match its manifest.");
            Need(json["files"] is JObject files && files[filename]?.Type == JTokenType.String, "The manifest has no owned FBX checksum.");
            string expected = ((JObject)json["files"])[filename].Value<string>();
            Need(Hex(expected, 64), "Invalid owned FBX checksum.");
            string hash = HashFile(full);
            if (!string.Equals(hash, expected, StringComparison.OrdinalIgnoreCase)) throw new PublicationPendingException();
            Need(json["materials"] is JArray inventory && inventory.Count <= 4096, "The manifest has no bounded material inventory.");
            var sources = new HashSet<string>(StringComparer.Ordinal);
            foreach (JToken value in (JArray)json["materials"])
                Need(value.Type == JTokenType.String && !string.IsNullOrEmpty(value.Value<string>()) && !value.Value<string>().Any(char.IsControl) && sources.Add(value.Value<string>()), "The manifest material inventory contains invalid or duplicate names.");
            var context = new Context { model = model, assetId = id, hash = hash, manifestHash = digest, signature = digest + ":" + hash, sources = sources, bindings = new List<Binding>(), simplified = new List<Binding>(), verificationOnly = new List<Binding>() };
            if (!withBindings) return context;
            var simple = new HashSet<string>(StringComparer.Ordinal);
            if (json["simple_materials"] != null)
            {
                Need(json["simple_materials"] is JArray, "Invalid simplified material inventory.");
                foreach (JToken value in (JArray)json["simple_materials"])
                {
                    Need(value is JObject, "Invalid simplified material record.");
                    string source = Text((JObject)value, "material");
                    Need(sources.Contains(source) && simple.Add(source), "A simplified material is missing or duplicated in the export inventory.");
                }
            }
            var declared = new Dictionary<string, Binding>(StringComparer.Ordinal);
            if (json["unity_material_matches"] != null)
            {
                Need(json["unity_material_matches"] is JArray matches && matches.Count <= sources.Count, "Invalid Unity material match inventory.");
                foreach (JToken token in (JArray)json["unity_material_matches"])
                {
                    Need(token is JObject, "Invalid Unity material match entry.");
                    JObject match = (JObject)token;
                    string source = Text(match, "source_material");
                    Need(SamePath(Text(match, "project_root"), Root) && Text(match, "export_asset_id") == id && sources.Contains(source) && !simple.Contains(source) && !declared.ContainsKey(source), "A Unity material match has a foreign, missing, duplicated or simplified source.");
                    declared.Add(source, new Binding { source = source, guid = Text(match, "material_guid"), local = Integer(match, "material_local_id") });
                }
            }
            // A new explicit choice may be newer than this unchanged manifest. A receipt from another FBX version cannot override a new export.
            foreach (string source in sources)
            {
                string path = ReceiptPath(id, source);
                if (!File.Exists(path)) continue;
                try
                {
                    var receipt = ReadJson(path, 65536).ToObject<Record>();
                    bool owned = receipt.schema == ReceiptSchema && (receipt.status == "matched" || receipt.status == "failed") && SamePath(receipt.project_root, Root) && receipt.model_path == model && receipt.export_asset_id == id && receipt.source_material == source && Hex(receipt.material_guid, 32) && receipt.material_local_id != 0;
                    bool samePublication = receipt.fbx_sha256 == hash && receipt.manifest_sha256 == digest;
                    if (owned && samePublication && receipt.explicit_choice)
                    {
                        var latest = new Binding { source = source, guid = receipt.material_guid, local = receipt.material_local_id, path = receipt.material_path, name = receipt.material_name, manual = true };
                        if (receipt.status == "matched") declared[source] = latest;
                        else
                        {
                            // A failed latest manual choice also suppresses an older match/Simple report, without replaying it.
                            declared.Remove(source); context.verificationOnly.Add(latest);
                        }
                    }
                    else if (owned && simple.Contains(source))
                    {
                        var binding = new Binding { source = source, guid = receipt.material_guid, local = receipt.material_local_id, path = AssetDatabase.GUIDToAssetPath(receipt.material_guid), name = receipt.material_name };
                        if (binding.path.StartsWith("Assets/", StringComparison.Ordinal) && binding.path.EndsWith(".mat", StringComparison.OrdinalIgnoreCase) && !(AssetImporter.GetAtPath(binding.path) is ModelImporter))
                        {
                            GuardPath(Path.Combine(Root, binding.path));
                            binding.material = AssetDatabase.LoadAllAssetsAtPath(binding.path).OfType<Material>().SingleOrDefault(value => AssetDatabase.TryGetGUIDAndLocalFileIdentifier(value, out string guid, out long local) && guid == binding.guid && local == binding.local);
                            if (binding.material != null && EditorUtility.IsDirty(binding.material)) binding.material = null;
                            KnownMaterialPaths.Add(binding.path);
                        }
                        context.simplified.Add(binding);
                    }
                    else if (owned && !simple.Contains(source) && samePublication)
                    {
                        var existing = new Binding { source = source, guid = receipt.material_guid, local = receipt.material_local_id, path = receipt.material_path, name = receipt.material_name };
                        if (receipt.status == "matched") declared[source] = existing;
                        else if (!declared.ContainsKey(source)) context.verificationOnly.Add(existing); // A valid report still owns automatic retry intent.
                    }
                }
                catch (Exception) { /* Invalid receipts never establish a pairing. */ }
            }
            foreach (Binding binding in declared.Values)
            {
                Need(Hex(binding.guid, 32) && binding.local != 0, "Invalid Unity material GUID or local ID.");
                binding.path = AssetDatabase.GUIDToAssetPath(binding.guid);
                Need(!string.IsNullOrEmpty(binding.path) && binding.path.StartsWith("Assets/", StringComparison.Ordinal) && binding.path.EndsWith(".mat", StringComparison.OrdinalIgnoreCase), "Choose an existing .mat Material under this project's Assets.");
                Need(!(AssetImporter.GetAtPath(binding.path) is ModelImporter), "The selected Unity material must be external to an imported model.");
                GuardPath(Path.Combine(Root, binding.path));
                binding.material = AssetDatabase.LoadAllAssetsAtPath(binding.path).OfType<Material>().SingleOrDefault(value => AssetDatabase.TryGetGUIDAndLocalFileIdentifier(value, out string guid, out long local) && guid == binding.guid && local == binding.local);
                Need(binding.material != null && !EditorUtility.IsDirty(binding.material), "The selected Unity material is missing or has unsaved edits.");
                context.bindings.Add(binding);
                KnownMaterialPaths.Add(binding.path);
            }
            foreach (Binding binding in context.verificationOnly)
            {
                // Missing/deleted targets remain failed evidence; resolving them must never create a new remap.
                binding.path = AssetDatabase.GUIDToAssetPath(binding.guid);
                if (!string.IsNullOrEmpty(binding.path) && binding.path.StartsWith("Assets/", StringComparison.Ordinal) && binding.path.EndsWith(".mat", StringComparison.OrdinalIgnoreCase) && !(AssetImporter.GetAtPath(binding.path) is ModelImporter))
                {
                    GuardPath(Path.Combine(Root, binding.path));
                    binding.material = AssetDatabase.LoadAllAssetsAtPath(binding.path).OfType<Material>().SingleOrDefault(value => AssetDatabase.TryGetGUIDAndLocalFileIdentifier(value, out string guid, out long local) && guid == binding.guid && local == binding.local);
                    if (binding.material != null && EditorUtility.IsDirty(binding.material)) binding.material = null;
                    KnownMaterialPaths.Add(binding.path);
                }
            }
            return context;
        }
        static Binding Identify(Material material, string source)
        {
            Need(material != null && EditorUtility.IsPersistent(material) && !EditorUtility.IsDirty(material), "Choose an existing saved Unity Material.");
            string path = AssetDatabase.GetAssetPath(material);
            Need(path.StartsWith("Assets/", StringComparison.Ordinal) && path.EndsWith(".mat", StringComparison.OrdinalIgnoreCase) && !(AssetImporter.GetAtPath(path) is ModelImporter), "Choose a saved .mat Material under Assets, not an embedded model material.");
            GuardPath(Path.Combine(Root, path));
            Need(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(material, out string guid, out long local) && Hex(guid, 32) && local != 0, "The Material has no persistent Unity identity.");
            KnownMaterialPaths.Add(path);
            return new Binding { source = source, guid = guid, local = local, path = path, name = material.name, material = material, manual = true };
        }
        static void VerifyBinding(Context context, Binding binding)
        {
            Need(binding.material != null && !EditorUtility.IsDirty(binding.material), "The previously selected Unity Material is missing or has unsaved edits.");
            RequireImportedSource(context.model, binding.source);
            var importer = AssetImporter.GetAtPath(context.model) as ModelImporter;
            Need(importer != null && importer.GetExternalObjectMap().TryGetValue(new AssetImporter.SourceAssetIdentifier(typeof(Material), binding.source), out var mapped) && mapped == binding.material, "The imported model does not retain this exact source material remap.");
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(context.model);
            Need(model != null && model.GetComponentsInChildren<Renderer>(true).Any(value => value.sharedMaterials.Any(material => material == binding.material)), "The imported model's renderers do not use the selected Unity Material.");
            Need(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(binding.material, out string guid, out long local) && guid == binding.guid && local == binding.local, "The selected Material identity changed during import.");
        }
        static void ValidateImportedBindings(Context context)
        {
            foreach (Binding binding in context.bindings.Concat(context.simplified).Concat(context.verificationOnly)) RequireImportedSource(context.model, binding.source);
        }
        static void RequireImportedSource(string model, string source)
        {
            var importer = AssetImporter.GetAtPath(model) as ModelImporter;
            if (importer == null) throw new SourceInventoryException("Import the owned FBX before matching its material.");
            var serialized = new SerializedObject(importer);
            var materials = serialized.FindProperty("m_Materials");
            if (materials == null || !materials.isArray || materials.arraySize > 4096) throw new SourceInventoryException("Unity's imported source material inventory is unavailable.");
            int count = 0;
            for (int i = 0; i < materials.arraySize; i++)
            {
                var entry = materials.GetArrayElementAtIndex(i);
                var name = entry.FindPropertyRelative("name");
                var type = entry.FindPropertyRelative("type");
                var assembly = entry.FindPropertyRelative("assembly");
                if (name?.stringValue == source && type?.stringValue == "UnityEngine:Material" && assembly?.stringValue == typeof(Material).Assembly.GetName().Name) count++;
            }
            if (count != 1) throw new SourceInventoryException("The exact source material is missing or ambiguous in Unity's imported FBX.");
        }
        static void WriteDeclaredFailures(string model, string error)
        {
            try
            {
                // Failure status needs the same verified publication boundary as success; a mid-publication FBX is left pending.
                Context context = ReadContext(model, false);
                JObject json = ReadJson(ManifestPath(model), 32 * 1024 * 1024, out string digest);
                Need(digest == context.manifestHash, "The character manifest changed during failure verification.");
                var desired = new Dictionary<string, Binding>(StringComparer.Ordinal);
                if (json["unity_material_matches"] is JArray matches)
                foreach (JObject match in matches.OfType<JObject>())
                {
                    string source = Text(match, "source_material");
                    if (!context.sources.Contains(source) || !SamePath(Text(match, "project_root"), Root) || Text(match, "export_asset_id") != context.assetId) continue;
                    desired[source] = new Binding { source = source, guid = Text(match, "material_guid"), local = Integer(match, "material_local_id"), path = "" };
                }
                foreach (string source in context.sources)
                {
                    string path = ReceiptPath(context.assetId, source);
                    if (!File.Exists(path)) continue;
                    try
                    {
                        var receipt = ReadJson(path, 65536).ToObject<Record>();
                        if (receipt.schema == ReceiptSchema && SamePath(receipt.project_root, Root) && receipt.model_path == model && receipt.export_asset_id == context.assetId && receipt.source_material == source && receipt.fbx_sha256 == context.hash && receipt.manifest_sha256 == context.manifestHash)
                            desired[source] = new Binding { source = source, guid = receipt.material_guid, local = receipt.material_local_id, path = receipt.material_path, name = receipt.material_name, manual = receipt.explicit_choice };
                    }
                    catch (Exception) { }
                }
                foreach (var pair in desired)
                {
                    WriteReceipt(ReceiptIdentity(context, pair.Key), pair.Value, context.hash, "failed", error, context.manifestHash);
                    if (!pair.Value.manual) SessionState.EraseString(SignatureKey(model));
                }
            }
            catch (Exception) { }
        }
        static string ManifestPath(string model) => Path.ChangeExtension(ModelPath(model), ".cdesigner.json");
        static string ModelPath(string model)
        {
            Need(!string.IsNullOrWhiteSpace(model) && model.StartsWith("Assets/", StringComparison.Ordinal) && model.EndsWith(".fbx", StringComparison.OrdinalIgnoreCase) && !model.Contains('\\') && model.Split('/').All(part => part.Length > 0 && part != "." && part != ".." && part.IndexOf(':') < 0), "The model must be an owned FBX path under Assets.");
            string full = Path.GetFullPath(Path.Combine(Root, model)); GuardPath(full); return full;
        }
        static bool SamePath(string first, string second)
        {
            try { return Path.IsPathRooted(first) && Path.IsPathRooted(second) && string.Equals(Path.GetFullPath(first).TrimEnd('\\', '/'), Path.GetFullPath(second).TrimEnd('\\', '/'), StringComparison.OrdinalIgnoreCase); }
            catch (Exception) { return false; }
        }
        static void GuardPath(string path)
        {
            string full = Path.GetFullPath(path);
            Need(full.StartsWith(Root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase), "The material matching path leaves this Unity project.");
            for (string current = full; !SamePath(current, Root); current = Path.GetDirectoryName(current))
                if (File.Exists(current) || Directory.Exists(current)) Need((File.GetAttributes(current) & FileAttributes.ReparsePoint) == 0, "Material matching does not follow filesystem links.");
        }
        static JObject ReadJson(string path, long limit)
        {
            return ReadJson(path, limit, out _);
        }
        static JObject ReadJson(string path, long limit, out string digest)
        {
            GuardPath(path);
            Need(new FileInfo(path).Length <= limit, "The material matching JSON is too large.");
            byte[] bytes = File.ReadAllBytes(path);
            Need(bytes.LongLength <= limit, "The material matching JSON is too large.");
            digest = HashBytes(bytes);
            using (var input = new StringReader(new UTF8Encoding(false, true).GetString(bytes).TrimStart('\uFEFF')))
            using (var reader = new JsonTextReader(input) { DateParseHandling = DateParseHandling.None, MaxDepth = 64 })
            {
                var value = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                Need(!reader.Read(), "The material matching JSON has trailing content.");
                return value;
            }
        }
        static string Text(JObject json, string key)
        {
            Need(json[key]?.Type == JTokenType.String && !string.IsNullOrEmpty(json[key].Value<string>()), "Missing or invalid " + key + ".");
            return json[key].Value<string>();
        }
        static long Integer(JObject json, string key)
        {
            Need(json[key]?.Type == JTokenType.Integer, "Missing or invalid " + key + ".");
            return json[key].Value<long>();
        }
        static bool Hex(string value, int length) => value != null && value.Length == length && value.All(character => (character >= '0' && character <= '9') || (character >= 'a' && character <= 'f'));
        static string HashBytes(byte[] value)
        {
            using (var digest = SHA256.Create()) return BitConverter.ToString(digest.ComputeHash(value)).Replace("-", "").ToLowerInvariant();
        }
        static string HashFile(string path)
        {
            using (var input = File.OpenRead(path)) using (var digest = SHA256.Create()) return BitConverter.ToString(digest.ComputeHash(input)).Replace("-", "").ToLowerInvariant();
        }
        static void WriteJson(string path, Record record, bool replace)
        {
            WriteBytes(path, new UTF8Encoding(false).GetBytes(JsonUtility.ToJson(record, true)), replace);
        }
        static void WriteBytes(string path, byte[] data, bool replace)
        {
            GuardPath(path); Directory.CreateDirectory(Path.GetDirectoryName(path));
            string temporary = Path.Combine(Path.GetDirectoryName(path), "." + Guid.NewGuid().ToString("N") + ".tmp");
            try
            {
                File.WriteAllBytes(temporary, data);
                if (File.Exists(path)) { if (replace) File.Replace(temporary, path, null); }
                else File.Move(temporary, path);
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }
        static void Need(bool condition, string message) { if (!condition) throw new InvalidDataException(message); }
    }

    /// <summary>Native Material picker and preview. It owns no user selection, scene objects or shared material edits.</summary>
    public sealed class CharacterMaterialMatchWindow : EditorWindow
    {
        CharacterMaterialMatching.Request request;
        Material material;
        UnityEditor.Editor preview;
        Vector2 scrollPosition;
        bool requestedApply, loadingRendered;
        internal bool Terminal;
        internal Material SelectedMaterial => material;
        internal bool ApplyReady => requestedApply && loadingRendered;
        internal void Begin(CharacterMaterialMatching.Request value)
        {
            request = value; titleContent = new GUIContent("Match Unity Material"); minSize = new Vector2(360, 380);
        }
        void OnGUI()
        {
            if (request == null) { EditorGUILayout.HelpBox("Waiting for a Blender material request.", MessageType.Info); return; }
            using (var body = new EditorGUILayout.ScrollViewScope(scrollPosition))
            {
                scrollPosition = body.scrollPosition;
                EditorGUILayout.LabelField("Match Unity Material", EditorStyles.boldLabel);
                EditorGUILayout.LabelField(request.source_material, EditorStyles.wordWrappedLabel);
                EditorGUILayout.LabelField(request.model_path, EditorStyles.wordWrappedMiniLabel);
                EditorGUILayout.Space(6);
                if (requestedApply)
                {
                    EditorGUILayout.HelpBox("Applying material match and checking the imported model...", MessageType.Info);
                    if (Event.current.type == EventType.Repaint) loadingRendered = true;
                    Repaint();
                }
                else
                {
                    EditorGUI.BeginChangeCheck();
                    var selected = (Material)EditorGUILayout.ObjectField("Unity Material", material, typeof(Material), false);
                    if (EditorGUI.EndChangeCheck())
                    {
                        if (preview != null) DestroyImmediate(preview);
                        material = selected; preview = material != null ? UnityEditor.Editor.CreateEditor(material) : null;
                    }
                    if (preview != null && preview.HasPreviewGUI())
                        preview.OnInteractivePreviewGUI(GUILayoutUtility.GetRect(120, 210, GUILayout.ExpandWidth(true)), GUIStyle.none);
                    else GUILayout.Space(210);
                    EditorGUILayout.HelpBox(CharacterMaterialMatching.EditorBusy ? "Waiting for Unity to finish Play Mode, compilation, import or build." : "Choose an existing saved Material. Its shader and settings stay unchanged.", MessageType.Info);
                }
            }
            using (new EditorGUILayout.HorizontalScope())
            {
                if (!requestedApply)
                    using (new EditorGUI.DisabledScope(material == null || CharacterMaterialMatching.EditorBusy))
                        if (GUILayout.Button("Apply Match", GUILayout.Height(28))) { requestedApply = true; loadingRendered = false; Repaint(); }
                if (GUILayout.Button("Cancel", GUILayout.Height(28))) CharacterMaterialMatching.CancelWindow(this);
            }
        }
        void OnDisable() { if (preview != null) DestroyImmediate(preview); preview = null; }
        void OnDestroy() { if (!Terminal) CharacterMaterialMatching.CancelWindow(this); }
    }

    public sealed class CharacterMaterialMatchPostprocessor : AssetPostprocessor
    {
        void OnPreprocessModel() => CharacterMaterialMatching.Preprocess(assetPath, (ModelImporter)assetImporter);
        static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
        {
            if (!CharacterMaterialMatching.MainEditor) return;
            CharacterMaterialMatching.MaterialAssetsChanged(imported.Concat(deleted).Concat(moved).Concat(movedFrom));
            foreach (string path in imported.Concat(moved))
            {
                if (path.EndsWith(".fbx", StringComparison.OrdinalIgnoreCase)) CharacterMaterialMatching.QueueModel(path, false);
                else if (path.EndsWith(".cdesigner.json", StringComparison.OrdinalIgnoreCase))
                    CharacterMaterialMatching.QueueModel(path.Substring(0, path.Length - ".cdesigner.json".Length) + ".fbx", true);
            }
        }
    }
}
