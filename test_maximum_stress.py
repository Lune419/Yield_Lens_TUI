import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from keyboard_ui import KeyboardUI
from maximum_stress import find_maximum, save_maximum
from yield_tui import Dashboard


class MaximumTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        self.path=self.folder/'stress.csv'
        self.path.write_text('X,Y\n0,-1\n1,-10\n\n2,-5\n3,-10\n4,-2\n',encoding='utf-8')

    def test_negative_direction_and_all_ties_with_original_positions(self):
        report=find_maximum(self.path,direction=-1,unit='MPa')
        self.assertEqual(report['maximum_stress'],10)
        self.assertEqual(report['match_count'],2)
        self.assertEqual([p['source_row'] for p in report['matches']],[3,6])
        self.assertEqual([p['data_number'] for p in report['matches']],[2,4])
        self.assertEqual([p['raw_stress'] for p in report['matches']],[-10,-10])

    def test_signed_max_is_not_absolute_max(self):
        report=find_maximum(self.path,direction=1)
        self.assertEqual(report['maximum_stress'],-1)
        self.assertEqual(report['matches'][0]['source_row'],2)

    def test_range_restricts_search_and_rejects_missing_rows(self):
        report=find_maximum(self.path,direction=-1,rows=(5,7))
        self.assertEqual(report['match_count'],1)
        self.assertEqual(report['matches'][0]['source_row'],6)
        with self.assertRaises(ValueError):find_maximum(self.path,rows=(4,7))
        with self.assertRaises(ValueError):find_maximum(self.path,rows=(7,2))

    def test_tui_flow_saves_report(self):
        app=Dashboard([self.path],output=self.folder/'out',preferences_path=None)
        ui=KeyboardUI(app)
        with patch.object(ui,'choose_path',return_value=self.path), \
             patch.object(ui,'number',side_effect=[1,2]), \
             patch.object(ui,'menu',side_effect=[0,1,1,0,0]), \
             patch.object(ui,'notice'):
            ui.maximum_stress()
        files=list((self.folder/'out').rglob('maximum.json'))
        self.assertEqual(len(files),1)
        report=json.loads(files[0].read_text(encoding='utf-8'))
        self.assertEqual(report['maximum_stress'],10)
        self.assertTrue(files[0].with_suffix('.csv').exists())


if __name__=='__main__':unittest.main()
