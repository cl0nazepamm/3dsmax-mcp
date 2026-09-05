import unittest
from unittest.mock import patch
from maxmcp.tools import poly_edit


class CageVertexTests(unittest.TestCase):
    def test_all_vertex_routes_use_base_mesh_with_explicit_world_coordinates(self):
        cases=[({'action':'get'},'META|16|0|0|1'),
               ({'action':'move','indices':[1],'offset':[0,0,1],'falloff':2},'OK|1|0|false'),
               ({'action':'set','indices':[1],'positions':[[1,2,3]]},'OK|1|false'),
               ({'action':'conform','indices':[1],'target':'Profile'},'OK|1|0|spline|false')]
        for args,reply in cases:
            with self.subTest(action=args['action']), patch.object(poly_edit.client,'send_command',return_value={'result':reply}) as send:
                result=poly_edit.edit_vertices(name='Chair',**args)
                self.assertNotIn('error',result)
                script=send.call_args.args[0]
                self.assertIn('local mesh = obj.baseobject',script)
                self.assertIn('in coordsys world (',script)
                for op in ['getNumVerts','getVert','setVert']:
                    self.assertNotIn('polyop.'+op+' obj',script)
                self.assertIn('polyop.getNumVerts mesh',script)
