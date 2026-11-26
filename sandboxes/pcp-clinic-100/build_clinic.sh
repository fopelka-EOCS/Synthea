#!/usr/bin/env bash
# Build the full 100-PCP clinic in batches of 10 PCPs, from the repo root:
#   JAVA=/path/to/java bash sandboxes/pcp-clinic-100/build_clinic.sh
# Per batch: generate exactly that batch's patients, trim and assign them,
# delete the raw Synthea output, then compress the batch. Peak disk is about one
# raw batch (~55 GB) plus the finished batches. Safe to rerun: a batch whose
# archive already exists is skipped.
set -euo pipefail

JAVA="${JAVA:-java}"
PY="${PY:-python}"
JAR=build/libs/synthea-with-dependencies.jar
CFG=sandboxes/pcp-clinic-100/synthea.properties
SCRIPT=sandboxes/pcp-clinic-100/assign_panels.py
REF_DATE=2026-11-26
SEED=20261126
OUT=output/pcp-clinic-100-full

mkdir -p "$OUT"
[ -f "$JAR" ] || ./gradlew uberJar

# "PCPs 1-10: 19596 patients" -> "1-10 19596"
"$PY" -I "$SCRIPT" --plan 10 --seed "$SEED" | sed -n 's/^PCPs \([0-9-]*\): \([0-9]*\) patients$/\1 \2/p' |
while read -r range count; do
  b=$(printf "%02d" $(( (${range%-*} + 9) / 10 )))
  archive="$OUT/batch-$b.tar.gz"
  if [ -f "$archive" ]; then echo "batch $b: done, skipping"; continue; fi
  raw="output/pcp-clinic-100-raw-$b"
  dest="$OUT/batch-$b"
  rm -rf "$raw" "$dest"
  echo "batch $b: PCPs $range, $count patients, $(date '+%H:%M')"
  "$JAVA" -jar "$JAR" -s $(( SEED + 10#$b )) -cs "$SEED" -r "${REF_DATE//-/}" \
    -p "$count" -a 18-100 -c "$CFG" "--exporter.baseDirectory=./$raw/" \
    > "$OUT/batch-$b-synthea.log" 2>&1
  "$PY" -I "$SCRIPT" --input "$raw/fhir" --out "$dest" --pcp-range "$range" \
    --reference-date "$REF_DATE" --seed "$SEED" > "$OUT/batch-$b-assign.log"
  grep -q "\"patients_assigned\": $count," "$dest/summary.json" ||
    { echo "batch $b: assigned count != $count, stopping"; exit 1; }
  rm -rf "$raw"
  tar -czf "$archive.part" -C "$OUT" "batch-$b" && mv "$archive.part" "$archive"
  echo "batch $b: finished $(date '+%H:%M'), $(du -sh "$dest" | cut -f1) trimmed, $(du -sh "$archive" | cut -f1) archived"
done
