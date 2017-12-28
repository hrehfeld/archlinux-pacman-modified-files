#! /usr/bin/env python
from subprocess import check_output as sp_output, check_call as sp_call, DEVNULL
import subprocess

import json
from extended_pathlib import Path

from collections import OrderedDict as odict

import shutil
import os
import hashlib
import tempfile
import sys

import socket

import re

import argparse

import config


p = argparse.ArgumentParser(description='check archlinux (config) files for changes')
p.add_argument('username', help='non-root username for building pkgs, etc.')
p.add_argument('paths', nargs='+')
args = p.parse_args()


USERNAME = args.username

with Path('.pkg-blacklist').open('r') as f:
    pkg_blacklist = [p.strip() for p in f.read().split('\n')]


INTERNAL_PKG_MARKER = '__'


def temp_dir(prefix):
    path = tempfile.mkdtemp(prefix=prefix)
    return path


def is_repo(p):
    return p.is_dir() and p.name == '.hg'

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

def get_hash(s):
    h = hashlib.sha256()
    h.update(s)
    return h.hexdigest()

machine = socket.gethostname()

repo_path = Path(config.repo_path.strip()).absolute()
machine_repo_path = Path(config.machine_repo_path.strip().replace('$HOST', machine)).absolute()

checked_paths = [Path(a) for a in args.paths]

BASE_DIR = Path(__file__).parent

ORPHAN_PKGS_FILE = BASE_DIR / '.orphans'

IGNORE_FILE = BASE_DIR / '.ignore'
ignored_paths = []
if IGNORE_FILE.exists():
    with IGNORE_FILE.open('r') as f:
        ignored_paths = f.read().split('\n')
    ignored_paths = list(filter(lambda p: len(p), [p.strip() for p in ignored_paths]))

TMP_PATH = Path(tempfile.mkdtemp(prefix='pacutil'))
CHROOT_PATH = TMP_PATH / 'chroot'

pacman_base = TMP_PATH / 'pacman'

PACMAN_DB_PATH = TMP_PATH / 'tmp-pacman'

STATE_PATH = BASE_DIR / 'state'

PACMAN_FILE_LIST_CMD = ['pacman', '-Ql']
MODIFIED = 0
UNMODIFIED = 1
PACMAN_CFG_FILE_LIST_CMD = ['pacman', '-Qii']

def get_config_files():
    ls = check_output(PACMAN_CFG_FILE_LIST_CMD, universal_newlines=True).split('\n')
    name_reg = re.compile(r'Name *: (.*)')
    ver_reg = re.compile(r'Version *: (.*)')
    f_reg = re.compile(r'((?:UN)?MODIFIED)[ \t]*(.*)')
    name = None
    ver = None
    fs = []
    r = odict()
    for l in ls:
        m = name_reg.match(l)
        if m:
            if name and fs:
                r.setdefault(name, odict())
                r[name].setdefault(ver, [])
                r[name][ver] += fs
                fs = []
            name =m.group(1)
        elif name:
            m = ver_reg.match(l)
            if m:
                ver = m.group(1)
            else:
                m = f_reg.match(l)
                if m:
                    state = UNMODIFIED if m.group(1).startswith('UN') else MODIFIED
                    fs.append((state, m.group(2)))
    return r
                
            

PACSTRAP_INSTALL_PKG = ['/usr/bin/pacstrap', '-c', '-G', '-M', '-d'] #+dir + installed_pkgs

def chmod(mode, path, sudo=False):
    cmd = ['chmod', '-R', mode, str(path)]
    if sudo:
        cmd = ['sudo'] + cmd
    return check_call(cmd, stdout=DEVNULL)
    
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
    check_call(['env', 'PATH=%s' % path] + pacstrap_cmd, stdout=DEVNULL
    )

    d = str(chroot_path.absolute())
    chmod('ugo=rwx', d, sudo=True)
    
    r = job(chroot_path)

    shutil.rmtree(d)
    return r


def load_state():
    state = odict()
    if STATE_PATH.exists():
        for pkgf in STATE_PATH.glob('*.json'):
            pkg = pkgf.stem
            with pkgf.open('r') as f:
                state[pkg] = json.load(f, object_pairs_hook=odict)
    return state


def save_state(state):
    mkdir_p(STATE_PATH)
    for pkg in state:
        state_str = json.dumps(state[pkg])
        pkgf = STATE_PATH / (pkg + '.json')
        with pkgf.open('w') as f:
            f.write(state_str)
    

def parse_installed_packages(s):
    pkgs = s.split('\n')
    pkgs = filter(lambda s: s != '', pkgs)
    pkgs = odict([p.split(' ') for p in pkgs])
    return pkgs
        
installed_pkgs = parse_installed_packages(check_output(['pacman', '-Q'], universal_newlines=True))
installed_native_pkgs = parse_installed_packages(check_output(['pacman', '-Qn'], universal_newlines=True))

def get_owned_files(installed_pkgs):
    fs = check_output(PACMAN_FILE_LIST_CMD, universal_newlines=True).split('\n')
    r = odict()
    for line in fs:
        if not line:
            continue
        l = line.split(' ', 1)
        if len(l) != 2:
            raise Exception(line)
        pkg, f = l
        ver = installed_pkgs[pkg]
        r.setdefault(pkg, odict())
        r[pkg].setdefault(ver, [])
        r[pkg][ver].append(f)
    return r


#prepare pacman db
if PACMAN_DB_PATH.exists():
    shutil.rmtree(str(PACMAN_DB_PATH))
mkdir_p(PACMAN_DB_PATH)
#check_call('sudo pacman -Sy -b '.split() + [str(PACMAN_DB_PATH)])
(PACMAN_DB_PATH / 'sync').symlink_to('/var/lib/pacman/sync')

#get list of chroot pkg_owned_files
noop_pacman = Path(pacman_base)
noop_pacman.write_text('#!/usr/bin/env sh\necho $@')
chmod('+x', noop_pacman)
path = str(noop_pacman.parent.absolute()) + ':' + os.getenv('PATH')
chroot_files = install_pkg(CHROOT_PATH / 'DUMMY', 'DUMMY', path, list_files)


#patch pacman call so that it doesn't sync db /every/ time
def nosync_pacman():
    nosync_pacman = Path(pacman_base)
    cmd = "env PATH=%s /usr/bin/pacman ${@/'-Sy'/-S} --dbpath %s -dd --nodeps" % (os.getenv('PATH'), str(PACMAN_DB_PATH))
    nosync_pacman.write_text('''#!/usr/bin/env sh
    echo "%s"
    %s
    ''' % (cmd, cmd))
    chmod('+x', nosync_pacman)
    path = str(nosync_pacman.parent.absolute()) + ':' + os.getenv('PATH')
    return path


#patch pacman call so that it doesn't sync db /every/ time
def aur_pacman(pkg, chroot, pkgbuild_path, version_path):
    os.rmdir(pkgbuild_path)
    os.rmdir(version_path)

    aur_pacman = Path(tempfile.mkdtemp(prefix='pacman')) / Path(pacman_base)
    cmd = r"""#!/usr/bin/env sh
    set -x
    PATH="%s"
    USERNAME="%s"
    PACMANDB="%s"
    TEMPD="%s"
    PKG="%s"
    CHROOT="%s"
    VERSION_PATH="%s"
    sudo -u $USERNAME -H git clone https://aur.archlinux.org/${PKG}.git $TEMPD
    cd $TEMPD
    sudo -u $USERNAME -H makepkg -sr --asdeps --noconfirm
    echo env PATH=$PATH /usr/bin/pacman -r $CHROOT -U --noconfirm --dbpath $PACMANDB -dd --nodeps ${TEMPD}/${PKG}*.pkg.tar.xz 
    env PATH=$PATH /usr/bin/pacman -r $CHROOT -U --noconfirm --dbpath $PACMANDB -dd --nodeps ${TEMPD}/${PKG}*.pkg.tar.xz
    VERSION=$(env PATH=$PATH /usr/bin/pacman -Q --dbpath $PACMANDB $PKG)
    sudo -u $USERNAME -H sh -c "echo \"$VERSION\" > $VERSION_PATH"
    cd ..
    rm -rf $TEMPD
    """ % (os.getenv('PATH'), USERNAME, str(PACMAN_DB_PATH), pkgbuild_path, pkg, chroot, version_path)
    aur_pacman.write_text(cmd)
    chmod('+x', aur_pacman)
    aur_path = str(aur_pacman.parent.absolute()) + ':' + os.getenv('PATH')
    return aur_path

state = load_state()
config_files = get_config_files()
for i, (pkg, version) in enumerate(installed_pkgs.items()):
    if pkg in pkg_blacklist:
        continue

    

    def get_files(version):
        msg = 'not found'
        if pkg in state:
            msg = 'version %s not found, only %s' % (version, ', '.join(state[pkg].keys()))
        print('------------------(%s/%s): %s %s' % (i+1, len(installed_pkgs), pkg, msg))

        chroot_path = CHROOT_PATH / pkg
        is_aur = pkg not in installed_native_pkgs
        if is_aur:
            print('AUR package')
            pkgbuild_path = temp_dir('aurbuild-%s' % pkg)
            version_path = temp_dir('version-%s' % pkg)
            _path = aur_pacman(pkg, str(chroot_path), pkgbuild_path, version_path)
        else:
            _path = nosync_pacman()

        def job(_):
            #print('Getting package pkg_owned_files...')
            pkg_files = list_files(chroot_path)
            pkg_files = filter(lambda p: p not in chroot_files, pkg_files)
            pkg_files = list(pkg_files)
            #print('\n'.join(list(map(str, (pkg_files)))))

            #print(pkg_files[0], file_hash(str(CHROOT_PATH / pkg_files[0])))
            hashes = [file_hash(str(chroot_path / p)) for p in pkg_files]
            #print(hashes)

            pkg_files = [Path('/') / p for p in pkg_files]
            r = list(zip(map(str, pkg_files), hashes))
            return r

            
        pkg_files = install_pkg(chroot_path, pkg, _path, job)
        if is_aur:
            version = Path(version_path).read_text()
            version = version.split(' ', 1)[1].strip()
            print('###### VERSION: %s' % version)
            
        pkg_files = odict(pkg_files)
        #print('\n'.join(list(map(str, (pkg_files)))))
        
        state.setdefault(pkg, odict())
        state[pkg][version] = pkg_files
        save_state({pkg: state[pkg]})

        return version
    
    def owned_check():
        pkg_files = state[pkg][version]
        if pkg in config_files and version in config_files[pkg]:
            for changed, f in config_files[pkg][version]:
                if not (f in map(str, pkg_files)):
                    raise Exception('%s not in %s' % (f, pkg_files))
        

    if pkg in state and version in state[pkg]:
        for f, h in state[pkg][version].items():
            if not f.startswith('/'):
                raise Exception('%s %s %s' % (pkg, version, f))
        try:
            owned_check()
        except Exception as e:
            print(e)
            version = get_files(version)
    else:

        version = get_files(version)
        owned_check()
        

def search_filepath_state(p):
    for pkg, version in installed_pkgs.items():
        if not pkg in state or version not in state[pkg]:
            continue
        pfiles = state[pkg][version]
        if p in pfiles:
            return (pkg, version)
    return None
    
def search_filepath(p, pkgs):
    for pkg, versions in pkgs.items():
        assert(len(versions) == 1)
        for version in sorted(versions.keys(), key=lambda s: '_' if s is None else s):
            fs = versions[version]
            if p in fs:
                return (pkg, version)
    return None
    
modified_config_files = odict()
unmodified_config_files = odict()
for pkg, vs in config_files.items():
    modified_config_files[pkg] = odict()
    unmodified_config_files[pkg] = odict()
    for version, fs in vs.items():
        modified_config_files[pkg][version] = [f for f in fs if f[0] == MODIFIED]
        unmodified_config_files[pkg][version] = [f for f in fs if f[0] == UNMODIFIED]

owned_files = get_owned_files(installed_pkgs)

orphan_files = []
modified_files = odict()
uncheckable_files = []
for d in checked_paths:
    if Path(d).is_file():
        files = [Path(d)]
    else:
        files = d.glob('**/*')

    for p in files:
        skip = False
        presolved = p.resolve()
        for ip in ignored_paths:
            if str(p).startswith(ip) or str(presolved).startswith(ip):
                skip = True
                break
        if skip:
            continue

        p = presolved
        if not p.is_file():
            continue
        s = str(p)

        pkg = None
        version = None
        r = search_filepath_state(s)
        if r is not None:
            pkg, version = r
            phash = state[pkg][version][s]
            try:
                hash = file_hash(p)
            except PermissionError:
                txt = check_output(['sudo', 'cat', str(p)])
                hash = get_hash(txt)
            if hash == phash:
                continue
        else:
            r = search_filepath(s, config_files)
            if r:
                pkg, version = r
                if search_filepath(s, unmodified_config_files):
                    assert(search_filepath(s, modified_config_files))
                    continue

            r = search_filepath(s, owned_files)
            if r:
                pkg, version = r
                uncheckable_files.append((s, r))
                continue
            orphan_files.append(s)
            continue

        modified_files.setdefault(pkg, [])
        modified_files[pkg].append(s)

modified_files = odict(sorted([fs for fs in modified_files.items()], key=lambda fs: fs[0]))



            
def get_orphan_pkgs():
    if ORPHAN_PKGS_FILE.exists():
        with ORPHAN_PKGS_FILE.open('r') as f:
            ls = f.read()

    ls = filter(len, ls.split('\n'))

    r = odict()
    for line in ls:
        l = line.split(' ', 1)
        if len(l) != 2:
            print('malformed orphan pkg line: %s' % line)
            continue
        pkg, f = l
        if pkg.startswith(INTERNAL_PKG_MARKER):
            pkg = pkg.replace('$HOST', machine)
            print(pkg)
        r[f] = pkg
    return r


orphan_pkg_associations = get_orphan_pkgs()

ignored_orphan_files = []
r = odict()
for p in orphan_files:
    if p not in orphan_pkg_associations:
        ignored_orphan_files.append(p)
        continue
    pkg = orphan_pkg_associations[p]
    r.setdefault(pkg, [])
    r[pkg].append(p)
orphan_files = r

def print_paths(l):
    print('\n'.join(map(str, l)))
    
#print('### modified')
#print_paths(modified_files)
print('### ignored orphans')
print_paths(ignored_orphan_files)
print('### uncheckable')
print_paths(uncheckable_files)

#print
for pkg, fs in modified_files.items():
    print(pkg, installed_pkgs[pkg])
    print('\t%s' % (' '.join(fs)))

TAG_SEP = '#'
BASE_BRANCH_NAME = 'base'
BASE_TAG_NAME = '0'
MACHINE_SEP = '!'

class hg:
    class HgException(Exception):
        pass
    
    def __init__(self, repo_path):
        self.repo_path = repo_path
        
    def __getattr__(self, name):
        def f(*args, **kwargs):
            args = [str(a) for a in args]
            kws = []
            for k, v in kwargs.items():
                if v is False:
                    continue
                
                if len(k) > 1:
                    n = '--' + k
                else:
                    n = '-' + k
                kws.append(n)
                if v not in [True, None]:
                    kws.append(str(v))
                    
            cmd = ['hg', name, *kws, *args]
            print(' '.join(cmd))
            try:
                r = check_output(cmd, cwd=self.repo_path, universal_newlines=True, bufsize=16384 * 16)
            except subprocess.CalledProcessError as e:
                raise hg.HgException(str(e))
            return r
        return f

    def empty_commit(self, msg):
        t = Path(self.repo_path) / '.empty'
        t.write_text('')
        repo.add(t)
        repo.commit(m=msg)
        repo.rm(t)
        repo.commit(m=msg, amend=True)

    def commit_merge(self, branch):
        cur = self.branch().strip()
        if split_lines(self.merge(branch, P=True)):
            self.merge(branch)
            self.commit(m='mrg: %s into %s' % (branch, cur))
        
def split_lines(s):
    ls = [s.strip() for s in s.split('\n')]
    ls = [l for l in ls if l]
    return ls

def git_split_lines(s):
    r = split_lines(s)
    return [s[2:] if s.startswith('* ') else s for s in r]


def repo_has_branch(branch):
    return branch in split_lines(repo.branches(q=True))


def init_repo(repo_path):
    repo = hg(str(repo_path))
    if not repo_path.exists():
        repo_path.mkdir()
    if not (repo_path / '.hg').exists():
        repo.init()
    return repo

repo = init_repo(repo_path)
if not repo_has_branch('default'):
    repo.branch('default')
    repo.empty_commit('initial')



def commit_and_tag(files, msg, tag):
    repo.add(*files)
    
    if repo.diff():
        repo.commit(*files, m=msg)

        #reassign tag
        repo.tag(tag, local=True, force=True)


def get_file_org(pkg, version, files, outdir):
    chroot_path = CHROOT_PATH / 'org' / pkg
    is_aur = pkg not in installed_native_pkgs
    if is_aur:
        print('AUR package')
        pkgbuild_path = temp_dir('aurbuild-%s' % pkg)
        version_path = temp_dir('version-%s' % pkg)
        _path = aur_pacman(pkg, str(chroot_path), pkgbuild_path, version_path)
    else:
        _path = nosync_pacman()

    def job(_):
        r = []
        for src in files:
            src = Path(src)
            assert(src.is_absolute())
            rel = src.relative_to('/')
            src = chroot_path / rel
            dst = outdir / rel
            mkdir_p(dst.parent)

            assert(src.exists())
            check_output(['sudo', 'cp', '-a', str(src), str(dst)])
            r.append(dst)
        return r

    fs = install_pkg(chroot_path, pkg, _path, job)
    if is_aur:
        aur_version = Path(version_path).read_text()
        aur_version = aur_version.split(' ', 1)[1].strip()
        print('###### VERSION: %s %s' % (version, aur_version))
        assert(version == aur_version)
    return fs


def natural_comp(key):
    """ Sort the given iterable in the way that humans expect."""
    return [int(c) if c.isdigit() else c for c in re.split('([0-9]+)', key)]

class ListComp:
    def __init__(self, l):
        self.l = l

    def __lt__(self, o):
        for ka, kb in zip(self.l, o.l):
            if ka == kb:
                continue
            else:
                if isinstance(ka, int) and isinstance(kb, str):
                    return True
                if isinstance(kb, int) and isinstance(ka, str):
                    return False
                return ka < kb
        return False
                

def repo_has_branch(branch):
    return branch in split_lines(repo.branches(q=True))

branches = split_lines(repo.branches(q=True))
print(branches)

def tag_escape(tag):
    return tag.replace(':', '_')

def tag_name(branch, version=None):
    if version is None:
        version = BASE_TAG_NAME
    s = branch + TAG_SEP + version
    return tag_escape(s)

def tag_split(tag):
    return tag.split(TAG_SEP, 1)

def machine_branch(pkg):
    return pkg + MACHINE_SEP + machine

def machine_branch_main():
    return MACHINE_SEP + machine

tags = split_lines(repo.tags(q=True))
#hg
tags = [t for t in tags if t != 'tip']
print(tags)
pkg_committed_versions = odict()
for tag in tags:
    pkg, version = tag_split(tag)
    pkg_committed_versions.setdefault(pkg, [])
    pkg_committed_versions[pkg].append(version)
pkg_committed_versions = odict([(pkg, list(sorted(versions, key=lambda v: ListComp(natural_comp(v)))))
                                for pkg, versions in pkg_committed_versions.items()])
print(pkg_committed_versions)

#machine main branch
#branch = machine_branch_main()
#if branch not in branches:
#    #raise Exception('please create a branch named !%s with an initial commit' % (machine))
#    repo.update('default', clean=True)
#    repo.branch(branch)
#    repo.empty_commit('initial')


def git_repo_files():
    r = getattr(git, 'ls-tree')(r='HEAD', **{'name-only': True, 'full-tree': True})
    return git_split_lines(r)

def hg_repo_files():
    r = repo.status(A=True)
    ls = split_lines(r)
    ls = [l.split(' ', 1)[1] for l in ls if not l.startswith('?')]
    return ls

repo_files = hg_repo_files


def repo_differs(fs, integrity_check=False):
    #check if all files are present
    differs = False
    for f in fs:
        fp = Path(f)
        assert(fp.is_absolute())
        p = repo_path / fp.relative_to('/')
        if not p.exists() or (integrity_check and file_hash(str(p)) != file_hash(f)):
            differs = True
            print('%s differs from %s' % (p, f))

    #check if files were removed
    for p in repo_files():
        p = Path(p)
        if not p.is_file() or is_repo(p):
            continue
        f = Path('/') / p.relative_to(repo_path)
        if not f.exists():
            differs = True
            print('%s differs from %s' % (p, f))
    return differs


pkgs = sorted(list(modified_files.keys()) + list(orphan_files.keys()))
pkgs_unique = []
for p in pkgs:
    if p in pkgs_unique:
        continue
    pkgs_unique.append(p)
pkgs = pkgs_unique

machine_branches = []
print(pkgs)
for pkg in pkgs:
    version = installed_pkgs[pkg]
    print('----------%s %s----------' % (pkg, version))

    #can only update last version
    tag_version = tag_escape(version)
    if pkg in pkg_committed_versions and ListComp(natural_comp(tag_version)) < ListComp(natural_comp(pkg_committed_versions[pkg][-1])):
        print('ERROR: history rewriting (i.e. downgrading) not supported %s %s %s' % (pkg, tag_version, pkg_committed_versions[pkg]))
        print(versions)
        print(natural_comp(tag_version), *[natural_comp(v) for v in pkg_committed_versions[pkg]])
        continue
    
    #create pkg branch from master branch
    if repo_has_branch(pkg):
        repo.update(pkg, clean=True)
    else:
        repo.update('default', clean=True)
        repo.branch(pkg)

    #create org branch
    tag = tag_name(pkg, version)
    fs = modified_files.get(pkg, [])
    for s in fs:
        assert(not str(s) in orphan_files)
    print('with files: %s' % ' '.join(fs))

    if repo_differs(fs):
        fs = get_file_org(pkg, version, fs, repo_path)
        fs = list(map(str, fs))
        msg = '%s %s' % (pkg, version)
        commit_and_tag(fs, msg, tag)
            
    def repo_machine_branch(version, fs):
        #machine branches
        branch = machine_branch(pkg)

        if branch in pkg_committed_versions and ListComp(natural_comp(tag_escape(version))) < ListComp(natural_comp(pkg_committed_versions[branch][-1])):
            print('ERROR: history rewriting not supported %s %s %s' % (branch, tag_version, pkg_committed_versions[branch]))
            return

        has_pkg_branch = repo_has_branch(pkg)
        
        if repo_has_branch(branch):
            repo.update(branch, clean=True)
            if has_pkg_branch:
                repo.commit_merge(pkg)
        else:
            base = pkg if has_pkg_branch else 'default'
            repo.update(base, clean=True)
            repo.branch(branch)
            

        
        gfs = []
        for s in fs:
            src = Path(s)
            dst = repo_path / src.relative_to('/')
            mkdir_p(dst.parent)
            check_output(['sudo', 'cp', '-a', str(src), str(dst)])
            gfs.append(str(dst))


        tag = tag_name(branch, version)
        msg = '%s %s' % (pkg, version)
        commit_and_tag(gfs, msg, tag)


    fs = []
    fs += modified_files.get(pkg, [])
    fs += orphan_files.get(pkg, [])
    repo_machine_branch(version, fs)
        


machine_repo = init_repo(machine_repo_path)
machine_repo.pull(repo_path)
machine_repo.update('default')
for pkg in pkgs:
    #machine master branch
    #machine_repo.update(machine_branch_main(), clean=True)
    machine_repo.commit_merge(machine_branch(pkg))

