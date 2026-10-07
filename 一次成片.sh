#!/bin/bash
# 寄方堂人参视频 · 一键成片
#   ./一次成片.sh 01
#   ./一次成片.sh 02 /path/to/media --tts
#   ./一次成片.sh 02 --card-zh "23道芦碗·约26年" --card-en "..." --card-name "积水成渊" --citation "荀子·劝学"
# 详见同目录 SKILL.md
set -e
cd "$(dirname "$0")"
CH="$1"; shift || true
[ -n "$CH" ] || { echo "用法: ./一次成片.sh <章号> [素材目录] [--tts] [档案卡4字段]"; exit 1; }
CH=$(printf "%02d" "$CH")
PY=/opt/homebrew/bin/python3
MED=""; TTS=0; CARDS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --tts) TTS=1; shift;;
    --card-zh|--card-en|--card-name|--citation) CARDS+=("$1" "$2"); shift 2;;
    *) MED="$1"; shift;;
  esac
done
if [ -z "$MED" ]; then
  MED=$(ls -d "$HOME/Movies/素材/_remux/"* 2>/dev/null | head -1)
  echo "  未指定素材目录，自动用: $MED"
fi
[ -d "$MED" ] || { echo "  素材目录不存在: $MED"; exit 1; }
echo "=============================================="
echo "  第 $CH 章   素材: $MED"
echo "=============================================="

if [ "$TTS" = "1" ]; then
  echo; echo ">>> 合成配音（需要 VoiceStudio 在 127.0.0.1:3900 运行）"
  $PY synth_sentences.py --chapter "$CH"
fi

$PY prep_shots.py --chapter "$CH" || true
$PY run_chapter.py --chapter "$CH" --replace --media-dir "$MED" "${CARDS[@]}"
echo
echo "  工程名: $(cat out/last_project.txt 2>/dev/null)"
