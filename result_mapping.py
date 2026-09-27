"""Map an existing offset result to a paired stress measurement, without refitting."""
import argparse
import csv
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from yield_tui import Config, read_data, row_index, transformed


def load_result(path):
    path = Path(path)
    try:
        result = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(result, dict): raise ValueError()
        offset = result.get('offset')
        if not isinstance(offset, dict):
            raise ValueError('這份結果沒有 0.2% offset 交點，無法對照。')
        rows, fraction = offset['bracket_rows'], offset['fraction']
        if not isinstance(rows, list) or len(rows) != 2 or any(type(r) is not int or r < 1 for r in rows):
            raise ValueError('交點列號格式不正確。')
        if type(fraction) not in (int, float) or not math.isfinite(fraction) or not 0 <= fraction <= 1:
            raise ValueError('交點內插比例必須介於 0 與 1。')
        if not isinstance(result['source_sha256'], str) or len(result['source_sha256']) != 64:
            raise ValueError('結果缺少有效的 W 來源指紋。')
        columns = result['columns']
        if not isinstance(columns, list) or len(columns) != 2 or any(type(c) is not int or c < 1 for c in columns):
            raise ValueError('W 欄位設定不正確。')
        if type(result['valid_count']) is not int: raise ValueError('結果資料筆數不正確。')
        if not isinstance(result['sheet'], str): raise ValueError('W 工作表設定不正確。')
        config = Config(**result['settings'])
        if config.sx not in (-1, 1) or config.sy not in (-1, 1): raise ValueError('方向設定不正確。')
        for value in (config.xfactor, config.yfactor, offset['x'], offset['y']):
            if type(value) not in (int, float) or not math.isfinite(value): raise ValueError('結果包含無效數值。')
        if config.xfactor <= 0 or config.yfactor <= 0: raise ValueError('換算係數必須大於 0。')
    except (KeyError, TypeError) as exc:
        raise ValueError('不是完整的 Yield Lens 結果 JSON。') from exc
    return result


def verify_source(result, w_path):
    w_path = Path(w_path)
    digest = hashlib.sha256(w_path.read_bytes()).hexdigest()
    if digest != result['source_sha256']:
        raise ValueError('W 原始檔與 result 的來源指紋不同；請選擇產生這份 result 的 W 檔，不能跨試驗套用列號。')
    w = read_data(w_path, result['sheet'], tuple(result['columns']))
    if len(w.rows) != result['valid_count']:
        raise ValueError('W 資料筆數與 result 不一致。')
    a, b = (row_index(w, row) for row in result['offset']['bracket_rows'])
    if b != a + 1: raise ValueError('交點兩側不是相鄰的有效資料。')
    x, y = transformed(w, Config(**result['settings']))
    t = result['offset']['fraction']
    calculated = [x[a] + t*(x[b]-x[a]), y[a] + t*(y[b]-y[a])]
    if not np.allclose(calculated, [result['offset']['x'], result['offset']['y']], rtol=1e-9, atol=1e-10):
        raise ValueError('JSON 交點與 W 原始資料／換算設定不一致。')
    return w, a, b


def map_stress(result_path, w_path, stress_path, sheet=0, columns=(1, 2),
               direction=None, unit='unknown'):
    result = load_result(result_path)
    w, a, b = verify_source(result, w_path)
    stress = read_data(stress_path, sheet, columns)
    if len(stress.rows) != len(w.rows):
        raise ValueError(f'W 有 {len(w.rows)} 筆、stress 有 {len(stress.rows)} 筆；資料筆數不同，無法逐筆對照。')
    if not np.allclose(w.raw[:, 0], stress.raw[:, 0], rtol=1e-9, atol=1e-12):
        raise ValueError('W 與 stress 的原始 X 順序／數值不一致；請確認配套檔案與 X 欄。')
    direction = result['settings']['sy'] if direction is None else direction
    if type(direction) is not int or direction not in (-1, 1): raise ValueError('stress 方向必須為 +1 或 -1。')
    if unit not in ('unknown', 'MPa', 'Pa', 'kPa', 'GPa'): raise ValueError('不支援的 stress 單位。')
    t = result['offset']['fraction']
    values = [float(stress.raw[i, 1]) for i in (a, b)]
    raw_value = values[0] + t*(values[1]-values[0])
    return dict(
        method='既有 offset 的相鄰點線性內插（非單筆實測值）',
        result_file=str(Path(result_path).resolve()),
        w_file=str(w.path), w_sha256=result['source_sha256'],
        stress_file=str(stress.path), stress_sha256=hashlib.sha256(stress.path.read_bytes()).hexdigest(),
        stress_sheet=stress.sheet, stress_columns=list(stress.columns),
        valid_count=len(w.rows), verification='W 指紋、有效筆數與逐筆原始 X 核對通過',
        offset_x=result['offset']['x'], offset_y=result['offset']['y'],
        offset_y_unit=result['settings']['yunit'],
        w_rows=[int(w.rows[i]) for i in (a, b)],
        data_numbers=[a+1, b+1], stress_rows=[int(stress.rows[i]) for i in (a, b)],
        fraction=t, stress_bracket_raw=values,
        stress_raw=raw_value, stress_direction=direction,
        stress_direction_source='result 的 Y 方向' if direction == result['settings']['sy'] else '使用者指定',
        stress_oriented=raw_value*direction, stress_unit=unit,
        conversion='僅套用方向；不套用 W 的荷重換算係數或歸零值',
    )


def describe_mapping(report):
    unit = report['stress_unit'] if report['stress_unit'] != 'unknown' else '（單位未確認）'
    return '\n'.join([
        report['verification'],
        f'既有 offset：{report["offset_y"]:.12g} {report["offset_y_unit"]}',
        f'W 原始列：{report["w_rows"]}；有效資料編號：{report["data_numbers"]}',
        f'stress 原始列：{report["stress_rows"]}；工作表：{report["stress_sheet"]}',
        f'兩側 stress 原始值：{report["stress_bracket_raw"]}',
        f'內插比例：{report["fraction"]:.12g}',
        f'stress 原始內插值：{report["stress_raw"]:.12g} {unit}',
        f'方向：{report["stress_direction"]:+d}（{report["stress_direction_source"]}）',
        f'方向換算後 stress：{report["stress_oriented"]:.12g} {unit}',
        report['method'], report['conversion'],
    ])


def save_mapping(report, output='results'):
    directory = Path(output)/('stress_mapping_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    directory.mkdir(parents=True, exist_ok=False)
    (directory/'mapping.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    with (directory/'mapping.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(report))
        writer.writeheader()
        writer.writerow({key:json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value for key,value in report.items()})
    return directory.resolve()


def main():
    parser = argparse.ArgumentParser(description='將既有 Yield Lens offset 對照至配套 stress 檔，不重新擬合。')
    parser.add_argument('result', help='既有結果 JSON 路徑')
    parser.add_argument('w', help='產生 result 的 W 原始檔')
    parser.add_argument('stress', help='配套 stress 檔')
    parser.add_argument('--sheet', type=int, default=1, help='stress 工作表編號，從 1 起')
    parser.add_argument('--cols', type=int, nargs=2, default=(1, 2), help='stress X/Y 欄，從 1 起')
    parser.add_argument('--direction', type=int, choices=(-1, 1), help='stress 方向，預設沿用 result Y 方向')
    parser.add_argument('--unit', choices=('unknown','MPa','Pa','kPa','GPa'), default='unknown')
    parser.add_argument('--output', default='results')
    args = parser.parse_args()
    if args.sheet < 1: parser.error('工作表編號必須至少為 1。')
    try:
        report = map_stress(args.result, args.w, args.stress, args.sheet-1, tuple(args.cols), args.direction, args.unit)
        print(describe_mapping(report))
        print('已儲存：', save_mapping(report, args.output))
    except (ValueError, OSError, IndexError, ImportError) as exc:
        parser.exit(1, f'無法對照：{exc}\n')


if __name__ == '__main__':
    main()
