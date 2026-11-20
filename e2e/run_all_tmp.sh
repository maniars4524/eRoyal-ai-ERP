#!/bin/bash
cd /e/eRoyal-ai-ERP/e2e
: > ../logs/e2e_all_1119.log
for f in *.test.js; do
  r=$(timeout 280 node "$f" 2>&1 | grep -E "FAIL|PASSED|FAILED" | tr '\n' ' ' | cut -c1-260)
  echo "== $f: ${r:-NO RESULT (timeout or crash)}" >> ../logs/e2e_all_1119.log
done
echo "ALL DONE" >> ../logs/e2e_all_1119.log
