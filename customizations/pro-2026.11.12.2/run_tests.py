"""Launch with the exact Pro runtime interpreter, without modifying its venv."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parent
CORE = Path(os.environ.get('FLOCKS_TEST_CORE_SOURCE', ROOT / '.work/zw_dev')).resolve()
sys.path[:0] = [str(ROOT), str(CORE), str(ROOT / '.work/venv/lib/python3.12/site-packages')]
import pytest
raise SystemExit(pytest.main(['-q', *(sys.argv[1:] or [str(ROOT/'tests')])]))
