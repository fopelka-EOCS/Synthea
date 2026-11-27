#!/usr/bin/env bash
# Build the full 100-PCP clinic in batches of 10 PCPs, from the repo root:
#   JAVA=/path/to/java bash sandboxes/pcp-clinic-100/build_clinic.sh
# Per batch, all heavy work happens on the local disk:
#   1. generate the batch's patients into ./output/pcp-clinic-100-raw-NN
#      (reused if a complete raw batch is already there)
#   2. trim and assign into ./output/pcp-clinic-100-stage/batch-NN; delete raw
#   3. copy the staged batch to OUT with robocopy, one file at a time with
#      retries (an external exFAT/USB
#      drive failed under many parallel writes, 2026-11-26)
#   4. verify on OUT that every file is present at its full size, mark VERIFIED
#   5. delete the staged copy, then start the next batch
# Peak local disk is about one raw batch plus one staged batch (~80 GB). Safe to
# rerun: a batch with OUT/batch-NN/VERIFIED is skipped. Any failure stops the
# build with the local copy intact.
set -euo pipefail

JAVA="${JAVA:-java}"
PY="${PY:-python}"
OUT="${OUT:-E:/Synthea-Sandboxes/pcp-clinic-100}"
STAGE=output/pcp-clinic-100-stage
JAR=build/libs/synthea-with-dependencies.jar
CFG=sandboxes/pcp-clinic-100/synthea.properties
SCRIPT=sandboxes/pcp-clinic-100/assign_panels.py
REF_DATE=2026-11-26
SEED=20261126

mkdir -p "$OUT" "$STAGE"
[ -f "$JAR" ] || ./gradlew uberJar

listing() { (cd "$1" && find . -type f -printf '%P %s\n' | LC_ALL=C sort); }

# "PCPs 1-10: 19596 patients" -> "1-10 19596"
"$PY" -I "$SCRIPT" --plan 10 --seed "$SEED" | sed -n 's/^PCPs \([0-9-]*\): \([0-9]*\) patients$/\1 \2/p' |
while read -r range count; do
  b=$(printf "%02d" $(( (${range%-*} + 9) / 10 )))
  raw="output/pcp-clinic-100-raw-$b"
  stage="$STAGE/batch-$b"
  dest="$OUT/batch-$b"
  if [ -f "$dest/VERIFIED" ]; then echo "batch $b: verified on $OUT, skipping"; continue; fi
  echo "batch $b: PCPs $range, $count patients, started $(date '+%H:%M')"

  if grep -qs "\"patients_assigned\": $count," "$stage/summary.json"; then
    echo "batch $b: reusing complete trimmed batch on local disk"
  else
    have=$(ls "$raw/fhir" 2>/dev/null | grep -v -c -E '^(hospital|practitioner|payer)Information' || true)
    if [ "$have" = "$count" ]; then
      echo "batch $b: reusing complete raw batch"
    else
      rm -rf "$raw"
      "$JAVA" -jar "$JAR" -s $(( SEED + 10#$b )) -cs "$SEED" -r "${REF_DATE//-/}" \
        -p "$count" -a 18-100 -c "$CFG" "--exporter.baseDirectory=./$raw/" \
        > "$STAGE/batch-$b-synthea.log" 2>&1
    fi
    rm -rf "$stage"
    "$PY" -I "$SCRIPT" --input "$raw/fhir" --out "$stage" --pcp-range "$range" \
      --reference-date "$REF_DATE" --seed "$SEED" > "$STAGE/batch-$b-assign.log"
    grep -q "\"patients_assigned\": $count," "$stage/summary.json" ||
      { echo "batch $b: assigned count != $count, stopping"; exit 1; }
    cp "$STAGE/batch-$b-synthea.log" "$STAGE/batch-$b-assign.log" "$stage/" 2>/dev/null || true
    rm -rf "$raw"
    echo "batch $b: trimmed $(date '+%H:%M'), $(du -sh "$stage" | cut -f1)"
  fi

  # robocopy retries files that fail while the USB drive briefly drops out, and
  # skips files already copied intact, so a rerun resumes a partial copy.
  # Exit codes 0-7 mean success; 8 and above mean some files could not be copied.
  echo "batch $b: copying to $OUT $(date '+%H:%M')"
  rc=0
  MSYS2_ARG_CONV_EXCL='*' robocopy "$(cygpath -w "$stage")" "$(cygpath -w "$dest")" \
    /E /R:5 /W:5 /NP /NFL /NDL /NJH > "$STAGE/batch-$b-robocopy.log" 2>&1 || rc=$?
  if [ "$rc" -ge 8 ]; then
    echo "batch $b: robocopy exit $rc, stopping (local copy kept; see $STAGE/batch-$b-robocopy.log)"; exit 1
  fi
  if [ "$(listing "$stage")" != "$(listing "$dest")" ]; then
    echo "batch $b: copy on $OUT does not match the local copy, stopping (local copy kept)"; exit 1
  fi
  date '+%Y-%m-%d %H:%M' > "$dest/VERIFIED"
  rm -rf "$stage" "$STAGE/batch-$b-synthea.log" "$STAGE/batch-$b-assign.log" "$STAGE/batch-$b-robocopy.log"
  echo "batch $b: verified on $OUT $(date '+%H:%M'), $(ls "$dest/patients" | wc -l) patient files"
done
echo "clinic complete in $OUT: $(ls -d "$OUT"/batch-*/VERIFIED | wc -l) verified batches"
