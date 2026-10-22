#!/usr/bin/env bash
# run_family_640.sh - AI-triage one family at 640 only. One pass, nothing else.
#
#   ./run_family_640.sh f1_folding_quad
#   ./run_family_640.sh f2_micro_selfie
#   ./run_family_640.sh f3_ducted_fpv
#   ./run_family_640.sh f5_enterprise_quad
#   ./run_family_640.sh f6_heavy_lift_v2
#   ./run_family_640.sh f7_fixed_wing_vtol
#
# Reads   raw/models/<family>/
# Writes  filtered/AI Filtered/<family>/
#
# 640 is the resolution v16 was trained at. Output is four confidence bands:
#   1_fires_high_conf70plus/   spot-check; keep a subsample
#   2_fires_mid_conf50to70/    review
#   3_fires_low_conf25to50/    HUNT FOR MISSED DRONES - not a negative harvest, see below
#   4_silent_no_detection/     hunt for missed drones; delete the rest
#
# THE CONFIDENCE RULE
#   Keep a false positive as a negative only if BOTH hold:
#     1. it fires at or above 0.50 - at or above ANY threshold we might run at
#     2. the scene is something a mast-mounted PTZ could really frame
#   Band 3 is 0.25-0.50, so it fails test 1 by definition. For every band-3 frame ask one
#   question: is there actually a drone in it? No -> delete. Yes -> keep as a positive.
#   Training on 0.25-level false positives teaches suppression at the bottom of the
#   confidence range, which is where faint distant drones live. v16 gained +12.6 points at
#   0-8px over v15; that is the thing at risk.
#
# Review the annotated/ folders, delete what you reject, then:
#   OUT="filtered/AI Filtered/<family>" ./run_ai_triage.sh check    # dry run
#   OUT="filtered/AI Filtered/<family>" ./run_ai_triage.sh delete   # apply
#
# Original frames under raw/models/ are never touched.
# TRIAL=30 ./run_family_640.sh <family>   runs a quick smoke test first.
set -euo pipefail

FAMILY="${1:-}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PY="$ROOT/.venv/bin/python"
MODEL="${MODEL:-$ROOT/model-testing/models/v16-best.pt}"

if [ -z "$FAMILY" ]; then
  echo "usage: ./run_family_640.sh <family>"
  echo
  echo "families in raw/models/:"
  for d in "$HERE"/raw/models/*/; do
    n=$(find "$d" -type f \( -iname '*.jpg' -o -iname '*.png' \) 2>/dev/null | wc -l)
    mark=""
    [ -d "$HERE/filtered/AI Filtered/$(basename "$d")" ] && mark="   <- already triaged"
    printf "   %-24s %7d frames%s\n" "$(basename "$d")" "$n" "$mark"
  done
  exit 1
fi

SRC="$HERE/raw/models/$FAMILY"
OUT="$HERE/filtered/AI Filtered/$FAMILY"
[ -d "$SRC" ] || { echo "no such family: $SRC"; exit 1; }
[ -x "$PY" ] || { echo "no interpreter at $PY"; exit 1; }
"$PY" -c "import cv2, ultralytics" 2>/dev/null || { echo "$PY missing cv2/ultralytics"; exit 1; }

LIMIT=""
[ -n "${TRIAL:-}" ] && LIMIT="--limit $TRIAL"

N=$(find "$SRC" -type f \( -iname '*.jpg' -o -iname '*.png' \) | wc -l)
echo "family : $FAMILY   ($N frames)"
echo "out    : $OUT"
echo "imgsz  : 640   batch: 16"
echo

"$PY" "$HERE/ai_triage.py" --src "$SRC" --out "$OUT" --model "$MODEL" \
      --imgsz 640 --batch 16 $LIMIT
