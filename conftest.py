import sys
import os

# Automatically add project root to python path for all tests
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

try:
    import dotenv
    dotenv.load_dotenv(os.path.join(ROOT_DIR, "backend", ".env"))
except ImportError:
    pass
