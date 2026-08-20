import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "wepenerd_testpkg"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE_NAME, package)
