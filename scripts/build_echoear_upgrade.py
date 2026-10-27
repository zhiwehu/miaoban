#!/usr/bin/env python3
"""Isolated existing-device build. Does not open a serial port or flash any device."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--work',required=True);p.add_argument('--version',default='2.0.5-voicehub.1')
    p.add_argument('--component-cache',type=Path)
    args=p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,31}',args.version):raise ValueError('Invalid application version')
    root=Path(__file__).resolve().parents[1];work=Path(args.work).resolve()
    if work.exists():raise FileExistsError('Use a new isolated build directory')
    sdk=Path(os.environ['IDF_PATH']).resolve()
    version=(sdk/'tools/cmake/version.cmake').read_text()
    if any(f'set(IDF_VERSION_{k} {v})' not in version for k,v in [('MAJOR',5),('MINOR',4),('PATCH',0)]):
        raise ValueError('This release profile requires ESP-IDF 5.4.0')
    work.mkdir(parents=True)
    paths=subprocess.check_output(['git','ls-files','firmware/head','firmware/shared'],cwd=root,text=True).splitlines()
    sources={}
    for name in paths:
        source=root/name;dest=work/name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,dest);sources[name]=hashlib.sha256(source.read_bytes()).hexdigest()
    project=work/'firmware/head'
    cmake=project/'CMakeLists.txt';text=cmake.read_text()
    if 'set(PROJECT_VER "2.0.5")' not in text:raise ValueError('Unrecognized source version declaration')
    cmake.write_text(text.replace('set(PROJECT_VER "2.0.5")',f'set(PROJECT_VER "{args.version}")',1))
    config=project/'sdkconfig.defaults';text=config.read_text()
    text=text.replace('# CONFIG_FLASH_NONE_ASSETS is not set','CONFIG_FLASH_NONE_ASSETS=y')
    text=text.replace('CONFIG_FLASH_DEFAULT_ASSETS=y','# CONFIG_FLASH_DEFAULT_ASSETS is not set')
    if 'CONFIG_OTA_URL=""' not in text:raise ValueError('OTA URL must not embed an installation-specific address')
    config.write_text(text)
    if args.component_cache:
        shutil.copytree(args.component_cache,project/'managed_components',ignore=shutil.ignore_patterns('__pycache__'))
    provenance=dict(source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
        sdk_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=sdk,text=True).strip(),
        sdk_modified_files=subprocess.check_output(['git','diff','--name-only'],cwd=sdk,text=True).splitlines(),
        version=args.version,sources=sources,profile='existing-echoear-application-only')
    (work/'build-provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    env=os.environ.copy();env['IDF_COMPONENT_MANAGER']='1'
    with (work/'build.log').open('w') as log:
        subprocess.run([sys.executable,str(project/'scripts/prepare_sounds.py')],env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
        subprocess.run([sys.executable,str(sdk/'tools/idf.py'),'-C',str(project),'-B',str(work/'build'),'app'],
            env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    licenses=work/'licenses';licenses.mkdir()
    for group,folder in [('esp-idf',sdk)]+[(p.name,p) for p in (project/'managed_components').iterdir() if p.is_dir()]:
        for file in folder.iterdir():
            if file.is_file() and file.name.upper().startswith(('LICENSE','COPYING','NOTICE')):
                target=licenses/group/file.name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,target)
    print('Application candidate: '+str(work/'build/xiaozhi.bin'))
    print('Hardware validation is still required before enabling end-user installation.')


if __name__=='__main__':main()
