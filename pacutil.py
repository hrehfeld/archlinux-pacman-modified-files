#! /usr/bin/env python
from subprocess import check_output as sp_output, check_call as sp_call, DEVNULL

import json
from pathlib import Path

from collections import OrderedDict as odict

import shutil
import os
import hashlib
import tempfile
import sys

def mkdir_p(p):
    return p.mkdir(exist_ok=True, parents=True)
    
def check_output(cmd, *args, **kwargs):
    #print(' '.join(cmd))
    return sp_output(cmd, *args, **kwargs)

def check_call(cmd, *args, **kwargs):
    #print(' '.join(cmd))
    return sp_call(cmd, *args, **kwargs)

def file_hash(filename):
    h = hashlib.sha256()
    BUF_SIZE = 128*1024
    with open(filename, 'rb', buffering=0) as f:
        for b in iter(lambda : f.read(BUF_SIZE), b''):
            h.update(b)
    return h.hexdigest()


checked_paths = [Path(a) for a in sys.argv]

BASE_DIR = Path(__file__).parent

IGNORE_FILE = BASE_DIR / '.ignore'
ignored_paths = []
if IGNORE_FILE.exists():
    with IGNORE_FILE.open('r') as f:
        ignored_paths = f.read().split('\n')
    ignored_paths = list(filter(lambda p: len(p), ignored_paths))

TMP_PATH = Path(tempfile.mkdtemp(prefix='pacutil'))
CHROOT_PATH = TMP_PATH / 'chroot'

pacman_base = TMP_PATH / 'pacman'

PACMAN_DB_PATH = TMP_PATH / 'tmp-pacman'

STATE_PATH = BASE_DIR / 'state'/ 'db.json'

PACMAN_FILE_LIST_CMD = ['pacman', '-Qlq']
MODIFIED = 'MODIFIED\t'
UNMODIFIED = 'UNMODIFIED\t'
PACMAN_CFG_FILE_LIST_CMD = ['pacman', '-Qii']

PACMAN_LIST_INSTALLED_PKGS = ['pacman', '-Qn']


PACSTRAP_INSTALL_PKG = ['/usr/bin/pacstrap', '-c', '-G', '-M', '-d'] #+dir + pkgs

def sudo_chmod(path, flags):
    check_call(['sudo', 'chmod', flags, '-R', path])

def is_system_file(p):
    s = str(p)
    return s.startswith('proc') or s.startswith('sys')

def list_files(chroot_path):
    pkg_files = chroot_path.glob('**/*')
    pkg_files = [p.relative_to(chroot_path) for p in pkg_files]
    pkg_files = filter(lambda p: not is_system_file(p), pkg_files)
    pkg_files = filter(lambda p: (chroot_path / p).is_file(), pkg_files)
    return pkg_files


def install_pkg(chroot_path, pkg, path, job):
    #extract pkg
    mkdir_p(chroot_path)
    d = chroot_path / 'etc'
    mkdir_p(d)
    d = d / 'pacman.d'
    mkdir_p(d)
    pacstrap_cmd = ['sudo'] + PACSTRAP_INSTALL_PKG + [str(chroot_path), pkg]
    check_call(['env', 'PATH=%s' % path] + pacstrap_cmd, stdout=DEVNULL)

    d = str(chroot_path.absolute())
    sudo_chmod(d, 'ugo=rwx')
    
    r = job(chroot_path)

    shutil.rmtree(d)
    return r


state = odict()
if STATE_PATH.exists():
    with STATE_PATH.open('r') as f:
        state = json.load(f, object_pairs_hook=odict)

def save_state(state):
    state_str = json.dumps(state)
    mkdir_p(STATE_PATH.parent)
    with STATE_PATH.open('w') as f:
        f.write(state_str)
    
        
pkgs = check_output(PACMAN_LIST_INSTALLED_PKGS, universal_newlines=True).split('\n')
pkgs = filter(lambda s: s != '', pkgs)
pkgs = [p.split() for p in pkgs]

#prepare pacman db
if PACMAN_DB_PATH.exists():
    shutil.rmtree(str(PACMAN_DB_PATH))
mkdir_p(PACMAN_DB_PATH)
#check_call('sudo pacman -Sy -b '.split() + [str(PACMAN_DB_PATH)])
(PACMAN_DB_PATH / 'sync').symlink_to('/var/lib/pacman/sync')

#get list of chroot files
noop_pacman = Path(pacman_base)
noop_pacman.write_text('#!/usr/bin/env sh\n')
check_call(['chmod', '+x', str(noop_pacman)], stdout=DEVNULL)
path = str(noop_pacman.parent.absolute()) + ':' + os.getenv('PATH')
chroot_files = install_pkg(CHROOT_PATH / 'DUMMY', 'DUMMY', path, list_files)


#patch pacman call so that it doesn't sync db /every/ time
nosync_pacman = Path(pacman_base)
cmd = "/usr/bin/pacman ${@/'-Sy'/-S} --dbpath %s -dd" % (str(PACMAN_DB_PATH))
nosync_pacman.write_text('''#!/usr/bin/env sh
echo "%s"
%s
''' % (cmd, cmd))
check_call(['chmod', '+x', str(nosync_pacman)], stdout=DEVNULL)
path = str(nosync_pacman.parent.absolute()) + ':' + os.getenv('PATH')

for i, (pkg, version) in enumerate(pkgs):
    #print('------------------(%s/%s): %s' % (i+1, len(pkgs), pkg))

    if pkg in state:
        if version in state[pkg]:
            continue
    chroot_path = CHROOT_PATH / pkg

    def job(_):
        #print('Getting package files...')
        pkg_files = list_files(chroot_path)
        pkg_files = filter(lambda p: p not in chroot_files, pkg_files)
        pkg_files = list(pkg_files)
        #print('\n'.join(list(map(str, (pkg_files)))))

        #print(pkg_files[0], file_hash(str(CHROOT_PATH / pkg_files[0])))
        hashes = [file_hash(str(chroot_path / p)) for p in pkg_files]
        #print(hashes)

        r = list(zip(map(str, [ Path('/') / p for p in pkg_files]), hashes))
        return r

    pkg_files = install_pkg(chroot_path, pkg, path, job)
    pkg_files = odict(pkg_files)
    #print('\n'.join(list(map(str, (pkg_files)))))

    state.setdefault(pkg, odict())
    state[pkg][version] = pkg_files


    save_state(state)
    

def search_filepath(p):
    for pkg, version in pkgs:
        pfiles = state[pkg][version]
        if p in pfiles:
            return pfiles[p]
    return None
    

owned_files = check_output(PACMAN_FILE_LIST_CMD, universal_newlines=True).split()

config_files = check_output(PACMAN_CFG_FILE_LIST_CMD, universal_newlines=True).split('\n')
modified_config_files = [l[len(MODIFIED):] for l in config_files if l.startswith(MODIFIED)]
unmodified_config_files = [l[len(UNMODIFIED):] for l in config_files if l.startswith(UNMODIFIED)]
config_files = modified_config_files + unmodified_config_files

orphan_files = []
modified_files = []
uncheckable_files = []
for d in checked_paths:
    files = d.glob('**/*')
    for p in files:
        if not p.is_file():
            continue
        s = str(p)
        skip = False
        for ip in ignored_paths:
            if s.startswith(ip):
                skip = True
        if skip:
            continue

        if s in config_files:
            if s not in modified_config_files:
                continue
        else:
            phash = search_filepath(s)
            if phash is not None:
                hash = file_hash(p)
                if hash == phash:
                    continue
            else:
                if (str(p) in owned_files):
                    uncheckable_files.append(p)
                    continue
                orphan_files.append(p)
                continue

        modified_files.append(p)


def print_paths(l):
    print('\n'.join(map(str, l)))
    



print_paths(modified_files)
print_paths(orphan_files)
print_paths(uncheckable_files)

        
            

