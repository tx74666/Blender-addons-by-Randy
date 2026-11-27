# Character Designer 0.77.270 · Current File Pose Library

Choose **Current File** in the Asset Browser and open the right sidebar to find **Pose Library**. Browse poses in the thumbnail grid. The panel provides **Save Pose**, **Apply Pose**, **Organize…**, and **Delete…** for local poses. Use Pose Mode to apply a pose to the character.

**Organize…** renames the selected local pose and assigns a group such as Hands or Full Body. **Groups** searches the native thumbnail grid using the stored group tag. Existing native single-frame bone pose assets can be organized alongside new captures. Save the project to keep changes, including deletions.

**Delete…** confirms the selected pose by name and refuses Actions with real users such as active bindings or NLA tracks. The shared Pose Library remains a separate asset source. The new panel stores its groups in Action asset metadata and only writes poses to the current project.

Isolated Blender 5.2 verification covered add-on refresh, evaluated pose capture and application, rename and group persistence after reopening, stale and in-use deletion refusals, and deletion persistence after saving. The four shared pose files and catalog matched their original hashes. [Verification and installation](<D:/Blender/Projects/Character/X/validation/current_file_pose_library_20261127/delivery270.json>)
