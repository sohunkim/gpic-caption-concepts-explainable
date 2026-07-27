#!/usr/bin/env bash
set -euo pipefail

repo=/root/work/gpic-caption-concepts-explainable
stage456=/mnt/ddn/prod-runs/snu14ksh/gpic_stage456/full_1m_20260716
output_dir=/mnt/ddn/prod-runs/snu14ksh/gpic_stage456/audits/attribute_conj_coverage_20260724
audit_script=/tmp/audit_attribute_conj_inventory_coverage.py

test -f "$audit_script"
test -f "$stage456/stage4/raw_mentions.jsonl"
test -f "$repo/resources/gpic_inventory/current/lexicons/attribute_synonyms.tsv"
mkdir -p "$output_dir"

cd "$repo"
PYTHONPATH="$repo/src" python3 "$audit_script" \
  --raw-mentions "$stage456/stage4/raw_mentions.jsonl" \
  --attribute-synonyms \
    "$repo/resources/gpic_inventory/current/lexicons/attribute_synonyms.tsv" \
  --output "$output_dir/conj_attribute_inventory_coverage.tsv" \
  --summary "$output_dir/conj_attribute_inventory_coverage_summary.json" \
  --progress-output "$output_dir/conj_attribute_inventory_coverage_progress.json" \
  --progress-interval-lines 250000

stat --printf='%n\t%s\n' \
  "$output_dir/conj_attribute_inventory_coverage.tsv" \
  "$output_dir/conj_attribute_inventory_coverage_summary.json" \
  "$output_dir/conj_attribute_inventory_coverage_progress.json"
