import json
from pathlib import Path
log_path = sorted(Path(r'D:\vidhoor-legal-copilot\backend\tests\test_logs').glob('eval_results_*.json'))[-1]
report = json.loads(log_path.read_text(encoding='utf-8'))
for r in report['results']:
    print(f"ID: {r['id']}")
    print(f"  Query: {r['query'][:80]}")
    print(f"  Precision: {r['precision']}")
    print(f"  Recall: {r['recall']}")
    print(f"  Act match: {r['act_match']}")
    print(f"  Matched sections: {r['matched_sections']}")
    print(f"  Clarify correct: {r['clarify_correct']}")
    print()
