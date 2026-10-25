#!/usr/bin/env bash
# Full pipeline: render shots -> soundtrack -> final TikTok-ready mp4.
# usage: ./build.sh CLIP.mp4 MUSIC.wav WORK_DIR OUT.mp4
set -euo pipefail
clip=$1 music=$2 work=$3 out=$4
here=$(cd "$(dirname "$0")" && pwd)

python3 -I "$here/render.py" "$clip" "$music" "$work"
python3 -I "$here/audio.py" "$music" "$work/timeline.json" "$work/mix.wav"

ls "$work"/shot_*.mp4 | sort | sed "s/^/file '/; s/$/'/" > "$work/concat.txt"
ffmpeg -v error -y -f concat -safe 0 -i "$work/concat.txt" -i "$work/mix.wav" \
  -map 0:v -map 1:a -c:v libx264 -preset slow -crf 17 -profile:v high -pix_fmt yuv420p \
  -r 60 -g 120 -movflags +faststart \
  -af "alimiter=limit=0.89:level=false" -c:a aac -b:a 320k -ar 44100 -shortest "$out"
ffprobe -v error -show_entries format=duration,size -of default=nw=1 "$out"
