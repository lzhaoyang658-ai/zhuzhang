#!/usr/bin/env bash
set -euo pipefail

target_dir="backend/data/sample_quotes/public"
mkdir -p "$target_dir"

curl -L --fail --silent --show-error \
  "https://www.yueyang.gov.cn/web/uploadfiles/202505/2025050911571841141.pdf" \
  -o "$target_dir/yueyang-home-decoration-contract-template.pdf"

curl -L --fail --silent --show-error -A "Mozilla/5.0" \
  -e "https://amr.qingdao.gov.cn/zwgk/tzgg/" \
  "https://amr.qingdao.gov.cn/zwgk/tzgg/202312/P020231116616186308193.pdf" \
  -o "$target_dir/qingdao-home-decoration-contract-template.pdf"

curl -L --fail --silent --show-error \
  "https://www.lg.gov.cn/attachment/1/1693/1693117/12683863.pdf" \
  -o "$target_dir/longgang-office-renovation-boq.pdf"

expected_file="samples/quote_corpus/public-sources.json"
python3 - "$expected_file" "$target_dir" <<'PY'
import hashlib
import json
import pathlib
import sys

manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
target = pathlib.Path(sys.argv[2])
for source in manifest["sources"]:
    path = target / f'{source["id"]}.pdf'
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != source["sha256"]:
        raise SystemExit(f"摘要不匹配：{path.name}\nexpected={source['sha256']}\nactual={actual}")
    print(f"verified {path.name} ({source['pages']} pages)")
PY
