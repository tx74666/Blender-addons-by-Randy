"""Fast geometry contracts; these do not claim to test Blender's solver."""
import importlib.util
import math
import struct
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('calibration_math', Path(__file__).resolve().parents[1]/'addons/character_designer/body_calibration_math.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class CalibrationGeometry(unittest.TestCase):
    def test_native_candidate_is_stable_after_float32_storage(self):
        examples = [
            ((.11077477782964706,.06768570840358734,1.6531343460083008),
             (.3262162506580353,.06925076246261597,1.4328820705413818),
             (.5040974020957947,.06748819351196289,1.2489454746246338)),
            ((1.1439851522445679,1.5167901515960693,.8362446427345276),
             (1.245132565498352,1.5538907051086426,1.1496816873550415),
             (1.3558835983276367,1.5963865518569946,1.4591623544692993)),
        ]
        for coordinates in examples:
            for scale in (.01,1.,100.):
                for sign in (-1,1):
                    s,e,w = [struct.unpack('fff',struct.pack('fff',v[0]*scale*sign,v[1]*scale,v[2]*scale)) for v in coordinates]
                    p=m.solve(s,e,w,(0,1,0),native_precision=True)
                    self.assertFalse(p['errors'],p)
                    self.assertEqual(p['joint'],struct.unpack('fff',struct.pack('fff',*p['joint'])))
                    q=m.solve(s,p['joint'],w,(0,1,0),native_precision=True)
                    self.assertFalse(q['errors'],q)
                    self.assertEqual(p['joint'],q['joint'])
                    self.assertEqual(q['shift'],0)
                    self.assertLess(m.length(m.sub(p['z'],q['z'])),1e-10)
                    self.assertLessEqual(q['direction_error_degrees'],5.)
                    self.assertLess(max(map(abs,p['length_changes'])),2e-6)

    def test_preserves_aligned_joint_exactly(self):
        p=m.solve((0,0,0),(.4,.1,0),(1,0,0),(0,1,0))
        self.assertEqual(p['joint'],(.4,.1,0))
        self.assertEqual(p['shift'],0)
    def test_reorientation_keeps_endpoints_and_lengths_at_all_scales(self):
        for scale in (1e-5,1,1e4):
            for angle in (90,180):
                s,e,w=(0,0,0),(.4*scale,.02*scale,0),(scale,0,0)
                target=(0,math.cos(math.radians(angle)),math.sin(math.radians(angle)))
                p=m.solve(s,e,w,target,max_shift=.5)
                self.assertAlmostEqual(m.length(m.sub(p['joint'],s))/scale,m.length(e)/scale,places=7)
                self.assertAlmostEqual(m.length(m.sub(w,p['joint']))/scale,m.length(m.sub(w,e))/scale,places=7)
                self.assertAlmostEqual(math.degrees(math.acos(m.dot(p['direction'],target))),5,places=6)
    def test_straight_is_explicit(self):
        with self.assertRaisesRegex(ValueError,'unstable'):m.solve((0,0,0),(.4,0,0),(1,0,0),(0,1,0))
        p=m.solve((0,0,0),(.4,0,0),(1,0,0),(0,1,0),allow_bend=True)
        self.assertGreater(p['length_changes'][0],0)
        self.assertAlmostEqual(p['length_changes'][0],p['length_changes'][1])
        a,b=m.sub(p['joint'],(0,0,0)),m.sub((1,0,0),p['joint'])
        self.assertAlmostEqual(math.degrees(math.acos(m.dot(m.unit(a),m.unit(b)))),5,places=6)
        native=m.solve((0,0,0),(.4,0,0),(1,0,0),(0,1,0),allow_bend=True,native_precision=True)
        self.assertFalse(native['errors'])
        self.assertEqual(m.solve((0,0,0),native['joint'],(1,0,0),(0,1,0),native_precision=True)['shift'],0)
        rejected=m.solve((0,0,0),(.4,0,0),(1,0,0),(0,1,0),allow_bend=True,native_precision=True,max_length=0)
        self.assertIn('Bone length change exceeds the allowed limit.',rejected['errors'])
    def test_conflicting_limits_are_reported(self):
        p=m.solve((0,0,0),(.5,0,0),(1,0,0),(0,1,0),allow_bend=True,max_length=0,max_shift=0)
        self.assertEqual(len(p['errors']),2)
    def test_invalid_inputs_fail(self):
        for s,e,w,t in [((0,0,0),(0,0,0),(1,0,0),(0,1,0)),
                         ((0,0,0),(.5,.1,0),(0,0,0),(0,1,0)),
                         ((0,0,0),(.5,.1,0),(1,0,0),(1,0,0)),
                         ((0,0,0),(.5,float('nan'),0),(1,0,0),(0,1,0))]:
            with self.assertRaises(ValueError):m.solve(s,e,w,t)
    def test_orthonormal_right_handed_frames(self):
        for sign in (-1,1):
            p=m.solve((0,0,0),(.4*sign,.05,.02),(sign,0,-.1),(0,1,0))
            y=m.unit(p['joint']);z=p['z'];x=m.cross(y,z)
            self.assertAlmostEqual(m.dot(y,z),0)
            self.assertAlmostEqual(m.dot(x,m.cross(y,z)),1)


if __name__=='__main__':unittest.main()
