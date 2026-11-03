#!/bin/bash
cd /home/user/work/eki-wiki
LOG=/tmp/eki_supervisor.log
mkdir -p /tmp
echo "[$(date '+%F %T')] supervisor 启动（fix→kana→missing→复查循环）" >> "$LOG"
while true; do
  echo "[$(date '+%F %T')] 启动 fix 阶段" >> "$LOG"
  python3 translate_pipeline.py --phase=fix --workers=48 --push=20 >> /tmp/fix_run.log 2>&1
  echo "[$(date '+%F %T')] fix 完成，启动 kana 清理阶段" >> "$LOG"
  python3 translate_pipeline.py --phase=kana --workers=48 --push=20 >> /tmp/kana_run.log 2>&1
  echo "[$(date '+%F %T')] kana 完成，启动 missing 阶段" >> "$LOG"
  python3 translate_pipeline.py --phase=missing --workers=48 --push=20 >> /tmp/missing_run.log 2>&1
  echo "[$(date '+%F %T')] missing 完成，启动 all 复查" >> "$LOG"
  python3 translate_pipeline.py --phase=all --workers=48 --push=20 >> /tmp/all_run.log 2>&1
  echo "[$(date '+%F %T')] 复查完成，60 秒后下一轮" >> "$LOG"
  sleep 60
done
