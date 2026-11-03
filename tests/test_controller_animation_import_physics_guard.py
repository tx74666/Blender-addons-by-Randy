"""Ordinary Add physics refusal precedes every mutation boundary; no Blender."""
import importlib
import sys
from types import ModuleType,SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch

import test_animation_worklist as fixture


class PhysicsImportGuard(fixture.WorklistTests):
    def setup_add_boundary(self,*,existing_rig=False,rigging=False):
        self.backend=ModuleType(self.worklist.__package__+'.controller_animation')
        self.backend._require_private_evaluation_idle=Mock(side_effect=ValueError('Pause Hair Physics first.'))
        p=patch.dict(sys.modules,{self.backend.__name__:self.backend});p.start();self.addCleanup(p.stop)
        if existing_rig:
            self.saved.rig=fixture.ID(data=fixture.ID(use_fake_user=False),animation_data=None)
        target=importlib.import_module(self.worklist.__package__+'.animation_worklist_target')
        patches=[patch.object(self.worklist,'_composition_add_route',return_value=(False,None)),
                 patch.object(self.worklist,'_prepared',return_value=({}, {}, {}, {'rigging':{}} if rigging else {}, '',None)),
                 patch.object(target,'is_adopted',return_value=False),
                 patch.object(self.worklist,'_rigging_module',return_value=NS() if rigging else None),
                 patch.object(self.worklist.source,'_context_state',create=True),
                 patch.object(self.worklist.source,'import_source',create=True),
                 patch.object(self.worklist.source,'_import_prepared_source',create=True),
                 patch.object(self.worklist,'_capture_rig'),patch.object(self.worklist,'_linked_baseline')]
        self.boundaries=[p.start() for p in patches]
        for p in patches:self.addCleanup(p.stop)

    def assert_no_mutation_boundaries(self):
        for mock in self.boundaries[4:]:mock.assert_not_called()
        self.assertFalse(self.saved.rig and self.saved.rig.data.use_fake_user)

    def test_physics_guard_rejects_new_ordinary_before_context_import_capture_or_baseline(self):
        self.setup_add_boundary()
        with self.assertRaisesRegex(ValueError,'Pause Hair Physics'):
            self.worklist.add(self.context,self.saved.catalog[0].clip_key)
        self.backend._require_private_evaluation_idle.assert_called_once_with()
        self.assert_no_mutation_boundaries()

    def test_physics_guard_rejects_existing_rig_before_fakeuser_capture_pin(self):
        self.setup_add_boundary(existing_rig=True)
        with self.assertRaisesRegex(ValueError,'Pause Hair Physics'):
            self.worklist.add(self.context,self.saved.catalog[0].clip_key)
        self.backend._require_private_evaluation_idle.assert_called_once_with()
        self.assert_no_mutation_boundaries()

    def test_paused_admission_can_reach_existing_context_boundary_without_duplicate_gate(self):
        self.setup_add_boundary()
        self.backend._require_private_evaluation_idle.side_effect=None
        self.boundaries[4].side_effect=RuntimeError('reached unchanged context boundary')
        with self.assertRaisesRegex(RuntimeError,'reached unchanged context'):
            self.worklist.add(self.context,self.saved.catalog[0].clip_key)
        self.backend._require_private_evaluation_idle.assert_called_once_with()
        for mock in self.boundaries[5:]:mock.assert_not_called()

    def test_rigging_special_route_does_not_enter_generic_physics_gate(self):
        self.setup_add_boundary(rigging=True)
        self.boundaries[4].side_effect=RuntimeError('reached dedicated rigging context boundary')
        with self.assertRaisesRegex(RuntimeError,'dedicated rigging context'):
            self.worklist.add(self.context,self.saved.catalog[0].clip_key)
        self.backend._require_private_evaluation_idle.assert_not_called()
        for mock in self.boundaries[5:]:mock.assert_not_called()


def load_tests(loader,_suite,_pattern):
    return unittest.TestSuite(PhysicsImportGuard(name) for name in loader.getTestCaseNames(PhysicsImportGuard)
        if name.startswith('test_physics_guard_') or name.startswith('test_paused_admission_') or name.startswith('test_rigging_special_'))


if __name__=='__main__':unittest.main()
