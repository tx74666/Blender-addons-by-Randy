# Character Designer 0.77.23 — Unity Material matching

2026-10-11，接手原 Character 工作。Blender 的 Unity Export 材質列表可對每個來源材質呼叫 Unity 原生 Material 選擇與預覽窗口。明確選擇沿用專案、匯出 profile 和來源材質身份；只有 native importer remap 與已載入 Renderer 都確認一致才顯示 Matched。保留既有 Material 的 Shader 與參數。

0.77.23 修復原生 Undo 只還原記憶體映射、沒有同步 `.meta` 和 Renderer 的缺口。保存完整 importer Undo snapshot，限定本功能的 group/name/importer/publication，對已還原映射執行正式重匯入，再只驗證 receipt。其他 Undo 不觸發此同步；失敗與忙碌時保留恢復狀態，不重播舊配對覆蓋撤銷结果。

Unity 6000.5.9f1 原生驗收：嚴格 Undo/Redo 1 case PASS（`f3976b5f3ee241eabc3f362a9674c879`），Undo 與 Redo 的 map、磁碟 metadata、Renderer 各自確認；11 項材質回歸 PASS（`6f48c2b28df348ba9edc88695180d6e1`），涵蓋取消/逾時/重複輸入/手動優先/Simple 與 Original/缺少來源或目標/其他 remap 保留/四個真實匯出向量/非 FBX 靜默處理。未透過 QA 強制重匯入製造 Undo PASS。

實際 Cosha 的 `Crystal_Blue → Assets/Art/Materials/Crystal/M_CrystalBlue.mat` 已原生 importer 與 Renderer 確認，取消、逾時及 Unity/Blender 窗口排版也已驗證。實際 authored Cosha 的再次匯出和應用重啟另行驗收；四個真實測試匯出向量不是這兩項的替代證據。

原有 980 筆完整 Undo prefix 保留，僅追加一個自有測試 importer snapshot，最終 981 筆/cursor 981/沒有待 Redo。兩個 normal Scene、兩個 Preview Scene、Selection、未保存的 Adventure、Building 自有 clean fixture 與磁碟內容保持；沒有 Scene save/switch/Play。Console 只有既有 Vivox 401、其他任務 Music 警告、MCP transport 重送抑制及五個預期錯配驗證警告，沒有新增本功能 project error。

套件使用 0.77.22 的完整 hash projection，157 項中只改版本與兩個 Material Matching C#，其他 154 項逐位元相同，未發佈無關 Dress 草稿。Blender 5.2 和 X 專案磁碟副本部署後各 157 項零差異；兩個 Unity C# 與 canonical 相同。正在編輯的 Blender 保留 0.77.22 註冊執行狀態與 X.blend 未保存姿勢，沒有盲目 refresh/reload/save。

套件：`dist/character_designer-0.77.23.zip`，SHA-256 `DD2D4903A7C45B5ED7A3B442E132DD04D13E34FBCA099864EAAADA08E0165F1E`。Projection SHA-256 `ec38f256ebf5847994b7d1563bff934cdc033929753eb3f735308f74ec84b32a`。

完整本機證據保存在 `D:/Unity Projects/RandomRealm2/.codex/task-staging/character2-material-match-20261010/` 的 native-verification、evidence 及 NATIVE-VERIFICATION.md。原 Character 會話已停止並歸檔；Unity 已明確歸還並交接下一位 writer。
