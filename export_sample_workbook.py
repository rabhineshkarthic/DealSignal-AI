"""Export the offline demo M&A workbook without any LLM call.

WARNING: the file this script writes (by default reports/Northstar_Atlas_MA_Model.xlsx)
contains OFFLINE DEMO qualitative content in the IC Summary conclusion, Due Diligence,
Risk Summary, and IC Memo sheets. That content is illustrative (see demo_state.py) and
must NOT be presented as the final live deal-team output. Only the deterministic model
sheets reflect real calculations. reports/*.xlsx is gitignored; regenerate as needed.

Usage: python export_sample_workbook.py [output.xlsx]
"""

from __future__ import annotations

import sys
from pathlib import Path

from demo_state import build_demo_state
from excel_export import DEFAULT_OUTPUT, export_ma_workbook


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    demo = build_demo_state()
    replace_own_default = output.exists() and output.resolve() == DEFAULT_OUTPUT.resolve()
    path = export_ma_workbook(demo.deal, demo.result, demo.memo, output,
                              overwrite=replace_own_default)
    print(f"Wrote {path}")
    print("WARNING: qualitative sheets contain OFFLINE DEMO illustrative content; do not "
          "present this file as the final live deal-team output.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
