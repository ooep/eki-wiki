#!/usr/bin/env python3
"""人工定稿残留假名键名（模型翻译不净的6个唯一键）"""
import json, glob

MAP = {
    'ジュンク堂书店名古屋荣店': '淳久堂书店名古屋荣店',
    'CLIO（クリオ）东新宿一号馆': 'CLIO东新宿一号馆',
    '上松ねざめ・立町・仓本行': '上松寝觉·立町·仓本方向',
    '西松屋横滨三ツ境店': '西松屋横滨三境店',
    'Frespo（フレスポ）若叶台': 'Frespo若叶台',
}
DEL = ['路みすません（保留原名）']


def build_full(bk):
    lead = bk.get('首段') or ''
    secs = bk.get('章节')
    parts = [lead] if lead else []
    if isinstance(secs, dict):
        for k, v in secs.items():
            parts.append(f'{k}\n{v}')
    elif isinstance(secs, list):
        for s in secs:
            if isinstance(s, dict):
                parts.append(f"{s.get('标题', '')}\n{s.get('内容', '')}")
    full = '\n\n'.join(p for p in parts if p)
    bk['全文'] = full
    bk['字数'] = len(full)


def fix(bk, state):
    if not isinstance(bk, dict):
        return
    secs = bk.get('章节')
    if isinstance(secs, dict):
        new = {}
        for k, v in secs.items():
            if k in DEL:
                state['chg'] = True
                continue
            nk = MAP.get(k, k)
            if nk != k:
                state['chg'] = True
            new[nk] = v
        bk['章节'] = new
    elif isinstance(secs, list):
        ns = []
        for s in secs:
            if isinstance(s, dict):
                k = str(s.get('标题', ''))
                if k in DEL:
                    state['chg'] = True
                    continue
                nk = MAP.get(k, k)
                if nk != k:
                    s['标题'] = nk
                    state['chg'] = True
            ns.append(s)
        bk['章节'] = ns
    if state['chg']:
        build_full(bk)


def main():
    n = 0
    for fp in sorted(glob.glob('data/*.json')):
        d = json.load(open(fp))
        state = {'chg': False}
        for st in d.get('车站') or []:
            ek = st.get('百科') or {}
            if isinstance(ek, dict):
                fix(ek.get('中文'), state)
        lb = (d.get('线路') or {}).get('百科') or {}
        if isinstance(lb, dict):
            fix(lb.get('中文'), state)
        if state['chg']:
            json.dump(d, open(fp, 'w'), ensure_ascii=False, indent=2)
            n += 1
    print('人工定稿键名替换:', n, '文件')


if __name__ == '__main__':
    main()
