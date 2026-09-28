# Character Designer 0.62.11

Remove the two outer Previous/Next arrow buttons beside Forearm Twist's Current
loop field. Keep the numeric field's native arrows and Pick button. The existing
ring-index update callback and Previous/Next operators remain unchanged.

Validation: Python syntax compilation, release package verification, independent
read-only confirmation that both navigation paths call the same set_current
function. This only removes two draw calls; no deformation logic changes.
No additional Blender background regression was run (未验证); current UI draw
function is refreshed without restarting or clearing any active twist session.

Local release only; no commit or push. Character assets are not saved or edited.
