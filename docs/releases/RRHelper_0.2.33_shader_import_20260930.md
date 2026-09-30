# RR Helper 0.2.33 — Direct shader import and native Shader color tag

Material → Shader keeps list selection separate from importing. Its action is
always named Import Shader. One click creates or automatically reuses the
source's generated group and inserts an unconnected node, without an OK dialog.
Existing-group checkmarks remain. Refresh stays separate and confirms before
rebuilding a group shared by other materials.

Created, successfully reused and refreshed local editable groups now use
Blender's native `color_tag = 'SHADER'`. Nodes retain normal Blender styling;
no custom header or success colors are applied. Linked/read-only groups remain
shared and unchanged. Versions without a color-tag property keep native defaults.

Validation on Blender 5.2.0 LTS: 31 material/shader tests and 63 registration
lifecycle checks passed in isolated factory scenes. Coverage includes direct
operator invocation, automatic reuse, fixed button naming, selection-only list
behavior, native tags, source/target graph preservation and failed-operation
cleanup. Package: `dist/rr_helper-0.2.33.zip`.

All three local deployment checks passed with zero differing files. The running
Builder6 session was refreshed to 0.2.33. Its existing Material Gold group already
had the SHADER tag; this was verified without changing material/group graphs or
adding nodes. The working blend was not saved by this update.

Local source and deployment only; no Git commit or push.
