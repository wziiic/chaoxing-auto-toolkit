# -*- coding: utf-8 -*-
"""列出作业清单：标题/课程/状态/截止/入口链接。"""
import json
import os
import re
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
cookie = open(os.path.join(HERE, "cookie.txt"), encoding='utf-8').read().strip()
H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36',
     'Cookie': cookie, 'Accept': 'text/html,*/*'}

items = []
for page_no in (1, 2):
    url = 'https://mooc1-api.chaoxing.com/mooc2/work/all-task'
    if page_no > 1:
        url += f'?pageNum={page_no}'
    html = requests.get(url, headers=H, timeout=30).text

    for blk in re.split(r'(?=<li onclick="goTask)', html):
        m_url = re.search(r'data="(https?://[^"]*work/task[^"]*)"', blk)
        if not m_url:
            continue
        u = m_url.group(1)
        title = re.search(r'class="overHidden2 fl"[^>]*>(.*?)</p>', blk, re.S)
        course = re.search(r'《([^》]+)》', blk)
        status = re.search(r'class="stuStatus[^"]*"[^>]*>([^<]*)<', blk)
        left = re.search(r'剩余([0-9天小时分钟]+)', blk)
        items.append({
            'workId': re.search(r'workId=(\d+)', u).group(1),
            'title': re.sub(r'<[^>]+>', '', title.group(1)).strip() if title else '?',
            'course': course.group(1) if course else '?',
            'status': status.group(1).strip() if status else '?',
            'left': left.group(1) if left else '',
            'url': u,
        })

seen, uniq = set(), []
for it in items:
    if it['workId'] in seen:
        continue
    seen.add(it['workId'])
    uniq.append(it)

print(f"{'workId':<10} {'状态':<7} {'课程':<24} {'作业':<30} 剩余")
print('-' * 108)
for it in uniq:
    print(f"{it['workId']:<10} {it['status']:<7} {it['course'][:22]:<24} {it['title'][:28]:<30} {it['left'][:18]}")

json.dump(uniq, open(os.path.join(HERE, "worklist.json"),
                     'w', encoding='utf-8'), ensure_ascii=False, indent=1)
undone = [i for i in uniq if '未提交' in i['status']]
print(f"\n共 {len(uniq)} 个，未提交 {len(undone)} 个 → worklist.json")
