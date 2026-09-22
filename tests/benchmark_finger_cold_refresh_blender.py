"""Count actual mesh reads in cold multi-guide and warmed Bend CPU work."""
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import bound_fixture, character_designer, C, bank, definition
from character_designer import finger_definition_ui as ui, finger_bone_tools as bones

character_designer.register()
obj, rig, _ = bound_fixture()
obj.character_designer_finger_bank.visible_digits = set(bank.detect.DIGITS)
C.view_layer.update()
ui.redraw()
result = {'version': character_designer.bl_info['version'], 'vertices': len(obj.data.vertices)}
for name, action in (('cold_five', lambda: ui.display_frames(C)), ('bend_after_reference', lambda: bones.show_bend(C, all_fingers=True))):
    with patch.object(definition, '_snapshot', wraps=definition._snapshot) as snapshots, patch.object(definition, '_topology', wraps=definition._topology) as topology:
        started = time.perf_counter()
        action()
        result[name] = dict(ms=(time.perf_counter()-started)*1000, snapshots=snapshots.call_count, topology_hashes=topology.call_count)
print('FINGER_COLD_BENCHMARK', json.dumps(result), flush=True)
