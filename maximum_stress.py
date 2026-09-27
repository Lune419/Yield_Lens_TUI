"""Find every maximum stress sample, preserving raw signs and source rows."""
import argparse
import hashlib
from pathlib import Path

import numpy as np

from result_mapping import save_mapping
from yield_tui import read_data, row_index


def find_maximum(path, sheet=0, columns=(1,2), direction=1, unit='unknown', rows=None):
    if type(direction) is not int or direction not in (-1,1):raise ValueError('方向必須為 +1 或 -1。')
    if unit not in ('unknown','MPa','Pa','kPa','GPa'):raise ValueError('不支援的應力單位。')
    data=read_data(path,sheet,columns)
    start,end=0,len(data.rows)-1
    if rows is not None:
        start,end=(row_index(data,r) for r in rows)
        if start>end:raise ValueError('範圍起列不能晚於末列。')
    oriented=data.raw[:,1]*direction
    value=float(np.max(oriented[start:end+1]))
    indices=np.flatnonzero(oriented[start:end+1]==value)+start
    return dict(method='指定範圍內方向換算後應力的最大實測值（不取絕對值、不內插）',
                stress_file=str(data.path),stress_sha256=hashlib.sha256(data.path.read_bytes()).hexdigest(),
                stress_sheet=data.sheet,stress_columns=list(data.columns),stress_unit=unit,
                stress_direction=direction,valid_count=len(data.rows),
                search_rows=[int(data.rows[start]),int(data.rows[end])],
                maximum_stress=value,match_count=len(indices),
                matches=[dict(source_row=int(data.rows[i]),data_number=int(i+1),
                              raw_x=float(data.raw[i,0]),raw_stress=float(data.raw[i,1]),
                              oriented_stress=float(oriented[i])) for i in indices])


def describe_maximum(report):
    unit=report['stress_unit'] if report['stress_unit']!='unknown' else '（單位未確認）'
    lines=[f'最大應力：{report["maximum_stress"]:.12g} {unit}',
           f'方向：{report["stress_direction"]:+d}；搜尋原始列：{report["search_rows"]}',
           f'共有 {report["match_count"]} 筆同值最大點；有效資料總數：{report["valid_count"]}',
           '原始列／第幾筆有效資料／原始 X／原始應力／方向換算值']
    for point in report['matches']:
        lines.append(f'{point["source_row"]} / {point["data_number"]} / {point["raw_x"]:.12g} / '
                     f'{point["raw_stress"]:.12g} / {point["oriented_stress"]:.12g}')
    return '\n'.join(lines+[report['method']])


def save_maximum(report,output='results'):
    return save_mapping(report,output,prefix='maximum_stress',filename='maximum')


def main():
    parser=argparse.ArgumentParser(description='搜尋 stress 檔的最大應力與所有對應資料位置。')
    parser.add_argument('stress')
    parser.add_argument('--sheet',type=int,default=1)
    parser.add_argument('--cols',type=int,nargs=2,default=(1,2))
    parser.add_argument('--direction',type=int,choices=(-1,1),default=1)
    parser.add_argument('--unit',choices=('unknown','MPa','Pa','kPa','GPa'),default='unknown')
    parser.add_argument('--rows',type=int,nargs=2,help='原始起列、末列；省略即搜尋整張工作表')
    parser.add_argument('--output',default='results')
    args=parser.parse_args()
    if args.sheet<1:parser.error('工作表編號需從 1 開始。')
    try:
        report=find_maximum(args.stress,args.sheet-1,tuple(args.cols),args.direction,args.unit,args.rows)
        print(describe_maximum(report))
        print('已儲存：',save_maximum(report,args.output))
    except (ValueError,OSError,IndexError,ImportError) as exc:parser.exit(1,f'無法搜尋：{exc}\n')


if __name__=='__main__':main()
