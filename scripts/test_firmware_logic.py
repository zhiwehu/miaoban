"""Build and run pure controller tests; does not connect to any device."""
from pathlib import Path
import shutil,subprocess,tempfile
root=Path(__file__).resolve().parents[1]
compiler=shutil.which('c++')
if not compiler:raise SystemExit('Install a C++17 compiler first')
with tempfile.TemporaryDirectory(prefix='voicehub-logic-') as tmp:
 for source in sorted((root/'tests').glob('*.cc')):
  binary=Path(tmp)/source.stem
  subprocess.run([compiler,'-std=c++17','-Wall','-Wextra','-Werror',str(source),'-o',str(binary)],check=True)
  subprocess.run([str(binary)],check=True)
  print('PASS',source.name)
