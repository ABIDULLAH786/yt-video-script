#!/usr/bin/env bash
# run_ai_triage.sh - the two commands for AI triage, with the right interpreter baked in.
#
# WHY THIS WRAPPER EXISTS
#   This project has three venvs and only two of them carry opencv + ultralytics:
#       .venv                      cv2 4.10.0   ultralytics 8.4.60   <- used here
#       edge-motion-pipeline/.venv cv2 4.13.0   ultralytics 8.4.138
#       drone-monitor/.venv        no cv2       <- running ai_triage.py under this fails
#   Whichever venv happens to be active, this picks the root one explicitly.
#
# USAGE
#   ./run_ai_triage.sh triage          sort + annotate the frames into AI Filtered v2/
#   ./run_ai_triage.sh check           DRY RUN - show what syncing would delete
#   ./run_ai_triage.sh delete          actually delete from images/ what you removed from annotated/
#
#   IMGSZ=1280 ./run_ai_triage.sh triage    run at 1280 -> "AI Filtered v2 imgsz1280/"
#   IMGSZ=1280 ./run_ai_triage.sh check     check/delete against that same folder
#   SRC="filtered/f6_heavy_lift (done)" ./run_ai_triage.sh triage    another family
#
# WORKFLOW
#   1. ./run_ai_triage.sh triage
#   2. open  filtered/AI Filtered v2/<band>/annotated/  and delete the frames you do not want
#      (filenames lead with the confidence, so the listing is already in priority order)
#   3. ./run_ai_triage.sh check        read the plan
#   4. ./run_ai_triage.sh delete       apply it
#
#   Your original collected frames under SRC are never touched by any of this.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PY="$ROOT/.venv/bin/python"

SRC="${SRC:-$HERE/filtered/f4_open_frame_fpv (still in process)}"
MODEL="${MODEL:-$ROOT/model-testing/models/v16-best.pt}"

# IMGSZ picks the inference resolution AND the output folder, so runs at different sizes
# never overwrite each other. 640 is what v16 was trained at; 1280 resolves smaller drones
# but is ~4x slower and tends to add marginal detections as well as real ones.
IMGSZ="${IMGSZ:-640}"

# BATCH scales down as IMGSZ goes up. This box is a GTX 1650 with 4 GB: batch 16 at 1280
# dies with "CUDA out of memory. Tried to allocate 1.17 GiB". 1280 is 4x the pixels of 640,
# so quarter the batch. Override with BATCH= if you run this on a bigger card.
if [ "$IMGSZ" -ge 1280 ]; then   BATCH="${BATCH:-4}"
elif [ "$IMGSZ" -ge 960 ]; then  BATCH="${BATCH:-8}"
else                             BATCH="${BATCH:-16}"
fi

if [ "$IMGSZ" = "640" ]; then
  OUT="${OUT:-$HERE/filtered/AI Filtered v2}"
else
  OUT="${OUT:-$HERE/filtered/AI Filtered v2 imgsz$IMGSZ}"
fi

[ -x "$PY" ] || { echo "no interpreter at $PY"; exit 1; }
"$PY" -c "import cv2, ultralytics" 2>/dev/null || {
  echo "$PY is missing cv2 or ultralytics"; exit 1; }

case "${1:-}" in
  triage)
    [ -d "$SRC" ] || { echo "source not found: $SRC"; exit 1; }
    echo "src   : $SRC"
    echo "out   : $OUT"
    echo "model : $MODEL"
    echo "imgsz : $IMGSZ   batch: $BATCH"
    echo
    "$PY" "$HERE/ai_triage.py" --src "$SRC" --out "$OUT" --model "$MODEL" --imgsz "$IMGSZ" --batch "$BATCH" "${@:2}"
    ;;
  check)
    "$PY" "$HERE/ai_triage_sync.py" --out "$OUT" "${@:2}"
    ;;
  delete)
    "$PY" "$HERE/ai_triage_sync.py" --out "$OUT" --apply "${@:2}"
    ;;
  *)
    sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
