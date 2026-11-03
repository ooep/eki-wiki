#!/usr/bin/env python3
"""清除 kana 批量翻译副作用（"（助词）"标注污染）+ 翻译残留假名键名"""
import json, glob, re, sys, time, urllib.request

REPO = '/home/user/work/eki-wiki'
API_URL = 'https://index-translate.bilibili.com/v1/chat/completions'
MODEL = 'Index-Translate-35B-A3B'
LK = {'zh': '中文', 'en': '英文'}
PAT = re.compile(r'[（(][^）)]*助词[^）)]*[）)]')
KANA = re.compile(r'[ぁ-んァ-ヶ]')

def trans_keys(keys, lang):
    """批量翻译键名"""
    out = {}
    for i in range(0, len(keys), 10):
        batch = keys[i:i + 10]
        src = '日文' if lang == 'zh' else '日文（翻译为英文）'
        tgt = '中文' if lang == 'zh' else '英文'
        sys_p = f'你是{lang}翻译专家。把下面的{src}标题/名称逐条翻译为{tgt}。专名、机车型号、书名按惯例翻译，助词省略。'
        usr = '\n'.join(f'{j+1}. {k}' for j, k in enumerate(batch))
        body = {
            'model': MODEL,
            'messages': [
                {'role': 'system', 'content': sys_p},
                {'role': 'user', 'content': usr},
            ],
            'temperature': 0,
            'max_tokens': 256,
            'stream': False,
            'chat_template_kwargs': {'enable_thinking': False},
        }
        for attempt in range(6):
            try:
                req = urllib.request.Request(API_URL, json.dumps(body).encode(),
                                             {'Content-Type': 'application/json',
                                              'User-Agent': 'Index-Translate-Client/1.0'})
                with urllib.request.urlopen(req, timeout=60) as r:
                    txt = json.load(r)['choices'][0]['message']['content'].strip()
                pairs = re.findall(r'(\d+)\.\s*(.+)', txt)
                if pairs:
                    for num, t in pairs:
                        idx = int(num) - 1
                        if 0 <= idx < len(batch):
                            out[batch[idx]] = t.strip()
                break
            except Exception as e:
                if attempt == 5:
                    print(f'  键名批量翻译失败: {e}', file=sys.stderr)
                time.sleep(3 + attempt * 2)
    return out

def build_full(obj):
    """重拼全文"""
    lead = obj.get('首段') or ''
    secs = obj.get('章节')
    parts = [lead] if lead else []
    if isinstance(secs, dict):
        for k, v in secs.items():
            parts.append(f'{k}\n{v}')
    elif isinstance(secs, list):
        for s in secs:
            if isinstance(s, dict):
                parts.append(f"{s.get('标题','')}\n{s.get('内容','')}")
    full = '\n\n'.join(p for p in parts if p)
    obj['全文'] = full
    obj['字数'] = len(full)

def clean_obj(bk):
    """清除该百科对象中的助词标注污染；返回是否变更"""
    changed = False
    if not isinstance(bk, dict):
        return changed
    secs = bk.get('章节')
    if isinstance(secs, dict):
        for k in list(secs.keys()):
            v = secs.get(k, '')
            nv = PAT.sub('', str(v))
            if nv != v:
                secs[k] = nv
                changed = True
    elif isinstance(secs, list):
        for s in secs:
            if isinstance(s, dict):
                v = s.get('内容', '')
                nv = PAT.sub('', str(v))
                if nv != v:
                    s['内容'] = nv
                    changed = True
    if changed:
        build_full(bk)
    return changed

def main():
    files = sorted(glob.glob(f'{REPO}/data/*.json'))
    # ---- 1) 清除助词标注污染 ----
    polluted = 0
    for fp in files:
        d = json.load(open(fp))
        chg = False
        for st in d.get('车站') or []:
            ek = st.get('百科') or {}
            if isinstance(ek, dict):
                bk = ek.get(LK['zh'])
                if clean_obj(bk):
                    chg = True
        lb = (d.get('线路') or {}).get('百科') or {}
        if isinstance(lb, dict):
            bk = lb.get(LK['zh'])
            if clean_obj(bk):
                chg = True
        if chg:
            json.dump(d, open(fp, 'w'), ensure_ascii=False, indent=2)
            polluted += 1
    print(f'助词标注污染已清理: {polluted} 文件')

    # ---- 2) 收集残留假名键名 ----
    bad_keys = set()
    files_with_bad = []
    for fp in files:
        d = json.load(open(fp))
        found = False
        for st in d.get('车站') or []:
            ek = st.get('百科') or {}
            if isinstance(ek, dict):
                bk = ek.get(LK['zh'])
                if isinstance(bk, dict):
                    secs = bk.get('章节')
                    if isinstance(secs, dict):
                        for k in secs:
                            if KANA.search(k):
                                bad_keys.add(k); found = True
                    elif isinstance(secs, list):
                        for s in secs:
                            if isinstance(s, dict) and KANA.search(str(s.get('标题', ''))):
                                bad_keys.add(str(s.get('标题', ''))); found = True
        lb = (d.get('线路') or {}).get('百科') or {}
        if isinstance(lb, dict):
            bk = lb.get(LK['zh'])
            if isinstance(bk, dict):
                secs = bk.get('章节')
                if isinstance(secs, dict):
                    for k in secs:
                        if KANA.search(k):
                            bad_keys.add(k); found = True
        if found:
            files_with_bad.append(fp)
    print(f'残留假名键名 {len(bad_keys)} 个，涉及 {len(files_with_bad)} 文件')
    if not bad_keys:
        return

    mapping = trans_keys(sorted(bad_keys), 'zh')
    print(f'翻译成功 {len(mapping)}/{len(bad_keys)}')
    if not mapping:
        return

    for fp in files_with_bad:
        d = json.load(open(fp))
        chg = False
        def fix(bk):
            nonlocal chg
            if not isinstance(bk, dict):
                return
            secs = bk.get('章节')
            if isinstance(secs, dict):
                new_secs = {}
                for k, v in secs.items():
                    nk = mapping.get(k, k)
                    if nk != k:
                        chg = True
                    new_secs[nk] = v
                bk['章节'] = new_secs
                if chg:
                    build_full(bk)
            elif isinstance(secs, list):
                for s in secs:
                    if isinstance(s, dict):
                        k = str(s.get('标题', ''))
                        nk = mapping.get(k, k)
                        if nk != k:
                            s['标题'] = nk
                            chg = True
                if chg:
                    build_full(bk)
        for st in d.get('车站') or []:
            ek = st.get('百科') or {}
            if isinstance(ek, dict):
                fix(ek.get(LK['zh']))
        lb = (d.get('线路') or {}).get('百科') or {}
        if isinstance(lb, dict):
            fix(lb.get(LK['zh']))
        if chg:
            json.dump(d, open(fp, 'w'), ensure_ascii=False, indent=2)
    print('键名替换完成')

if __name__ == '__main__':
    main()
