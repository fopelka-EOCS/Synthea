#!/usr/bin/env bash
# Build the full 100-PCP clinic in batches of 10 PCPs, from the repo root:
#   JAVA=/path/to/java bash sandboxes/pcp-clinic-100/build_clinic.sh
# Per batch: generate exactly that batch's patients into a temporary raw folder
# under ./output/, then trim and assign them into OUT, then delete the raw
# folder. Peak local disk is about one raw batch (~55 GB); finished batches
# (~26 GB each) accumulate in OUT. Safe to rerun: a batch whose summary shows the
# full patient count is skipped.
set -euo pipefail

JAVA="${JAVA:-java}"
PY="${PY:-python}"
OUT="${OUT:-E:/Synthea-Sandboxes/pcp-clinic-100}"
JAR=build/libs/synthea-with-dependencies.jar
CFG=sandboxes/pcp-clinic-100/synthea.properties
SCRIPT=sandboxes/pcp-clinic-100/assign_panels.py
REF_DATE=2026-11-26
SEED=20261126

mkdir -p "$OUT"
[ -f "$JAR" ] || ./gradlew uberJar

# "PCPs 1-10: 19596 patients" -> "1-10 19596"
"$PY" -I "$SCRIPT" --plan 10 --seed "$SEED" | sed -n 's/^PCPs \([0-9-]*\): \([0-9]*\) patients$/\1 \2/p' |
while read -r range count; do
  b=$(printf "%02d" $(( (${range%-*} + 9) / 10 )))
  raw="output/pcp-clinic-100-raw-$b"
  dest="$OUT/batch-$b"
  if grep -qs "\"patients_assigned\": $count," "$dest/summary.json"; then
    echo "batch $b: done, skipping"; continue
  fi
  rm -rf "$raw" "$dest"
  echo "batch $b: PCPs $range, $count patients, started $(date '+%H:%M')"
  "$JAVA" -jar "$JAR" -s $(( SEED + 10#$b )) -cs "$SEED" -r "${REF_DATE//-/}" \
    -p "$count" -a 18-100 -c "$CFG" "--exporter.baseDirectory=./$raw/" \
    > "$OUT/batch-$b-synthea.log" 2>&1
  "$PY" -I "$SCRIPT" --input "$raw/fhir" --out "$dest" --pcp-range "$range" \
    --reference-date "$REF_DATE" --seed "$SEED" > "$OUT/batch-$b-assign.log"
  grep -q "\"patients_assigned\": $count," "$dest/summary.json" ||
    { echo "batch $b: assigned count != $count, stopping"; exit 1; }
  rm -rf "$raw"
  echo "batch $b: finished $(date '+%H:%M'), $(du -sh "$dest" | cut -f1)"
done
echo "clinic complete: $(du -sh "$OUT" | cut -f1) in $OUT"
