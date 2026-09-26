import tempfile
import unittest
from pathlib import Path
import json
import xml.etree.ElementTree as ET
import numpy as np
from yield_tui import Data, Config, analyze, default_config, read_data, transformed, Dashboard, export_result, terminal_chart


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'sample.1.csv'
        self.x=np.linspace(0,.03,301)
        self.y=np.where(self.x<=.005,200000*self.x,1000+1000*(self.x-.005))
        np.savetxt(self.path,np.c_[self.x,self.y],delimiter=',',header='X,Y',comments='')
        self.d=read_data(self.path)
        self.c=Config(xmode='strain',yunit='MPa',fit_rows=(4,40))

    def test_analytic_intersection(self):
        r=analyze(self.d,self.c)
        # 200000*(x-.002) = 1000+1000*(x-.005)
        expected=1395/199000
        self.assertAlmostEqual(r['offset']['x'],expected,12)
        self.assertAlmostEqual(r['offset']['y'],200000*(expected-.002),8)
        self.assertGreater(r['fit']['r_squared'],.999999)

    def test_intercept_not_forced_through_zero(self):
        self.d.raw[:,1]+=42
        r=analyze(self.d,self.c)
        self.assertAlmostEqual(r['fit']['intercept'],42,9)
        self.assertAlmostEqual(r['offset']['x'],1395/199000,12)

    def test_unknown_blocks_offset(self):
        self.c.xmode='unknown'
        self.assertIsNone(analyze(self.d,self.c)['offset'])

    def test_percent_conversion(self):
        self.d.raw[:,0]*=100
        self.c.xmode='percent';self.c.xfactor=.01
        self.assertAlmostEqual(analyze(self.d,self.c)['offset']['x'],1395/199000,12)

    def test_negative_axes(self):
        self.d.raw*=-1
        c=default_config(self.d)
        c.xmode='strain';c.fit_rows=(4,40)
        self.assertEqual((c.sx,c.sy),(-1,-1))
        self.assertAlmostEqual(analyze(self.d,c)['offset']['x'],1395/199000,12)

    def test_no_intersection_for_elastic_curve(self):
        self.d.raw[:,1]=200000*self.x
        self.assertIsNone(analyze(self.d,self.c)['offset'])

    def test_reversal_not_sorted(self):
        self.d.raw[50:,0]-=.1
        r=analyze(self.d,self.c)
        self.assertIsNone(r['offset'])
        self.assertTrue(any('回退' in w for w in r['warnings']))

    def test_manual_is_separate(self):
        self.c.yield_row=60
        r=analyze(self.d,self.c)
        self.assertEqual(r['manual_yield']['row'],60)
        self.assertIn('不等於真正降伏點',r['candidate']['method'])
        self.assertNotEqual(r['manual_yield']['x'],r['offset']['x'])

    def test_zero_preserves_raw(self):
        original=self.d.raw.copy();self.c.zero_row=5
        x,y=transformed(self.d,self.c)
        self.assertEqual(x[3],0);self.assertEqual(y[3],0)
        np.testing.assert_array_equal(self.d.raw,original)

    def test_bad_fit_rejected(self):
        self.c.fit_rows=(3,5)
        with self.assertRaises(ValueError):analyze(self.d,self.c)

    def test_export_and_dashboard_commands(self):
        app=Dashboard([self.path],str(Path(self.tmp.name)/'out'),False)
        for cmd in ['x strain','y MPa','fit 4 40','yield 60','view 0 .01','rows 4 8','export']:
            app.command(cmd)
        files=list(Path(self.tmp.name).rglob('*.json'))
        self.assertEqual(len(files),1)
        r=json.loads(files[0].read_text(encoding='utf-8'))
        self.assertIsNotNone(r['offset'])
        self.assertIsNotNone(r['manual_yield'])
        self.assertIn('sample.1_',files[0].name)
        ET.parse(next(Path(self.tmp.name).rglob('*.svg')))
        self.assertIn('O',terminal_chart(app.d,app.c,r,60,12,None,False))
        with self.assertRaises(ValueError):app.command('x tensile 0')
        with self.assertRaises(ValueError):app.command('y custom nan MPa')

    def test_uploaded_legacy_xls(self):
        path=Path(__file__).parent/'example'/'1 w.xls'
        if not path.exists():self.skipTest('Example not present')
        d=read_data(path)
        self.assertEqual(len(d.rows),2303)
        self.assertEqual(tuple(d.rows[[0,-1]]),(2,2304))
        c=default_config(d)
        self.assertEqual((c.sx,c.sy),(-1,-1))
        self.assertEqual(analyze(d,c)['branch_rows'],[2,2266])
        self.assertIsNone(analyze(d,c)['offset'])


if __name__=='__main__':unittest.main(verbosity=2)
