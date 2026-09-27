import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from keyboard_ui import KeyboardUI
from result_mapping import load_result, map_stress, save_mapping
from yield_tui import Config, Dashboard, analyze, read_data, export_result


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.w = self.folder/'load.csv'
        self.stress = self.folder/'stress.csv'
        x = np.linspace(0, .03, 301)
        y = np.where(x <= .005, 200000*x, 1000+1000*(x-.005))
        self.raw = np.c_[-x, -y]
        np.savetxt(self.w, self.raw, delimiter=',', header='X,Y', comments='')
        # Different header counts: pair valid samples, not spreadsheet row numbers.
        np.savetxt(self.stress, np.c_[-x, -y*.025], delimiter=',', header='stress test\nX,Y', comments='')
        d = read_data(self.w)
        c = Config(sx=-1, sy=-1, xmode='strain', yunit='N', fit_rows=(4,40))
        self.result = analyze(d,c)
        root = export_result(d,c,self.result,self.folder/'export')
        self.result_file = Path(str(root)+'.json')

    def test_existing_offset_maps_raw_and_oriented_values(self):
        report = map_stress(self.result_file, self.w, self.stress, unit='MPa')
        self.assertAlmostEqual(report['stress_raw'], -self.result['offset']['y']*.025, 10)
        self.assertAlmostEqual(report['stress_oriented'], self.result['offset']['y']*.025, 10)
        self.assertEqual(report['w_rows'], self.result['offset']['bracket_rows'])
        self.assertEqual(report['stress_rows'], [r+1 for r in report['w_rows']])
        self.assertEqual(report['data_numbers'], [r-1 for r in report['w_rows']])
        self.assertEqual(report['fraction'], self.result['offset']['fraction'])

    def test_mismatched_w_is_rejected(self):
        other = self.folder/'other.csv'
        other.write_bytes(self.w.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError, '來源指紋不同'):
            map_stress(self.result_file, other, self.stress)

    def test_count_and_x_mismatches_are_rejected(self):
        np.savetxt(self.stress, self.raw[:-1], delimiter=',')
        with self.assertRaisesRegex(ValueError, '資料筆數不同'):
            map_stress(self.result_file, self.w, self.stress)
        reversed_data = self.raw[::-1]
        np.savetxt(self.stress, reversed_data, delimiter=',')
        with self.assertRaisesRegex(ValueError, '順序／數值不一致'):
            map_stress(self.result_file, self.w, self.stress)

    def test_invalid_and_missing_offset_are_rejected(self):
        for offset in (None, dict(self.result['offset'], fraction=1.5),
                       dict(self.result['offset'], bracket_rows=[10, 12])):
            modified = dict(self.result, offset=offset)
            self.result_file.write_text(json.dumps(modified), encoding='utf-8')
            with self.assertRaises(ValueError):
                map_stress(self.result_file, self.w, self.stress)

    def test_no_refitting_and_no_w_factor_applied_to_stress(self):
        d = read_data(self.w)
        c = Config(sx=-1, sy=-1, xmode='strain', yunit='N', yfactor=100, fit_rows=(4,40))
        self.result_file.write_text(json.dumps(analyze(d,c)), encoding='utf-8')
        with patch('yield_tui.analyze', side_effect=AssertionError('must not refit')):
            report = map_stress(self.result_file,self.w,self.stress,direction=1)
        self.assertAlmostEqual(report['stress_raw'], -self.result['offset']['y']*.025, 10)
        self.assertEqual(report['stress_raw'],report['stress_oriented'])

    def test_tampered_result_coordinates_are_rejected(self):
        self.result['offset']['y'] += 10
        self.result_file.write_text(json.dumps(self.result), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '交點與 W'):
            map_stress(self.result_file,self.w,self.stress)

    def test_tui_workflow_and_export_do_not_change_original(self):
        original = self.result_file.read_bytes()
        app = Dashboard([self.w], output=self.folder/'output', preferences_path=None)
        ui = KeyboardUI(app)
        with patch.object(ui, 'choose_path', side_effect=[self.result_file.parent, self.w, self.stress]), \
             patch.object(ui, 'menu', side_effect=[0,0,1,0,0]), \
             patch.object(ui, 'number', side_effect=[1,2]), \
             patch.object(ui, 'notice') as notice:
            ui.stress_mapping()
        saved = list((self.folder/'output').rglob('mapping.json'))
        self.assertEqual(len(saved),1)
        report=json.loads(saved[0].read_text(encoding='utf-8'))
        self.assertEqual(report['stress_unit'],'MPa')
        self.assertTrue(saved[0].with_suffix('.csv').exists())
        self.assertEqual(original,self.result_file.read_bytes())
        self.assertIn('方向換算後 stress',notice.call_args_list[0].args[1])


if __name__ == '__main__':unittest.main()
