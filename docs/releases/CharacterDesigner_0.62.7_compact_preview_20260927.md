# Character Designer 0.62.7 — compact wrist preview and redraw performance

Hands now shows one XYZ frame at each wrist, one continuous up/down arc, the cyan
forearm direction arrow, and the hand/forearm angle. The palm rectangle, duplicate
current frame, multiple dashed arcs and long labels are removed. Details remains
available with axis meanings, hand/forearm angle and coordinate space; actionable
errors stay visible. The angle compares longitudinal axes, not Roll error.

The panel and viewport share transient display results. Redraws reuse the solved
candidate, generated line data and GPU batch. Display inputs include exact Rest,
settings, mappings, calibration records, referenced Basis coordinates/topology,
transforms and pose data where applicable. Graph updates, undo, redo and load clear
the cache. It is bounded and does not retain evaluated RNA. Preview, Apply, Confirm
and Generate still execute the original fresh validation; pose readiness is live.

An unaccepted Mesh reference uses the same pure wrist-frame calculation as an
accepted reference, so flipping it changes the single displayed XYZ frame before
acceptance. Pending references still block Apply/Confirm. A legacy reference that
fails on one side retains that wrist's current axes while the other side previews.

Blender 5.2.0 LTS validation: Display/cache 13 tests, Hands 10 tests, and calibration
28 tests passed (see final delivery logs). Read-only checks on the running CoshaRig
preserved native Rest, Pose, saved calibration records and object/bone counts.
Independent review identified and verified fixes for the two reference-display
cases above. No calibration/IK behavior or original character asset was changed.

Measured on CoshaRig: previous panel calculation median 36.13 ms, new warm-cache
median about 9.5 ms; overlay segment calculation 6.29 ms to about 0.67 ms. This
measures Python preparation, not overall viewport FPS. Cold panel calculation was
about 46 ms, so the benefit is repeated redraws with unchanged inputs. Per two hands,
line segments dropped from 756 to 60 and labels from 22 to 10. No new full-character
artistic motion or original Fingers acceptance claim is made by this release.
