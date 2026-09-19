import unittest
from smg_api import plan_breakdown


class SonPlanBreakdownTest(unittest.TestCase):
    def test_subobjects_decimal_totals_and_unknown_are_preserved(self):
        objects=[]
        for scope, value in [('ППС-19.1','0.1'),('ППС-19.2','0.2')]:
            objects.append(dict(scope=scope,label=scope,values={kind:{'node':value,'missing':None,'zero':'0'} for kind in ('staff','rental','outstaff','total')}))
        result=plan_breakdown({'format':'son-v1','objects':objects})
        self.assertEqual(result['values']['staff']['node'],'0.3')
        self.assertEqual(result['values']['total']['zero'],'0')
        self.assertIsNone(result['values']['staff']['missing'])
        self.assertEqual(result['objects'],objects)

    def test_legacy_plan_does_not_invent_a_breakdown(self):
        self.assertIsNone(plan_breakdown({'filename':'Перевахта.xlsx'}))
