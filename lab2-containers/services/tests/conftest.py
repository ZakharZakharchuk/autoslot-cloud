import os
import sys

# Тести запускаються з каталогу services/:  python -m pytest tests -q
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
