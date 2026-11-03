#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eki-wiki 三语百科补译管道 v3（Index-Translate-35B-A3B）
- 文件级并行（workers 控制总并发，文件内部任务串行）
- 名称批量翻译（每批 25 个，JSON 数组回填）
- 章节三种修复：键名含日文 / 内容含日文（只翻内容）/ 目标缺失整节翻译
- 每完成 1 个文件写 checkpoint；每 push_every 个文件 git 分批推送
- 405/429/5xx 退避重试
"""
import json, os, re, sys, time, glob, random, threading, urllib.request, urllib.error, urllib.parse, collections, subprocess

API_BASE = "https://index-translate.bilibili.com/v1"
MODEL = "Index-Translate-35B-A3B"
DATA_DIR = "/home/user/work/eki-wiki/data"
STATE_FILE = "/home/user/work/eki-wiki/.translate_state.json"
LOG = "/home/user/work/eki-wiki/.translate_progress.log"
REPO = "/home/user/work/eki-wiki"

KANA = re.compile(r'[ぁ-んァ-ヶ]')
KANA_SEG = re.compile(r'[ぁ-んァ-ヶー]+')  # 假名连续段（含长音）
LK = {'zh': '中文', 'en': '英文'}  # 数据文件中的语言键
def has_jp(s):
    return bool(s) and bool(KANA.search(s))

# 日文特有汉字词（中文不会出现的写法）——用于检测汉字型日文键名残留
JP_ONLY_WORDS = [
    '歴史','駅構造','駅舎','沿革','営業','運賃','時刻表','接続','利用状況','廃止','開業',
    '貨物','旅客','信号','配線','番線','ホーム','改札','券売','乗車','下車','停車','通過',
    '方面','上り','下り','複線','単線','電化','非電化','開通','廃線','工事','竣工','着手',
    '延伸','移設','移転','開設','閉鎖','統合','分割','併合','駅前','駅名','駅員','乗降',
    '輸送','優等','各停','準急','寝台','食堂車','荷物','手荷物','郵便','電報','駅間','單線',
]
def is_jp_key(s):
    return bool(s) and (KANA.search(s) or any(w in str(s) for w in JP_ONLY_WORDS))

# 高频章节键名的静态映射（避免重复翻译误判与键名翻译调用）
STATIC_KEYS = {
    '歴史': {'zh': '历史', 'en': 'History'},
    '駅構造': {'zh': '车站构造', 'en': 'Station layout'},
    '利用状況': {'zh': '使用情况', 'en': 'Passenger statistics'},
    '駅周辺': {'zh': '车站周边', 'en': 'Around the station'},
    '隣の駅': {'zh': '相邻车站', 'en': 'Adjacent stations'},
    'バス路線': {'zh': '巴士线路', 'en': 'Bus routes'},
    '年表': {'zh': '年表', 'en': 'Timeline'},
    '普通': {'zh': '普通列车', 'en': 'Local trains'},
    '概要': {'zh': '概要', 'en': 'Overview'},
    'その他': {'zh': '其他', 'en': 'Others'},
    '日本の鉄道駅一覧': {'zh': '日本铁路车站列表', 'en': 'List of railway stations in Japan'},
    '駅名の由来': {'zh': '站名由来', 'en': 'Origin of the name'},
    '乗り入れ路線': {'zh': '接入线路', 'en': 'Lines served'},
    '日本国有鉄道の鉄道駅': {'zh': '日本国有铁道车站', 'en': 'Stations of Japanese National Railways'},
    '発車メロディ': {'zh': '发车提示音', 'en': 'Departure melodies'},
    '駅弁': {'zh': '车站便当', 'en': 'Station bento'},
    '各駅停車': {'zh': '各站停车', 'en': 'Local trains'},
    '年度別1日平均乗降人員': {'zh': '按年度日均上下车人数', 'en': 'Average daily boarding and alighting by year'},
    '付記事項': {'zh': '附记事项', 'en': 'Notes'},
    '構造': {'zh': '构造', 'en': 'Structure'},
    '快速': {'zh': '快速列车', 'en': 'Rapid trains'},
    '通過': {'zh': '通过', 'en': 'Passing'},
    'JR東日本': {'zh': 'JR东日本', 'en': 'JR East'},
    '東口': {'zh': '东口', 'en': 'East exit'},
    '■普通': {'zh': '■普通列车', 'en': 'Local trains'},
    '路線データ': {'zh': '线路数据', 'en': 'Line data'},
    '西口': {'zh': '西口', 'en': 'West exit'},
    '急行': {'zh': '急行', 'en': 'Express trains'},
    '備考': {'zh': '备注', 'en': 'Notes'},
    '北口': {'zh': '北口', 'en': 'North exit'},
    '南口': {'zh': '南口', 'en': 'South exit'},
    '路線バス': {'zh': '线路巴士', 'en': 'Route buses'},
    '周辺': {'zh': '周边', 'en': 'Surroundings'},
    '駅一覧': {'zh': '车站列表', 'en': 'List of stations'},
    '運行形態': {'zh': '运行形态', 'en': 'Service patterns'},
    '本線': {'zh': '本线', 'en': 'Main line'},
    '埼玉県の鉄道駅': {'zh': '埼玉县的铁路车站', 'en': 'Railway stations in Saitama Prefecture'},
    '停留場構造': {'zh': '停留场构造', 'en': 'Stop layout'},
    '貨物取扱': {'zh': '货物处理', 'en': 'Freight handling'},
    '東京メトロ': {'zh': '东京地铁', 'en': 'Tokyo Metro'},
    '2番のりば': {'zh': '2号站台', 'en': 'Platform 2'},
    '快速・普通': {'zh': '快速・普通列车', 'en': 'Rapid and local trains'},
    '千葉県の鉄道駅': {'zh': '千叶县的铁路车站', 'en': 'Railway stations in Chiba Prefecture'},
    '長野県の鉄道駅': {'zh': '长野县的铁路车站', 'en': 'Railway stations in Nagano Prefecture'},
    '1番のりば': {'zh': '1号站台', 'en': 'Platform 1'},
    '付記': {'zh': '附记', 'en': 'Notes'},
    '愛知県の鉄道駅': {'zh': '爱知县的铁路车站', 'en': 'Railway stations in Aichi Prefecture'},
    '当駅乗降人員': {'zh': '本站上下车人数', 'en': 'Passenger numbers at this station'},
    '高速バス': {'zh': '高速巴士', 'en': 'Highway buses'},
    '年度別1日平均乗車人員': {'zh': '按年度日均乘车人数', 'en': 'Average daily boarding by year'},
    'かつて存在した路線': {'zh': '曾经存在的线路', 'en': 'Former lines'},
    'JR西日本': {'zh': 'JR西日本', 'en': 'JR West'},
    '商業施設': {'zh': '商业设施', 'en': 'Commercial facilities'},
    '大阪府の鉄道駅': {'zh': '大阪府的铁路车站', 'en': 'Railway stations in Osaka Prefecture'},
    '九州旅客鉄道の鉄道駅': {'zh': '九州旅客铁道车站', 'en': 'Stations of Kyushu Railway Company'},
    '東京都交通局': {'zh': '东京都交通局', 'en': 'Toei Transportation'},
    '山手線': {'zh': '山手线', 'en': 'Yamanote Line'},
    '特急': {'zh': '特急', 'en': 'Limited express'},
    '福岡県の鉄道駅': {'zh': '福冈县的铁路车站', 'en': 'Railway stations in Fukuoka Prefecture'},
    '大阪市の鉄道駅': {'zh': '大阪市的铁路车站', 'en': 'Railway stations in Osaka'},
    '山陽本線': {'zh': '山阳本线', 'en': 'Sanyo Main Line'},
    '停車列車': {'zh': '停靠列车', 'en': 'Stopping trains'},
    '配線図': {'zh': '配线图', 'en': 'Track diagram'},
    '在来線': {'zh': '既有线', 'en': 'Conventional lines'},
    '快速・各駅停車': {'zh': '快速・各站停车', 'en': 'Rapid and local trains'},
    '3番のりば': {'zh': '3号站台', 'en': 'Platform 3'},
    '都営バス': {'zh': '都营巴士', 'en': 'Toei buses'},
    '日本の鉄道路線一覧': {'zh': '日本铁路线路列表', 'en': 'List of railway lines in Japan'},
    '区間快速・普通': {'zh': '区间快速・普通', 'en': 'Section rapid and local trains'},
    '東京都統計年鑑': {'zh': '东京都统计年鉴', 'en': 'Tokyo Statistical Yearbook'},
    '山陰本線': {'zh': '山阴本线', 'en': 'Sanin Main Line'},
    '中央本線': {'zh': '中央本线', 'en': 'Chuo Main Line'},
    '奈良県の鉄道駅': {'zh': '奈良县的铁路车站', 'en': 'Railway stations in Nara Prefecture'},
    '茨城県の鉄道駅': {'zh': '茨城县的铁路车站', 'en': 'Railway stations in Ibaraki Prefecture'},
    '過去の接続路線': {'zh': '过去的衔接线路', 'en': 'Former connecting lines'},
    '新幹線': {'zh': '新干线', 'en': 'Shinkansen'},
    '駅舎': {'zh': '站房', 'en': 'Station building'},
    '使用車両': {'zh': '使用车辆', 'en': 'Rolling stock'},
    '接近メロディ': {'zh': '接近提示音', 'en': 'Approach melodies'},
    '東武鉄道': {'zh': '东武铁道', 'en': 'Tobu Railway'},
    'ホーム': {'zh': '站台', 'en': 'Platforms'},
    '改札': {'zh': '检票口', 'en': 'Ticket gates'},
    'トイレ': {'zh': '卫生间', 'en': 'Restrooms'},
    'バリアフリー': {'zh': '无障碍设施', 'en': 'Accessibility'},
    '待合室': {'zh': '候车室', 'en': 'Waiting room'},
    '売店': {'zh': '商店', 'en': 'Kiosk'},
    '自動券売機': {'zh': '自动售票机', 'en': 'Ticket machines'},
    '乗車人員': {'zh': '乘车人数', 'en': 'Boarding passengers'},
    '降車人員': {'zh': '下车人数', 'en': 'Alighting passengers'},
    '乗降人員': {'zh': '上下车人数', 'en': 'Passenger numbers'},
    '開業': {'zh': '开业', 'en': 'Opening'},
    '廃止': {'zh': '废止', 'en': 'Closure'},
    '路線図': {'zh': '线路图', 'en': 'Route map'},
    '接続路線': {'zh': '衔接线路', 'en': 'Connecting lines'},
    '乗換': {'zh': '换乘', 'en': 'Transfer'},
    '定期券': {'zh': '月票', 'en': 'Commuter passes'},
    '駅前': {'zh': '站前', 'en': 'Station forecourt'},
    '周辺施設': {'zh': '周边设施', 'en': 'Nearby facilities'},
    '観光': {'zh': '旅游', 'en': 'Tourism'},
    'イベント': {'zh': '活动', 'en': 'Events'},
    '歴史年表': {'zh': '历史年表', 'en': 'History timeline'},
    '駅務': {'zh': '站务', 'en': 'Station operations'},
    '営業': {'zh': '营业', 'en': 'Operations'},
    '廃駅': {'zh': '废站', 'en': 'Closed station'},
    '信号': {'zh': '信号', 'en': 'Signals'},
    '勾配': {'zh': '坡度', 'en': 'Gradient'},
    '橋梁': {'zh': '桥梁', 'en': 'Bridges'},
    'トンネル': {'zh': '隧道', 'en': 'Tunnels'},
    '踏切': {'zh': '道口', 'en': 'Level crossings'},
    '運行': {'zh': '运行', 'en': 'Operations'},
    'ダイヤ': {'zh': '时刻表', 'en': 'Timetable'},
    '料金': {'zh': '票价', 'en': 'Fares'},
    '乗車券': {'zh': '车票', 'en': 'Tickets'},
    '特急料金': {'zh': '特急票价', 'en': 'Limited express fares'},
    '駅名標': {'zh': '站名牌', 'en': 'Station signs'},
    '広告': {'zh': '广告', 'en': 'Advertising'},
    '出口': {'zh': '出口', 'en': 'Exits'},
    '案内': {'zh': '指引', 'en': 'Guidance'},
    '放送': {'zh': '广播', 'en': 'Announcements'},
    '非常口': {'zh': '紧急出口', 'en': 'Emergency exits'},
    '点字': {'zh': '盲文', 'en': 'Braille'},
    '車椅子': {'zh': '轮椅', 'en': 'Wheelchairs'},
    '駅員': {'zh': '车站工作人员', 'en': 'Station staff'},
    '委託': {'zh': '委托', 'en': 'Entrustment'},
    '簡易委託': {'zh': '简易委托', 'en': 'Simple entrustment'},
    '無人駅': {'zh': '无人车站', 'en': 'Unstaffed station'},
    '有人駅': {'zh': '有人车站', 'en': 'Staffed station'},
    '直営': {'zh': '直营', 'en': 'Direct management'},
    '管理駅': {'zh': '管理车站', 'en': 'Managed station'},
    '所属': {'zh': '所属', 'en': 'Affiliation'},
    '管轄': {'zh': '管辖', 'en': 'Jurisdiction'},
    '所在地': {'zh': '所在地', 'en': 'Location'},
    '位置': {'zh': '位置', 'en': 'Location'},
    '地図': {'zh': '地图', 'en': 'Map'},
    'アクセス': {'zh': '交通方式', 'en': 'Access'},
    '駐車場': {'zh': '停车场', 'en': 'Parking'},
    '駐輪場': {'zh': '自行车停车场', 'en': 'Bicycle parking'},
    'コインロッカー': {'zh': '投币储物柜', 'en': 'Coin lockers'},
    '待合所': {'zh': '候车处', 'en': 'Waiting area'},
    '階段': {'zh': '楼梯', 'en': 'Stairs'},
    'エスカレーター': {'zh': '自动扶梯', 'en': 'Escalators'},
    'エレベーター': {'zh': '电梯', 'en': 'Elevators'},
    '案内所': {'zh': '咨询处', 'en': 'Information'},
    '自動改札': {'zh': '自动检票机', 'en': 'Automatic ticket gates'},
    '駅ナカ': {'zh': '站内商业', 'en': 'Station shops'},
    'フード': {'zh': '餐饮', 'en': 'Food'},
    '飲料': {'zh': '饮料', 'en': 'Drinks'},
    '新聞': {'zh': '报纸', 'en': 'Newspapers'},
    'タクシー': {'zh': '出租车', 'en': 'Taxis'},
    'バス': {'zh': '巴士', 'en': 'Buses'},
    '航空': {'zh': '航空', 'en': 'Aviation'},
    '船舶': {'zh': '船舶', 'en': 'Ships'},
    '貨物': {'zh': '货物', 'en': 'Freight'},
    '荷物': {'zh': '行李', 'en': 'Baggage'},
    '郵便': {'zh': '邮政', 'en': 'Post'},
    '電報': {'zh': '电报', 'en': 'Telegrams'},
    '電話': {'zh': '电话', 'en': 'Telephones'},
    '施設': {'zh': '设施', 'en': 'Facilities'},
    '設備': {'zh': '设备', 'en': 'Equipment'},
    '歴史的建造物': {'zh': '历史建筑', 'en': 'Historic buildings'},
    '文化財': {'zh': '文物', 'en': 'Cultural properties'},
    '名所': {'zh': '名胜', 'en': 'Sights'},
    '旧跡': {'zh': '古迹', 'en': 'Historic sites'},
    '神社': {'zh': '神社', 'en': 'Shrines'},
    '寺院': {'zh': '寺院', 'en': 'Temples'},
    '公園': {'zh': '公园', 'en': 'Parks'},
    '学校': {'zh': '学校', 'en': 'Schools'},
    '病院': {'zh': '医院', 'en': 'Hospitals'},
    '役所': {'zh': '政府机关', 'en': 'Public offices'},
    '図書館': {'zh': '图书馆', 'en': 'Libraries'},
    '郵便局': {'zh': '邮局', 'en': 'Post offices'},
    '警察署': {'zh': '警察局', 'en': 'Police stations'},
    '消防署': {'zh': '消防局', 'en': 'Fire stations'},
    '銀行': {'zh': '银行', 'en': 'Banks'},
    '商店街': {'zh': '商业街', 'en': 'Shopping streets'},
    'デパート': {'zh': '百货公司', 'en': 'Department stores'},
    'ホテル': {'zh': '酒店', 'en': 'Hotels'},
    '旅館': {'zh': '旅馆', 'en': 'Inns'},
    '温泉': {'zh': '温泉', 'en': 'Hot springs'},
    '海水浴場': {'zh': '海水浴场', 'en': 'Beaches'},
    'スキー場': {'zh': '滑雪场', 'en': 'Ski resorts'},
    '体育館': {'zh': '体育馆', 'en': 'Gymnasiums'},
    '競技場': {'zh': '竞技场', 'en': 'Stadiums'},
    '球場': {'zh': '球场', 'en': 'Ballparks'},
    '劇場': {'zh': '剧场', 'en': 'Theaters'},
    '映画館': {'zh': '电影院', 'en': 'Cinemas'},
    '美術館': {'zh': '美术馆', 'en': 'Art museums'},
    '博物館': {'zh': '博物馆', 'en': 'Museums'},
    '動物園': {'zh': '动物园', 'en': 'Zoos'},
    '植物園': {'zh': '植物园', 'en': 'Botanical gardens'},
    '遊園地': {'zh': '游乐园', 'en': 'Amusement parks'},
    '競馬場': {'zh': '赛马场', 'en': 'Horse racing tracks'},
    '神社仏閣': {'zh': '神社寺院', 'en': 'Shrines and temples'},
    '観光名所': {'zh': '旅游景点', 'en': 'Tourist attractions'},
    '周辺の観光': {'zh': '周边观光', 'en': 'Nearby attractions'},
    'イベント開催': {'zh': '活动举办', 'en': 'Events'},
    '祭り': {'zh': '祭典', 'en': 'Festivals'},
    '花火大会': {'zh': '烟花大会', 'en': 'Fireworks displays'},
    '紅葉': {'zh': '红叶', 'en': 'Autumn leaves'},
    '桜': {'zh': '樱花', 'en': 'Cherry blossoms'},
}

def norm_key(s):
    s = KANA.sub('X', s)
    return re.sub(r'[\s\u3000「」『』（）()・,，、．.·\-—]', '', s)

def secs_of(w):
    s = w.get('章节') if w else None
    if not s: return {}
    if isinstance(s, dict): return dict(s)
    if isinstance(s, list):
        out = {}
        for it in s:
            if isinstance(it, dict):
                k = it.get('标题') or it.get('name') or it.get('title') or ''
                v = it.get('内容') or it.get('content') or it.get('text') or ''
                if k: out[k] = v
        return out
    return {}

def secs_to_list(w):
    s = w.get('章节') if w else None
    if isinstance(s, list):
        return s
    return [{'标题': k, '内容': v} for k, v in (s or {}).items()]

TOK_RE = re.compile(r'[一-龯]{2,}|\d+\.?\d*|[A-Za-z]{2,}')
def toks(s):
    if not s:
        return set()
    return set(TOK_RE.findall(s))

def has_counterpart(v, cur_values, th=4):
    """v（日文内容）在目标章节内容中是否有 token 重叠对应"""
    tv = toks(v)
    if not tv:
        return False
    for cv in cur_values:
        if len(toks(cv) & tv) >= th:
            return True
    return False

def build_full(lead, sec_items):
    parts = [lead.strip()] if lead and lead.strip() else []
    for it in sec_items:
        k, v = it.get('标题', ''), it.get('内容', '')
        if k and v:
            parts.append(f"{k}\n{v}")
    return '\n\n'.join(parts)

class API:
    def __init__(self, workers=48):
        self.lock = threading.Lock()
        self.sem = threading.Semaphore(workers)
        self.ok = 0; self.fail = 0; self.calls = 0
        self.logf = open(LOG, "a", encoding="utf-8")

    def _call(self, prompt, max_tokens=2048):
        url = f"{API_BASE}/chat/completions"
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/json",
            "User-Agent": "Index-Translate-Client/1.0",
        })
        last_err = None
        for attempt in range(10):
            if not self.sem.acquire(timeout=8):
                continue
            try:
                with urllib.request.urlopen(req, timeout=240) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    content = res["choices"][0]["message"]["content"].strip()
                    if "</think>" in content:
                        content = content.split("</think>", 1)[1].strip()
                    with self.lock:
                        self.ok += 1; self.calls += 1
                        if self.calls % 40 == 0:
                            self.logf.write(f"[{time.strftime('%H:%M:%S')}] ok={self.ok} fail={self.fail}\n"); self.logf.flush()
                    return content
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 307, 308, 429, 405) or e.code >= 500:
                    time.sleep(1 + attempt * 1.5 + random.random())
                elif e.code == 400:
                    body = e.read().decode("utf-8", errors="replace")[:200]
                    with self.lock:
                        self.fail += 1
                        self.logf.write(f"[{time.strftime('%H:%M:%S')}] HTTP400 {body[:120]}\n"); self.logf.flush()
                    return None
                else:
                    body = e.read().decode("utf-8", errors="replace")[:150]
                    with self.lock:
                        self.fail += 1
                        self.logf.write(f"[{time.strftime('%H:%M:%S')}] HTTPERR {e.code} {body[:100]}\n"); self.logf.flush()
                    return None
            except Exception as e:
                last_err = str(e)[:120]
                time.sleep(1 + attempt * 1.2 + random.random())
            finally:
                self.sem.release()
        with self.lock:
            self.fail += 1
            self.logf.write(f"[{time.strftime('%H:%M:%S')}] FAIL {last_err}\n"); self.logf.flush()
        return None

    def translate(self, text, target, source="ja", max_tokens=2048, extra=""):
        tgt = {"zh": "中文", "en": "英语"}[target]
        src = "日语" if source == "ja" else ""
        prompt = f"请将以下{src}文本翻译为{tgt}，直接输出翻译结果，不要进行任何解释。{extra}\n\n{text.strip()}"
        return self._call(prompt, max_tokens)

    def trans_key(self, jp_key, target):
        tgt = {"zh": "中文", "en": "英语"}[target]
        prompt = (f"请将以下日语维基百科的章节标题翻译为{tgt}，只输出译文标题本身，不要解释，不要加引号。\n\n{jp_key.strip()}")
        out = self._call(prompt, 256)
        if not out:
            return None
        return out.strip().strip('"').strip('「」')

    def trans_content_only(self, content, target):
        """只翻译章节正文，标题保留不变"""
        tgt = {"zh": "中文", "en": "英语"}[target]
        prompt = (
            f"请将以下日语维基百科章节正文完整翻译为{tgt}，保留换行、序号、日期、括号与专名（站名/列车名采用通用译名或保留罗马字）。\n"
            f"【重要】输出必须为纯{tgt}文本，不得包含任何日文假名（平假名/片假名）、日文注音或日文残留词；原文中出现的日文站名注音、公司名假名等一律译出或删除。\n"
            f"直接输出译文正文，不要任何解释、标题或额外文字。\n\n{content.strip()}"
        )
        return self._call(prompt, 4096)

    def trans_section(self, jp_key, jp_content, target):
        tgt = {"zh": "中文", "en": "英语"}[target]
        prompt = (
            f"请将以下日语维基百科章节【标题】与【正文】翻译成{tgt}。\n"
            f"【注意】标题译成简洁自然的目标语言标题；正文完整翻译所有内容，保留换行、序号、日期与括号；站名、列车名等专名采用通用译名或保留罗马字。\n"
            f"【重要】输出必须为纯{tgt}文本，不得包含任何日文假名（平假名/片假名）、日文注音或日文残留词；原文中的日文站名注音、公司名假名等一律译出或删除。\n"
            f"只输出 JSON 对象 {{\"key\": \"译文标题\", \"content\": \"译文正文\"}}，不要任何额外文字。\n\n"
            f"【标题】\n{jp_key}\n\n【正文】\n{jp_content}"
        )
        out = self._call(prompt, 4096)
        if not out:
            return None
        m = re.search(r'\{[\s\S]*\}', out)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and obj.get('key'):
                return str(obj['key']).strip(), str(obj.get('content', '')).strip()
        except Exception:
            return None
        return None

    def trans_names_batch(self, jps, target):
        """批量翻译名称：jps 为日文原名列表，返回 {原文: 译文}"""
        tgt = {"zh": "中文", "en": "英语"}[target]
        zh_hint = ("使用规范中文站名/线路名：汉字站名沿用规范汉字并加「站」；"
                   "假名站名译为对应规范汉字站名（如 すすきの→薄野站、しきぶ→式部站），不确定时保留假名并加「站」。")
        en_hint = "使用 Hepburn 式罗马字，站名以 Station 结尾，线路名保持常用英文名。"
        hint = zh_hint if target == 'zh' else en_hint
        lines = "\n".join(f"{i+1}. {jp}" for i, jp in enumerate(jps))
        prompt = (
            f"请将以下日语车站名/线路名逐一翻译为{tgt}。{hint}\n"
            f"严格输出 JSON 数组，形如 [[\"1\",\"译文1\"],[\"2\",\"译文2\"],...]，不要任何其他文字。\n\n{lines}"
        )
        out = self._call(prompt, 2048)
        if not out:
            return None
        m = re.search(r'\[[\s\S]*\]', out)
        if not m:
            return None
        try:
            arr = json.loads(m.group(0))
        except Exception:
            return None
        res = {}
        if not isinstance(arr, list):
            return None
        if arr and all(isinstance(x, str) for x in arr):
            # ["译文1","译文2",...]
            for i, val in enumerate(arr):
                if i < len(jps) and val.strip():
                    res[jps[i]] = val.strip()
        elif arr and isinstance(arr[0], list):
            if len(arr) == 1 and isinstance(arr[0], list) and all(isinstance(x, str) for x in arr[0]) and len(arr[0]) == len(jps):
                # 单层嵌套：[[译文1, 译文2, ...]]
                for i, val in enumerate(arr[0]):
                    if val.strip():
                        res[jps[i]] = val.strip()
            else:
                # [[序号, 译文], ...]
                for item in arr:
                    try:
                        if len(item) < 2:
                            continue
                        idx = int(item[0]) - 1
                        val = str(item[1]).strip()
                        if 0 <= idx < len(jps) and val:
                            res[jps[idx]] = val
                    except Exception:
                        continue
        return res

    def trans_kana_batch(self, segs, target):
        """批量翻译日文假名片段（片假名/平假名词）为 target 语言，返回 {原文: 译文}"""
        tgt = {"zh": "中文", "en": "英语"}[target]
        zh_hint = ("这些是日语假名片段，通常嵌在中文文本中，是未翻译干净的日文残留（语气词、助词、注音、专名等）。"
                   "请翻译为中文：普通词汇与专名用中文通用译名；无法翻译的日文语气词/助词（如 る、た、する 等）直接输出空或删除；"
                   "输出必须为纯中文，不得保留任何日文假名。")
        en_hint = ("These are Japanese words (katakana/hiragana). Translate ordinary words into English; "
                   "company/bus operator names use common English names; loanwords keep natural English.")
        hint = zh_hint if target == 'zh' else en_hint
        lines = "\n".join(f"{i+1}. {seg}" for i, seg in enumerate(segs))
        prompt = (
            f"请将以下日语假名片段逐一翻译为{tgt}。{hint}\n"
            f"严格输出 JSON 数组，形如 [[\"1\",\"译文1\"],[\"2\",\"译文2\"],...]，不要任何其他文字。\n\n{lines}"
        )
        out = self._call(prompt, 2048)
        if not out:
            return None
        m = re.search(r'\[[\s\S]*\]', out)
        if not m:
            return None
        try:
            arr = json.loads(m.group(0))
        except Exception:
            return None
        res = {}
        if not isinstance(arr, list):
            return None
        if arr and all(isinstance(x, str) for x in arr):
            for i, val in enumerate(arr):
                if i < len(segs) and val.strip():
                    res[segs[i]] = val.strip()
        elif arr and isinstance(arr[0], list):
            for item in arr:
                try:
                    if len(item) < 2:
                        continue
                    idx = int(item[0]) - 1
                    val = str(item[1]).strip()
                    if 0 <= idx < len(segs) and val:
                        res[segs[idx]] = val
                except Exception:
                    continue
        return res

class Worker:
    def __init__(self, api):
        self.api = api
        self.keymap = {}
        self.keymap_lock = threading.Lock()
        self.name_map = {}
        self.name_lock = threading.Lock()

    def get_key(self, jp_key, target):
        st = STATIC_KEYS.get(jp_key)
        if st and st.get(target):
            return st[target]
        with self.keymap_lock:
            e = self.keymap.get(jp_key)
            if e and e.get(target):
                return e[target]
        t = self.api.trans_key(jp_key, target)
        if t:
            with self.keymap_lock:
                self.keymap.setdefault(jp_key, {})[target] = t
        return t

    def get_name(self, jp_name, target):
        with self.name_lock:
            e = self.name_map.get(jp_name)
            if e and e.get(target):
                return e[target]
        extra = "使用规范中文站名，保留必要读音。" if target == 'zh' else "使用 Hepburn 式罗马字，站名以 Station 结尾。"
        t = self.api.translate(jp_name, target, max_tokens=128, extra=extra)
        if t:
            with self.name_lock:
                self.name_map.setdefault(jp_name, {})[target] = t
        return t

    def fill_names_batch(self, jps, target):
        """jps: 日文原名列表；批量翻译后写入 name_map"""
        # 去重：同一原文只翻一次
        uniq = []
        seen = set()
        for src in jps:
            if src and src not in seen:
                seen.add(src)
                uniq.append(src)
        if not uniq:
            return 0
        hit = 0
        B = 25
        for i in range(0, len(uniq), B):
            chunk = uniq[i:i + B]
            with self.name_lock:
                todo = [jp for jp in chunk if not (self.name_map.get(jp) or {}).get(target)]
            if not todo:
                hit += len(chunk)
                continue
            res = self.api.trans_names_batch(todo, target)
            if res:
                with self.name_lock:
                    for jp, val in res.items():
                        self.name_map.setdefault(jp, {})[target] = val
                hit += len(res)
        return hit

    def trans_whole(self, jp_wiki, target):
        jp_title = jp_wiki.get('词条标题') or ''
        jp_lead = jp_wiki.get('首段') or ''
        jp_secs = secs_to_list(jp_wiki)
        t_title = self.api.translate(jp_title, target, max_tokens=256) if jp_title else ''
        t_lead = self.api.translate(jp_lead, target, max_tokens=4096) if jp_lead else ''
        sec_items = []
        for it in jp_secs:
            k, v = it.get('标题', ''), it.get('内容', '')
            if not k or not v:
                continue
            r = self.api.trans_section(k, v, target)
            if r:
                nk, nv = r
                sec_items.append({'标题': nk, '内容': nv})
                with self.keymap_lock:
                    self.keymap.setdefault(k, {})[target] = nk
            else:
                sec_items.append({'标题': k, '内容': v})
        if target == 'zh':
            link = f"https://zh.wikipedia.org/wiki/{urllib.parse.quote(jp_title)}" if jp_title else ""
        else:
            en_t = (t_title or jp_title).replace(' ', '_')
            link = f"https://en.wikipedia.org/wiki/{en_t}" if en_t else ""
        full = build_full(t_lead, sec_items)
        out = {
            '词条标题': t_title, '链接': link, '首段': t_lead,
            '章节': {i['标题']: i['内容'] for i in sec_items},
            '全文': full, '字数': len(full),
        }
        if target == 'zh':
            out['来源'] = 'Index-Translate全文翻译（据日文维基全文）'
        return out

    def fill_partial(self, jp_wiki, obj, target):
        """修复已有百科：键名含日文 / 内容含日文 / 缺失章节。返回是否改动"""
        changed = False
        jp_secs = secs_of(jp_wiki)
        cur = dict(obj.get('章节') or {})
        norm_cur = {norm_key(k): k for k in cur}

        def put(nk, nv):
            nonlocal changed
            nk2 = nk.strip()
            if not nk2:
                return
            if nk2 in cur:
                return
            nn = norm_key(nk2)
            if nn in norm_cur and norm_cur[nn] != nk2:
                return  # 同义键已存在，跳过避免覆盖
            cur[nk2] = nv
            norm_cur[nn] = nk2
            changed = True

        # 阶段1：修复已有章节（键名或内容含日文）
        for k in list(cur.keys()):
            v = cur.get(k) or ''
            kj, vj = is_jp_key(k), has_jp(v)
            if not kj and not vj:
                continue
            if kj and vj:
                r = self.api.trans_section(k, v, target)
                if r:
                    nk, nv = r
                    cur.pop(k)
                    del_norm = norm_key(k)
                    if norm_cur.get(del_norm) == k:
                        norm_cur.pop(del_norm, None)
                    put(nk, nv)
                    with self.keymap_lock:
                        self.keymap.setdefault(k, {})[target] = nk
            elif kj and not vj:
                nk = self.get_key(k, target)
                if nk and nk != k:
                    cur.pop(k)
                    del_norm = norm_key(k)
                    if norm_cur.get(del_norm) == k:
                        norm_cur.pop(del_norm, None)
                    put(nk, v)
            else:  # 键名正常、内容含日文 → 只翻内容
                nv = self.api.trans_content_only(v, target)
                if nv:
                    cur[k] = nv
                    changed = True
        # 阶段2：日文有、目标无 → 整节翻译
        for k, v in jp_secs.items():
            if not k or not v:
                continue
            kt = self.get_key(k, target)
            hit = kt and (kt in cur or norm_key(kt) in norm_cur)
            if hit:
                continue
            r = self.api.trans_section(k, v, target)
            if r:
                nk, nv = r
                put(nk, nv)
                with self.keymap_lock:
                    self.keymap.setdefault(k, {})[target] = nk
        if changed:
            obj['章节'] = cur
            lead = obj.get('首段') or ''
            obj['全文'] = build_full(lead, [{'标题': k, '内容': v} for k, v in cur.items()])
            obj['字数'] = len(obj['全文'])
        return changed

    def process_kana(self, d, path):
        """清理章节内容中的日文假名残留：收集唯一假名段→批量翻译→替换回内容"""
        changed = False
        for lang in ('zh', 'en'):
            segs = set()
            locs = []  # (obj, secs, ref)
            def collect(key, obj):
                secs = key.get('章节')
                if isinstance(secs, dict):
                    for k, v in list(secs.items()):
                        for seg in KANA_SEG.findall(str(v)):
                            segs.add(seg.strip())
                        locs.append((obj, secs, k))
                elif isinstance(secs, list):
                    for s in secs:
                        if isinstance(s, dict):
                            v = s.get('内容')
                            for seg in KANA_SEG.findall(str(v or '')):
                                segs.add(seg.strip())
                            locs.append((obj, secs, s))
            for st in d.get('车站') or []:
                ek = st.get('百科') or {}
                if isinstance(ek, dict):
                    key = ek.get(LK[lang])
                    if isinstance(key, dict):
                        collect(key, key)
            lb = (d.get('线路') or {}).get('百科') or {}
            if isinstance(lb, dict):
                key = lb.get(LK[lang])
                if isinstance(key, dict):
                    collect(key, key)
            if not segs:
                continue
            ordered = sorted(segs, key=len, reverse=True)
            mapping = {}
            for i in range(0, len(ordered), 40):
                r = self.api.trans_kana_batch(ordered[i:i + 40], lang)
                if r:
                    mapping.update(r)
            failed_locs = []
            for obj, secs, ref in locs:
                if isinstance(secs, dict):
                    k = ref
                    v = secs.get(k, '')
                    nv = v
                    for seg in ordered:
                        t = mapping.get(seg)
                        if t and t != seg:
                            nv = nv.replace(seg, t)
                        elif len(seg) == 1:
                            nv = nv.replace(seg, '')
                    if nv != v:
                        secs[k] = nv
                        changed = True
                    if KANA.search(secs.get(k, '')):
                        failed_locs.append((obj, secs, k))
                else:
                    s = ref
                    v = s.get('内容') or ''
                    nv = v
                    for seg in ordered:
                        t = mapping.get(seg)
                        if t and t != seg:
                            nv = nv.replace(seg, t)
                        elif len(seg) == 1:
                            nv = nv.replace(seg, '')
                    if nv != v:
                        s['内容'] = nv
                        changed = True
                    if KANA.search(s.get('内容') or ''):
                        failed_locs.append((obj, secs, s))
            # 上下文重翻仍含假名的章节（翻译失败的段）
            for obj, secs, ref in failed_locs:
                if isinstance(secs, dict):
                    v = secs.get(ref, '')
                else:
                    v = ref.get('内容') or ''
                nv = self.api.trans_content_only(v, lang)
                if nv and nv != v:
                    if isinstance(secs, dict):
                        secs[ref] = nv
                    else:
                        ref['内容'] = nv
                    changed = True
                # 最终兜底：重翻后仍含假名 → 直接删除假名段（保证无日文残留）
                cur_v = secs.get(ref, '') if isinstance(secs, dict) else (ref.get('内容') or '')
                if KANA.search(cur_v):
                    cur_v2 = KANA_SEG.sub('', cur_v)
                    if cur_v2 != cur_v:
                        if isinstance(secs, dict):
                            secs[ref] = cur_v2
                        else:
                            ref['内容'] = cur_v2
                        changed = True
            # 替换后同步全文/字数
            for obj, secs, ref in locs:
                lead = obj.get('首段') or ''
                if isinstance(secs, dict):
                    items = [{'标题': k, '内容': v} for k, v in secs.items()]
                else:
                    items = [{'标题': s.get('标题', ''), '内容': s.get('内容', '')} for s in secs if isinstance(s, dict)]
                obj['全文'] = build_full(lead, items)
                obj['字数'] = len(obj['全文'])
        if changed:
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        return changed

    def process_file(self, path, phase='all'):
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        if phase == 'kana':
            return self.process_kana(d, path)
        tasks = []          # 每个任务: {'kind','args','res'}
        whole_store = {}    # oid_key -> {'title','lead','secs','ok'}
        changed_objs = {}   # id(obj) -> obj（章节/整篇被修改的百科对象）
        want = 'all'
        if phase == 'fix':
            want = 'fix'
        elif phase == 'missing':
            want = 'missing'

        def add_task(kind, **args):
            if want == 'fix' and kind == 'sec_missing':
                return None
            if want == 'missing' and kind != 'sec_missing':
                return None
            t = {'kind': kind, 'args': args, 'res': None}
            tasks.append(t)
            return t

        line = d.get('线路') or {}
        lb = line.get('百科') or {}
        lname = line.get('名称') or {}
        jp = lb.get('日文') or {}
        stations = d.get('车站') or []

        # ---- 收集：名称 ----
        name_items = {'zh': [], 'en': []}
        def name_src(name):
            return name.get('词条') or name.get('日文原名') or name.get('日文')
        if lname:
            for lang in ('zh', 'en'):
                src = name_src(lname)
                if src and not lname.get(LK[lang]):
                    name_items[lang].append(src)
        for st in stations:
            name = st.get('名称') or {}
            for lang in ('zh', 'en'):
                src = name_src(name)
                if src and not name.get(LK[lang]):
                    name_items[lang].append(src)
        name_items['zh'] = list(dict.fromkeys(name_items['zh']))
        name_items['en'] = list(dict.fromkeys(name_items['en']))
        for lang, items in name_items.items():
            for i in range(0, len(items), 25):
                add_task('name_batch', lang=lang, chunk=items[i:i + 25])

        # ---- 收集：整篇缺失（拆子任务并行）----
        def collect_whole(jp_wiki, lang, obj, kind):
            oid_key = (kind, id(obj), lang)
            store = {'title': '', 'lead': '', 'secs': [], 'ok': 0, 'lock': threading.Lock()}
            whole_store[oid_key] = store
            if jp_wiki.get('词条标题'):
                add_task('w_title', jp_title=jp_wiki['词条标题'], lang=lang, oid_key=oid_key)
            if jp_wiki.get('首段'):
                add_task('w_lead', jp_lead=jp_wiki['首段'], lang=lang, oid_key=oid_key)
            for it in secs_to_list(jp_wiki):
                k, v = it.get('标题', ''), it.get('内容', '')
                if k and v:
                    add_task('w_sec', k=k, v=v, lang=lang, oid_key=oid_key)
            changed_objs[id(obj)] = obj

        # ---- 收集：已有百科的修复 ----
        def collect_fixes(jp_wiki, obj, lang):
            jp_secs = secs_of(jp_wiki)
            cur = secs_of(obj)
            norm_cur = {norm_key(k): k for k in cur}
            # 修复已有章节
            for k in list(cur.keys()):
                v = cur.get(k) or ''
                kj, vj = is_jp_key(k), has_jp(v)
                if not kj and not vj:
                    continue
                if kj and vj:
                    add_task('sec_full', k=k, v=v, lang=lang, obj=obj)
                elif kj:
                    add_task('sec_key', k=k, lang=lang, obj=obj)
                else:
                    add_task('sec_content', k=k, v=v, lang=lang, obj=obj)
            # 缺失章节
            cur_vals = list(cur.values())
            for k, v in jp_secs.items():
                if not k or not v:
                    continue
                kt = None
                stk = STATIC_KEYS.get(k)
                if stk:
                    kt = stk.get(lang)
                if not kt:
                    with self.keymap_lock:
                        e = self.keymap.get(k)
                        if e:
                            kt = e.get(lang)
                hit = kt and (kt in cur or norm_key(kt) in norm_cur)
                if not hit and lang == 'zh':
                    # 中文侧：token 重叠判定（键名风格不统一时的兜底）
                    hit = has_counterpart(v, cur_vals, 4)
                if hit:
                    continue
                add_task('sec_missing', k=k, v=v, lang=lang, obj=obj)

        if lb and jp:
            for lang in ('zh', 'en'):
                if not lb.get(LK[lang]):
                    collect_whole(jp, lang, lb, 'line')
                elif lb.get(LK[lang]):
                    collect_fixes(jp, lb[LK[lang]], lang)
        for st in stations:
            ek = st.get('百科') or {}
            jp = ek.get('日文') or {}
            if ek and jp:
                for lang in ('zh', 'en'):
                    if not ek.get(LK[lang]):
                        collect_whole(jp, lang, ek, 'st')
                    elif ek.get(LK[lang]):
                        collect_fixes(jp, ek[LK[lang]], lang)

        # ---- 执行（文件内并行；全局并发由 API.sem 控制）----
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(self._run_task, t, whole_store) for t in tasks]
            for fut in futs:
                try:
                    fut.result()
                except Exception as e:
                    print(f'[task-err] {path}: {e}', flush=True)

        # ---- 回填（主线程）----
        changed = False
        # 名称
        for lang, items in name_items.items():
            for src in items:
                with self.name_lock:
                    tval = (self.name_map.get(src) or {}).get(lang)
                if not tval:
                    continue
                if lname:
                    src2 = name_src(lname)
                    if src2 == src and not lname.get(LK[lang]):
                        lname[LK[lang]] = tval
                        changed = True
                for st in stations:
                    name = st.get('名称') or {}
                    src2 = name_src(name)
                    if src2 == src and not name.get(LK[lang]):
                        name[LK[lang]] = tval
                        changed = True
        # 整篇
        for (kind, oid, lang), store in whole_store.items():
            obj = lb if kind == 'line' else None
            if kind != 'line':
                for st in stations:
                    if id(st.get('百科') or {}) == oid:
                        obj = st.get('百科')
                        break
            if not obj or store.get('ok', 0) == 0:
                continue
            t_title = store.get('title') or ''
            t_lead = store.get('lead') or ''
            sec_items = store.get('secs', [])
            if not t_lead and not sec_items:
                continue
            if lang == 'zh':
                link = f"https://zh.wikipedia.org/wiki/{urllib.parse.quote(t_title)}" if t_title else ""
            else:
                link = f"https://en.wikipedia.org/wiki/{(t_title or '').replace(' ', '_')}" if t_title else ""
            full = build_full(t_lead, sec_items)
            out = {
                '词条标题': t_title, '链接': link, '首段': t_lead,
                '章节': {i['标题']: i['内容'] for i in sec_items},
                '全文': full, '字数': len(full),
            }
            if lang == 'zh':
                out['来源'] = 'Index-Translate全文翻译（据日文维基全文）'
            obj[LK[lang]] = out
            changed = True
        # 章节修复
        for t in tasks:
            if t['kind'] not in ('sec_full', 'sec_key', 'sec_content', 'sec_missing'):
                continue
            obj = t['args']['obj']
            lang = t['args']['lang']
            sec_orig = obj.get('章节')
            cur = secs_of(obj)
            norm_cur = {norm_key(k): k for k in cur}
            applied = False
            if t['kind'] == 'sec_full':
                k = t['args']['k']
                if t['res']:
                    nk, nv = t['res']
                    cur.pop(k, None)
                    nn = norm_key(nk)
                    if nn not in norm_cur:
                        cur[nk] = nv
                        norm_cur[nn] = nk
                        applied = True
            elif t['kind'] == 'sec_key':
                k = t['args']['k']
                if t['res'] and t['res'] != k:
                    nk = t['res']
                    v = cur.pop(k, '')
                    dn = norm_key(k)
                    if norm_cur.get(dn) == k:
                        norm_cur.pop(dn, None)
                    nn = norm_key(nk)
                    if nn not in norm_cur:
                        cur[nk] = v
                        norm_cur[nn] = nk
                        applied = True
                    else:
                        applied = True  # 同义键已存在：日文键已移除
            elif t['kind'] == 'sec_content':
                k = t['args']['k']
                if t['res']:
                    cur[k] = t['res']
                    applied = True
            elif t['kind'] == 'sec_missing':
                k, v = t['args']['k'], t['args']['v']
                if t['res']:
                    nk, nv = t['res']
                    nn = norm_key(nk)
                    if nn not in norm_cur:
                        cur[nk] = nv
                        norm_cur[nn] = nk
                        applied = True
            if applied:
                if isinstance(sec_orig, list):
                    obj['章节'] = [{'标题': kk, '内容': vv} for kk, vv in cur.items()]
                else:
                    obj['章节'] = cur
                lead = obj.get('首段') or ''
                obj['全文'] = build_full(lead, [{'标题': kk, '内容': vv} for kk, vv in cur.items()])
                obj['字数'] = len(obj['全文'])
                changed = True
        # 全文缺失重拼（零 API：首段+章节 直接拼接）
        for st in stations:
            ek2 = st.get('百科') or {}
            if not isinstance(ek2, dict):
                continue
            for lang in ('zh', 'en'):
                o2 = ek2.get(LK[lang])
                if isinstance(o2, dict) and not o2.get('全文'):
                    lead2 = o2.get('首段') or ''
                    secs2 = secs_of(o2)
                    o2['全文'] = build_full(lead2, [{'标题': kk, '内容': vv} for kk, vv in secs2.items()])
                    o2['字数'] = len(o2['全文'])
                    changed = True
        if lb:
            for lang in ('zh', 'en'):
                o2 = lb.get(LK[lang])
                if isinstance(o2, dict) and not o2.get('全文'):
                    lead2 = o2.get('首段') or ''
                    secs2 = secs_of(o2)
                    o2['全文'] = build_full(lead2, [{'标题': kk, '内容': vv} for kk, vv in secs2.items()])
                    o2['字数'] = len(o2['全文'])
                    changed = True
        if changed:
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        return changed

    def _run_task(self, t, whole_store):
        kind = t['kind']
        a = t['args']
        if kind == 'name_batch':
            res = self.api.trans_names_batch(a['chunk'], a['lang'])
            if res:
                with self.name_lock:
                    for jp, val in res.items():
                        self.name_map.setdefault(jp, {})[a['lang']] = val
        elif kind == 'w_title':
            out = self.api.translate(a['jp_title'], a['lang'], max_tokens=256)
            with whole_store[a['oid_key']]['lock']:
                whole_store[a['oid_key']]['title'] = out or ''
                if out:
                    whole_store[a['oid_key']]['ok'] += 1
        elif kind == 'w_lead':
            out = self.api.translate(a['jp_lead'], a['lang'], max_tokens=4096)
            with whole_store[a['oid_key']]['lock']:
                whole_store[a['oid_key']]['lead'] = out or ''
                if out:
                    whole_store[a['oid_key']]['ok'] += 1
        elif kind == 'w_sec':
            r = self.api.trans_section(a['k'], a['v'], a['lang'])
            with whole_store[a['oid_key']]['lock']:
                if r:
                    nk, nv = r
                    whole_store[a['oid_key']]['secs'].append({'标题': nk, '内容': nv})
                    whole_store[a['oid_key']]['ok'] += 1
                    with self.keymap_lock:
                        self.keymap.setdefault(a['k'], {})[a['lang']] = nk
                else:
                    whole_store[a['oid_key']]['secs'].append({'标题': a['k'], '内容': a['v']})
        elif kind == 'sec_full':
            r = self.api.trans_section(a['k'], a['v'], a['lang'])
            if r:
                nk, nv = r
                t['res'] = (nk, nv)
                with self.keymap_lock:
                    self.keymap.setdefault(a['k'], {})[a['lang']] = nk
        elif kind == 'sec_key':
            nk = self.get_key(a['k'], a['lang'])
            t['res'] = nk
        elif kind == 'sec_content':
            nv = self.api.trans_content_only(a['v'], a['lang'])
            t['res'] = nv
        elif kind == 'sec_missing':
            r = self.api.trans_section(a['k'], a['v'], a['lang'])
            if r:
                nk, nv = r
                t['res'] = (nk, nv)
                with self.keymap_lock:
                    self.keymap.setdefault(a['k'], {})[a['lang']] = nk

# ---------------- 主流程 ----------------
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE, encoding='utf-8'))
        except Exception:
            pass
    return {'done': [], 'keymap': {}, 'name_map': {}}

def save_state(st):
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)

def git_push(batch_no):
    helper = os.path.join(REPO, '.git-cred-eki')
    cmds = [
        ['git', '-C', REPO, 'add', 'data/'],
        ['git', '-C', REPO, '-c', f'credential.helper={helper}', 'commit', '-m', f'eki-wiki 三语百科补译批次 {batch_no}（Index-Translate-35B）'],
        ['git', '-C', REPO, '-c', f'credential.helper={helper}', 'push', 'origin', 'main'],
    ]
    for c in cmds:
        r = subprocess.run(c, capture_output=True, text=True, timeout=300)
        merged = (r.stdout or '') + (r.stderr or '')
        if r.returncode != 0 and 'nothing to commit' not in merged and 'Everything up-to-date' not in merged:
            print(f'[push] 失败: {c[2] if len(c)>2 else c}: {merged[-300:]}', flush=True)
            return False
    print(f'[push] 批次 {batch_no} 推送完成', flush=True)
    return True

def main():
    args = sys.argv[1:]
    workers_n = 48
    push_every = 25
    phase = 'all'
    only = []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith('--phase='):
            phase = a.split('=', 1)[1]
        elif a.startswith('--push='):
            push_every = int(a.split('=', 1)[1])
        elif a.startswith('--workers='):
            workers_n = int(a.split('=', 1)[1])
        elif a == '--phase':
            i += 1
            phase = args[i]
        else:
            only.append(a)
        i += 1
    api = API(workers_n)
    worker = Worker(api)
    st = load_state()
    worker.keymap.update(st.get('keymap', {}))
    worker.name_map.update(st.get('name_map', {}))
    if phase == 'kana':
        done_key = 'done_kana'
    elif phase in ('missing', 'all'):
        done_key = 'done_missing'
    else:
        done_key = 'done'
    done = set(st.get(done_key, []))
    files = sorted(glob.glob(os.path.join(DATA_DIR, '*.json')))
    if only:
        files = [f for f in files if os.path.basename(f) in only]
    todo = [f for f in files if f not in done]
    print(f'总文件 {len(files)}，已完成 {len(done)}，待处理 {len(todo)}，并发 {workers_n}，phase={phase}', flush=True)

    done_lock = threading.Lock()
    counter = {'n': 0, 'changed': 0}
    push_batch = {'n': 0}

    def work(path):
        try:
            ch = worker.process_file(path, phase)
            with done_lock:
                done.add(path)
                counter['n'] += 1
                if ch:
                    counter['changed'] += 1
        except Exception as e:
            with done_lock:
                print(f'[err] {path}: {e}', flush=True)
                # 不标记 done：让后续 all 复查轮重试
                counter['n'] += 1

    def monitor():
        while True:
            time.sleep(30)
            with done_lock:
                n, chg = counter['n'], counter['changed']
            print(f"[进度] {time.strftime('%H:%M:%S')} 完成 {n}/{len(todo)} 变更 {chg}", flush=True)
            if n >= len(todo):
                break
    threading.Thread(target=monitor, daemon=True).start()

    idx = 0
    while idx < len(todo):
        batch = todo[idx:idx + workers_n]
        threads = [threading.Thread(target=work, args=(p,)) for p in batch]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        idx += workers_n
        with done_lock:
            st[done_key] = sorted(done)
            st['keymap'] = worker.keymap
            st['name_map'] = worker.name_map
        save_state(st)
        with done_lock:
            n = counter['n']
        if n - push_batch['n'] >= push_every or idx >= len(todo):
            push_batch['n'] = n
            git_push(push_batch['n'])
    print('全部完成', flush=True)

if __name__ == '__main__':
    main()
