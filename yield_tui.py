#!/usr/bin/env python3
"""Yield Lens: local, command-driven terminal dashboard; Python 3.10+."""
from __future__ import annotations
import argparse
import csv
import hashlib
import html
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
from datetime import datetime
from dataclasses import dataclass, asdict
import numpy as np

VERSION = '1.0.0'


@dataclass
class Config:
    sx: int = 1
    sy: int = 1
    xmode: str = 'unknown'
    xfactor: float = 1.0
    yunit: str = 'unknown'
    yfactor: float = 1.0
    zero_row: int | None = None
    start_row: int | None = None
    end_row: int | None = None
    fit_rows: tuple[int, int] | None = None
    yield_row: int | None = None
    tolerance: float = 0.02
    consecutive: int = 5


@dataclass
class Data:
    path: Path
    sheet: str
    columns: tuple[int, int]
    rows: np.ndarray
    raw: np.ndarray
    skipped: int


def read_data(path, sheet=0, columns=(1, 2)):
    path = Path(path)
    if min(columns) < 1 or columns[0] == columns[1]:
        raise ValueError('欄號必須為不同的正整數。')
    suffix = path.suffix.lower()
    if suffix == '.xls':
        import xlrd
        book = xlrd.open_workbook(str(path), logfile=io.StringIO())
        sh = book.sheet_by_index(sheet) if isinstance(sheet, int) else book.sheet_by_name(sheet)
        values = [sh.row_values(i) for i in range(sh.nrows)]
        sheet_name = sh.name
    elif suffix == '.xlsx':
        import openpyxl
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sh = book.worksheets[sheet] if isinstance(sheet, int) else book[sheet]
        values = list(sh.values)
        sheet_name = sh.title
        book.close()
    else:
        raw = path.read_bytes()
        for encoding in ('utf-8-sig', 'utf-16', 'cp950'):
            try:
                txt = raw.decode(encoding)
                break
            except UnicodeError:
                continue
        else:
            raise ValueError('無法辨識文字編碼；請另存 UTF-8 CSV。')
        try:
            dialect = csv.Sniffer().sniff(txt[:8192], delimiters=',\t;')
        except csv.Error:
            dialect = csv.excel_tab if suffix == '.tsv' else csv.excel
        values = list(csv.reader(io.StringIO(txt), dialect))
        sheet_name = 'CSV'
    points, rows, skipped = [], [], 0
    for row, values_row in enumerate(values, 1):
        try:
            x, y = (float(values_row[c - 1]) for c in columns)
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError()
        except (IndexError, TypeError, ValueError):
            skipped += 1
            continue
        points.append((x, y))
        rows.append(row)
    if len(points) < 5:
        raise ValueError('有效數值少於 5 筆；請確認工作表及 X/Y 欄。')
    return Data(path.resolve(), sheet_name, columns, np.array(rows), np.array(points), skipped)


def default_config(d):
    # Use endpoints, never absolute values: sign changes remain visible.
    return Config(sx=1 if d.raw[-1, 0] >= d.raw[0, 0] else -1,
                  sy=1 if d.raw[-1, 1] >= d.raw[0, 1] else -1)


def row_index(d, row):
    ix = np.flatnonzero(d.rows == row)
    if not len(ix):
        raise ValueError(f'第 {row} 列不是有效數值列。')
    return int(ix[0])


def transformed(d, c):
    a = d.raw.copy()
    if c.zero_row is not None:
        a -= d.raw[row_index(d, c.zero_row)]
    a[:, 0] *= c.sx * c.xfactor
    a[:, 1] *= c.sy * c.yfactor
    return a[:, 0], a[:, 1]


def analyze(d, c):
    x, y = transformed(d, c)
    begin = row_index(d, c.start_row) if c.start_row else 0
    # Default analysis ends at first maximum of the oriented ordinate.
    end = row_index(d, c.end_row) if c.end_row else begin + int(np.argmax(y[begin:]))
    if end <= begin:
        raise ValueError('分析終點必須晚於起點；請調整方向或 branch 起訖列。')
    result = dict(version=VERSION, source=str(d.path), sheet=d.sheet,
                  columns=d.columns, source_sha256=hashlib.sha256(d.path.read_bytes()).hexdigest(),
                  settings=asdict(c), valid_count=len(x), skipped_rows=d.skipped,
                  branch_rows=[int(d.rows[begin]), int(d.rows[end])],
                  fit=None, offset=None, candidate=None, manual_yield=None, warnings=[])
    if c.xmode == 'unknown':
        result['warnings'].append('X 單位未確認：不計算 0.2% offset。')
    if c.yunit == 'unknown':
        result['warnings'].append('Y 單位未確認：目前不能稱作荷重或應力。')
    if c.xmode == 'custom':
        result['warnings'].append('使用自訂位移→應變係數；請確認課程公式與量測方式。')
    if c.yield_row is not None:
        i = row_index(d, c.yield_row)
        result['manual_yield'] = dict(row=int(d.rows[i]), x=float(x[i]), y=float(y[i]),
                                      method='使用者手動指定，非程式驗證')
    if c.fit_rows is None:
        result['warnings'].append('尚未指定線性區間；用 fit 起始列 結束列。')
        return result
    lo, hi = [row_index(d, r) for r in c.fit_rows]
    if not begin <= lo < hi <= end or hi - lo < 4:
        raise ValueError('線性區間需含至少 5 筆，且位於分析起訖列內。')
    xf, yf = x[lo:hi+1], y[lo:hi+1]
    if np.ptp(xf) <= 0:
        raise ValueError('線性區間的 X 沒有變化。')
    if np.any(np.diff(xf) <= 0):
        result['warnings'].append('線性區間含重複或回退 X；請檢查卸載、雜訊或夾具滑動。')
    k, b = np.polyfit(xf, yf, 1)
    if k <= 0:
        raise ValueError('斜率非正值；請確認方向及線性區間。')
    residual = yf - (k * xf + b)
    ss = float(np.sum((yf - yf.mean()) ** 2))
    r2 = 1 - float(np.sum(residual ** 2)) / ss if ss else 0.0
    rmse = float(np.sqrt(np.mean(residual ** 2)))
    result['fit'] = dict(slope=float(k), intercept=float(b), r_squared=r2,
                         rmse=rmse, rows=[int(d.rows[lo]), int(d.rows[hi])],
                         x_zero=float(-b/k))
    if r2 < .995:
        result['warnings'].append('R² < 0.995：線性擬合品質偏低，請重新選區間。')
    # Stop at material reversal rather than sorting or splicing separate cycles.
    reversals = np.flatnonzero(np.diff(x[hi:end+1]) < 0)
    search_end = hi + int(reversals[0]) if len(reversals) else end
    if len(reversals):
        result['warnings'].append(f'X 於原始第 {d.rows[search_end+1]} 列回退；交點搜尋在此之前停止。')
    if c.xmode != 'unknown':
        diff = y - (k * (x - .002) + b)
        for i in range(hi, search_end):
            if x[i+1] > x[i] and diff[i] >= 0 and diff[i+1] <= 0 and diff[i] > diff[i+1]:
                t = float(diff[i] / (diff[i] - diff[i+1]))
                result['offset'] = dict(x=float(x[i] + t*(x[i+1]-x[i])),
                                        y=float(y[i] + t*(y[i+1]-y[i])),
                                        bracket_rows=[int(d.rows[i]), int(d.rows[i+1])],
                                        fraction=t, offset_strain=.002)
                break
        if result['offset'] is None:
            result['warnings'].append('分析範圍內沒有 offset 交點；不外插，也不取最接近的點冒充。')
    # A reproducible deviation cue, NOT a metallurgical yield-point definition.
    threshold = max(c.tolerance * float(np.ptp(yf)), 3 * rmse)
    departure = k*x + b - y
    for i in range(hi+1, search_end-c.consecutive+2):
        if np.all(departure[i:i+c.consecutive] > threshold):
            result['candidate'] = dict(row=int(d.rows[i]), x=float(x[i]), y=float(y[i]),
                                       threshold=threshold, consecutive=c.consecutive,
                                       method='連續偏離線性：僅供目視判讀，不等於真正降伏點')
            break
    return result


def fmt(v):
    return f'{v:.6g}'


def labels(c):
    return ('strain (mm/mm)' if c.xmode != 'unknown' else 'X (unit unknown)',
            'Y (unit unknown)' if c.yunit == 'unknown' else c.yunit)


def chart_series(d, c, r):
    x, y = transformed(d, c)
    series = [('data', x, y, '#38bdf8')]
    if r.get('fit'):
        f = r['fit']
        t = np.linspace(float(x.min()), float(x.max()), 400)
        series.append(('linear fit', t, f['slope']*t+f['intercept'], '#facc15'))
        if c.xmode != 'unknown':
            series.append(('0.2% offset', t, f['slope']*(t-.002)+f['intercept'], '#fb7185'))
    return series


def limits(d, c, view):
    x, y = transformed(d, c)
    left, right = view if view else (float(x.min()), float(x.max()))
    mask = (x >= left) & (x <= right)
    if not mask.any() or right <= left:
        raise ValueError('顯示範圍內沒有資料。')
    bottom, top = float(y[mask].min()), float(y[mask].max())
    margin = (top-bottom)*.08 or 1
    return left, right, bottom-margin, top+margin


def terminal_chart(d, c, r, width=85, height=16, view=None, color=True):
    left, right, bottom, top = limits(d, c, view)
    pw, ph = width*2, height*4
    pixels = np.zeros((ph, pw), dtype=np.uint8)
    def point(px, py, code):
        if 0 <= px < pw and 0 <= py < ph:
            pixels[py, px] = code
    def xy(x, y):
        return ((x-left)/(right-left)*(pw-1), (top-y)/(top-bottom)*(ph-1))
    series = chart_series(d, c, r)
    # Draw clipped samples; interpolate only short, visible segments.
    for code, (_, xs, ys, _) in enumerate(series, 1):
        for j in range(len(xs)-1):
            ax, ay = xy(xs[j], ys[j]); bx, by = xy(xs[j+1], ys[j+1])
            if max(ax,bx)<0 or min(ax,bx)>=pw or max(ay,by)<0 or min(ay,by)>=ph:
                continue
            n = min(2000, max(2, int(max(abs(bx-ax),abs(by-ay)))+1))
            for t in np.linspace(0,1,n):
                point(round(ax+t*(bx-ax)),round(ay+t*(by-ay)),code)
    markers = {}
    for name, char in [('offset','O'),('candidate','C'),('manual_yield','Y')]:
        if r.get(name):
            px, py = xy(r[name]['x'],r[name]['y'])
            if 0<=px<pw and 0<=py<ph:
                markers[(int(py)//4,int(px)//2)] = char
    bit = ((1,8),(2,16),(4,32),(64,128))
    colors = ['', '\033[36m', '\033[33m', '\033[35m']
    lines = []
    for cy in range(height):
        line = ''
        for cx in range(width):
            if (cy,cx) in markers:
                line += ('\033[97;1m' if color else '') + markers[(cy,cx)] + ('\033[0m' if color else '')
                continue
            code, dots = 0, 0
            for dy in range(4):
                for dx in range(2):
                    v = pixels[cy*4+dy,cx*2+dx]
                    if v:
                        dots |= bit[dy][dx]
                        code = max(code,int(v))
            line += (colors[code] if color else '') + chr(0x2800+dots) + ('\033[0m' if code and color else '')
        value = top-(top-bottom)*cy/max(1,height-1)
        lines.append(f'{value:10.4g} │'+line)
    lines.append(' '*12+f'{left:.6g}'+ ' '*max(1,width-len(fmt(left))-len(fmt(right)))+f'{right:.6g}')
    return '\n'.join(lines)


HELP = '''指令（輸入後 Enter；列號一律為原始 Excel 列號）
 files                    檔案清單；use 2 切換第 2 個檔案
 sheet 1                  切換工作表（1 起算）；cols 1 2 選 X/Y 欄
 sign -1 -1               X/Y 方向；不是 abs，不會抹掉正負變化
 x percent                原始 X 是百分比，除以 100 成應變
 x strain                 原始 X 是 mm/mm；x micro 是微應變
 x tensile 50             原始 X 是標距伸長 mm，L0=50 mm
 x custom 0.0005          應變 = 方向修正後 X × 自訂係數
 x unknown                回到未知單位；停用 0.2% offset
 y N / y kN / y kgf       確認荷重單位（保留該單位）
 y MPa                    原始 Y 已是應力；不再套用截面公式
 y custom 0.123 MPa       Y × 係數轉為 MPa；係數由課程公式決定
 zero 2 / zero off        扣除某列的 X/Y 基準值；原始數據保留
 branch 2 2266            指定單次加載的起訖列；branch auto 重設
 fit 350 450              用此區間做線性回歸；fit off 取消
 suggest                  建議 10–30% 峰值區間（僅提示，不自動套用）
 yield 520 / yield off    手動標記／取消 Yield point
 tol 0.02 5               偏離門檻比例與連續點數（候選點，非標準降伏）
 view 0.01 0.05 / view all 放大 X 範圍（轉換後單位）／全圖
 rows 400 410             原始列、原始 X/Y、轉換後 X/Y
 export                   匯出 JSON、CSV、SVG 與全檔總表
 help                     說明；q 離開
注意：不自動宣稱有上／下降伏點；C 只是偏離線性的候選點。
'''


def export_result(d, c, result, out):
    out = Path(out)
    out.mkdir(parents=True,exist_ok=True)
    slug = re.sub(r'[^\w.-]+','_',d.path.stem)+'_'+hashlib.sha256((str(d.path)+d.sheet).encode()).hexdigest()[:8]
    root = out/slug
    Path(str(root)+'.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    x,y = transformed(d,c)
    with Path(str(root)+'.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f); w.writerow(['source_row','raw_X','raw_Y',*labels(c),'fit_Y','offset_Y'])
        fit=result.get('fit')
        for row,raw,xx,yy in zip(d.rows,d.raw,x,y):
            w.writerow([int(row),*raw,xx,yy,fit['slope']*xx+fit['intercept'] if fit else '',
                        fit['slope']*(xx-.002)+fit['intercept'] if fit and c.xmode!='unknown' else ''])
    # Vector export of full-resolution data; clipping keeps extrapolated lines in plot.
    left,right,bottom,top=limits(d,c,None)
    def xy(a,b): return (95+(a-left)/(right-left)*1040, 530-(b-bottom)/(top-bottom)*450)
    svg=['<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="650" viewBox="0 0 1200 650">',
         '<rect width="1200" height="650" fill="#0f172a"/>',
         '<defs><clipPath id="plot"><rect x="95" y="80" width="1040" height="450"/></clipPath></defs>']
    def text(x,y,t,color='#e2e8f0',size=15):
        svg.append(f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" font-family="sans-serif">{html.escape(str(t))}</text>')
    text(30,30,f'{d.path.name} | {d.sheet}',size=22)
    text(30,56,'O: 0.2% offset   C: deviation candidate (not yield)   Y: manual yield')
    for t in np.linspace(0,1,6):
        xx=95+t*1040; yy=530-t*450
        svg.append(f'<path d="M {xx} 80 V 530 M 95 {yy} H 1135" stroke="#334155"/>')
        text(xx-18,552,fmt(left+t*(right-left)),size=12)
        text(4,yy+4,fmt(bottom+t*(top-bottom)),size=12)
    svg.append('<g clip-path="url(#plot)">')
    for name,xs,ys,color in chart_series(d,c,result):
        points=' '.join(f'{xx:.3f},{yy:.3f}' for xx,yy in (xy(a,b) for a,b in zip(xs,ys)))
        svg.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>')
    for name,char in [('offset','O'),('candidate','C'),('manual_yield','Y')]:
        if result.get(name):
            p=result[name]; xx,yy=xy(p['x'],p['y'])
            svg.append(f'<circle cx="{xx}" cy="{yy}" r="5" fill="#fff"/>')
            text(xx+8,yy-8,char)
    svg.append('</g>')
    text(450,585,labels(c)[0]);text(30,610,'Y: '+labels(c)[1])
    text(30,635,'Settings and warnings: see the accompanying JSON. Raw points are retained in CSV.')
    svg.append('</svg>');Path(str(root)+'.svg').write_text('\n'.join(svg),encoding='utf-8')
    return root


class Dashboard:
    def __init__(self, paths, output='results', color=True):
        self.paths=paths; self.index=0; self.cache={};self.output=output;self.color=color
        self.view=None;self.message='先確認 X/Y 單位，再選線性区間。help 可查看所有指令。'
        self.load(0)

    def load(self,index):
        if not 0<=index<len(self.paths): raise ValueError('檔案編號不存在。')
        if index not in self.cache:
            d=read_data(self.paths[index]);self.cache[index]=(d,default_config(d))
        self.index=index;self.d,self.c=self.cache[index];self.view=None

    def render(self):
        w,h=shutil.get_terminal_size((110,42))
        try:r=analyze(self.d,self.c)
        except ValueError as exc:r={'warnings':[str(exc)],'fit':None,'offset':None,'candidate':None,'manual_yield':None}
        print(f'YIELD LENS {VERSION}  │  {self.index+1}/{len(self.paths)}  {self.d.path.name}  │  {self.d.sheet}')
        print(f'{len(self.d.rows):,} 筆；略過 {self.d.skipped} 列標頭／非數值  │  X/Y 方向 {self.c.sx:+}/{self.c.sy:+}  │  zero={self.c.zero_row}')
        print(f'X: {self.c.xmode} × {self.c.xfactor:g} → {labels(self.c)[0]}  │  Y: × {self.c.yfactor:g} → {labels(self.c)[1]}')
        print(f'分析列: {r.get("branch_rows","未設定")}   線性列: {self.c.fit_rows}   青=data 黃=fit 紫=offset')
        print(terminal_chart(self.d,self.c,r,max(30,min(w-14,135)),max(6,min(h-19,22)),self.view,self.color))
        if r.get('fit'):
            f=r['fit'];print(f'fit: Y = {fmt(f["slope"])} X {f["intercept"]:+.6g}   R²={f["r_squared"]:.6f}   RMSE={fmt(f["rmse"])}')
        else: print('fit: 尚未設定有效線性區間')
        for key,title in [('offset','O 0.2% offset'),('candidate','C 偏離候選點'),('manual_yield','Y 手動降伏點')]:
            p=r.get(key)
            print(title+': '+(f'X={fmt(p["x"])}  Y={fmt(p["y"])}  原始列={p.get("row",p.get("bracket_rows"))}' if p else '未取得'))
        print('提醒: '+' | '.join(r.get('warnings',[])))
        print('fit 起列 末列 │ x percent/strain │ y N/MPa │ yield 列 │ view 左 右 │ export │ help │ q')
        print(self.message)

    def command(self,line):
        parts=line.strip().split()
        if not parts:return
        cmd,*args=parts; c=self.c;d=self.d
        if cmd=='q':raise EOFError
        if cmd=='help':self.message=HELP;return
        if cmd=='files':self.message='\n'.join(f'{i+1}: {p}' for i,p in enumerate(self.paths));return
        if cmd=='use':self.load(int(args[0])-1);return
        if cmd in ('sheet','cols'):
            sh=int(args[0])-1 if cmd=='sheet' else d.sheet
            cols=tuple(map(int,args)) if cmd=='cols' else d.columns
            if len(cols)!=2:raise ValueError('cols 需要兩個欄號。')
            nd=read_data(d.path,sh,cols);self.d=nd;self.c=default_config(nd)
            self.cache[self.index]=(self.d,self.c);self.view=None
        elif cmd=='sign':
            sx,sy=map(int,args)
            if sx not in (-1,1) or sy not in (-1,1):raise ValueError('方向只能為 1 或 -1。')
            c.sx,c.sy=sx,sy;self.view=None
        elif cmd=='x':
            mode=args[0]
            factors={'unknown':1,'strain':1,'percent':.01,'micro':1e-6}
            if mode in factors:factor=factors[mode]
            elif mode in ('tensile','custom'):
                v=float(args[1])
                if not math.isfinite(v) or v<=0:raise ValueError('長度或係數必須為正有限值。')
                factor=1/v if mode=='tensile' else v
            else:raise ValueError('x 模式：unknown strain percent micro tensile custom')
            c.xmode,c.xfactor=mode,factor;self.view=None
        elif cmd=='y':
            if args[0]=='custom':factor=float(args[1]);unit=args[2]
            else:factor=1.;unit=args[0]
            if unit not in ('unknown','N','kN','kgf','MPa'):raise ValueError('Y 單位：unknown N kN kgf MPa')
            if not math.isfinite(factor) or factor<=0:raise ValueError('係數必須為正有限值。')
            c.yunit,c.yfactor=unit,factor
        elif cmd=='zero':
            row=None if args[0]=='off' else int(args[0])
            if row is not None:row_index(d,row)
            c.zero_row=row;self.view=None
        elif cmd=='branch':
            if args==['auto']:c.start_row=c.end_row=None
            else:
                a,b=map(int,args);ia,ib=row_index(d,a),row_index(d,b)
                if ia>=ib:raise ValueError('起列必須小於末列。')
                c.start_row,c.end_row=a,b
        elif cmd=='fit':
            if args==['off']:c.fit_rows=None
            else:
                a,b=map(int,args);row_index(d,a);row_index(d,b)
                trial=Config(**asdict(c));trial.fit_rows=(a,b);analyze(d,trial)
                c.fit_rows=(a,b)
        elif cmd=='yield':
            row=None if args==['off'] else int(args[0])
            if row is not None:row_index(d,row)
            c.yield_row=row
        elif cmd=='tol':
            tol=float(args[0]);n=int(args[1])
            if not math.isfinite(tol) or not 0<tol<1 or not 1<=n<=len(d.rows):raise ValueError('比例需介於 0 與 1；點數需在資料筆數內。')
            c.tolerance,c.consecutive=tol,n
        elif cmd=='view':
            view=None if args==['all'] else tuple(map(float,args))
            if view is not None and (len(view)!=2 or not all(math.isfinite(v) for v in view)):raise ValueError('view 需要兩個有限值。')
            limits(d,c,view);self.view=view
        elif cmd=='suggest':
            x,y=transformed(d,c)
            start=row_index(d,c.start_row) if c.start_row else 0
            end=row_index(d,c.end_row) if c.end_row else start+int(np.argmax(y[start:]))
            seg=y[start:end+1];low=seg[0];peak=float(seg.max())
            a=np.flatnonzero(seg>=low+.1*(peak-low));b=np.flatnonzero(seg>=low+.3*(peak-low))
            if not len(a) or not len(b):raise ValueError('找不到建議區間。')
            self.message=f'僅供起點參考：fit {d.rows[start+a[0]]} {d.rows[start+b[0]]}；請確認此區為直線，再自行輸入。'
            return
        elif cmd=='rows':
            a,b=map(int,args)
            if b<a or b-a>50:raise ValueError('一次最多查看 51 列。')
            x,y=transformed(d,c);mask=(d.rows>=a)&(d.rows<=b)
            self.message='原始列        raw X          raw Y        X(轉換)       Y(轉換)\n'+'\n'.join(
                f'{d.rows[i]:6} {d.raw[i,0]:14.7g} {d.raw[i,1]:14.7g} {x[i]:14.7g} {y[i]:14.7g}' for i in np.flatnonzero(mask))
            return
        elif cmd=='export':
            output=Path(self.output)/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            output.mkdir(parents=True)
            with (output/'summary.csv').open('w',encoding='utf-8-sig',newline='') as f:
                w=csv.writer(f);w.writerow(['file','status','X_mode','Y_unit','offset_X','offset_Y','manual_yield_X','manual_yield_Y','warnings'])
                for idx,path in enumerate(self.paths):
                    if idx not in self.cache:
                        w.writerow([str(path),'尚未載入／未分析']);continue
                    dd,cc=self.cache[idx]
                    try:
                        rr=analyze(dd,cc);export_result(dd,cc,rr,output)
                        off=rr.get('offset') or {};man=rr.get('manual_yield') or {}
                        w.writerow([str(path),'待人工審核',cc.xmode,cc.yunit,off.get('x',''),off.get('y',''),man.get('x',''),man.get('y',''),' | '.join(rr['warnings'])])
                    except (ValueError,OSError) as exc:w.writerow([str(path),'錯誤: '+str(exc)])
            self.message=f'已匯出到 {output.resolve()}（未載入檔案會列為未分析）';return
        else:raise ValueError('不認識的指令；輸入 help 查看。')
        self.message='已更新；計算使用全部原始數值，沒有平滑或重排。'

    def run(self, commands=False):
        interactive=sys.stdin.isatty() and sys.stdout.isatty()
        if interactive and not commands:
            from keyboard_ui import KeyboardUI
            KeyboardUI(self).run()
            return
        while True:
            try:
                if interactive: print('\033[2J\033[H',end='')
                self.render()
                self.command(input('指令 > '))
            except (EOFError,KeyboardInterrupt):
                print('\n已離開。若需保留分析，離開前請先 export。');break
            except Exception as exc:
                self.message=f'錯誤：{exc}'


def main():
    p=argparse.ArgumentParser(description='荷重／應力與應變判讀 TUI；資料完全在本機處理。')
    p.add_argument('paths',nargs='*',help='檔案或資料夾，可多個')
    p.add_argument('--output',default='results');p.add_argument('--no-color',action='store_true')
    p.add_argument('--commands',action='store_true',help='使用傳統文字指令模式')
    args=p.parse_args();paths=[]
    for s in args.paths:
        path=Path(s)
        paths.extend(sorted(q for q in path.iterdir() if q.suffix.lower() in ('.xls','.xlsx','.csv','.tsv') and not q.name.startswith('~$')) if path.is_dir() else [path])
    if not paths:
        paths=sorted(Path('.').glob('*.xls'))+sorted(Path('.').glob('*.xlsx'))+sorted(Path('.').glob('*.csv'))
    if not paths:
        paths=sorted((Path(__file__).parent/'example').glob('*.xls'))
    if not paths:p.error('請指定 XLS/XLSX/CSV/TSV 檔案或資料夾。')
    if os.name=='nt':
        os.system('')  # Enable ANSI on Windows Terminal / modern conhost.
    try:Dashboard(list(dict.fromkeys(paths)),args.output,not args.no_color).run(args.commands)
    except (ValueError,OSError,ImportError) as exc:
        p.exit(1,f'無法啟動：{exc}\n請先執行 python -m pip install -r requirements.txt\n')


if __name__=='__main__':main()
