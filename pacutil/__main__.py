#! /usr/bin/env python
from subprocess import DEVNULL
import subprocess

import json
from pathlib import Path

from collections import OrderedDict as odict

import shutil
import os
import sys

import socket

import re

import argparse

import config

import getpass

from version import earlier_version

import requests
import re

import urllib.parse

import time

from . import logging as log

from . import color as col

from .util import temp_dir, mkdir_p, check_call, check_output, copy_archive, file_hash, get_hash, handle_filepath, clean_glob
from .util import chmod, filter_odict, startswith_any, is_system_file, natural_comp, ListComp
from .util import hostname as machine
from hg import hg as _hg

hg = lambda repo_path: _hg(repo_path, log=log)

with Path('.pkg-blacklist').open('r') as f:
    pkg_blacklist = [p.strip() for p in f.read().split('\n')]


INTERNAL_PKG_MARKER = '__'
TAG_SEP = '#'
BASE_BRANCH_NAME = 'base'
BASE_TAG_NAME = '0'
MACHINE_SEP = '!'
FEATURE_SEP = '>'

DEFAULT_BRANCH = 'default'

progress_every = 10

BASE_DIR = Path(__file__).parent.parent

username = getpass.getuser()

arch = None

repo_path = Path(handle_filepath(config.repo_path)).absolute()
machine_repo_path = Path(handle_filepath(config.machine_repo_path)).absolute()
backup_repo_path = Path(handle_filepath(config.backup_repo_path)).absolute()

ORPHAN_PKGS_FILE = BASE_DIR / '.orphans'

IGNORE_FILE = BASE_DIR / '.ignore'
ignored_paths = []
if IGNORE_FILE.exists():
    with IGNORE_FILE.open('r') as f:
        ignored_paths = f.read().split('\n')
    ignored_paths = list(filter(lambda p: len(p), [p.strip() for p in ignored_paths]))

TMP_PATH = temp_dir('pacutil')
CHROOT_PATH = TMP_PATH / 'chroot'

pacman_base = TMP_PATH / 'pacman'

PACMAN_DB_PATH = TMP_PATH / 'tmp-pacman'

def get_state_path():
    return BASE_DIR / 'state' / arch

MODIFIED = 0
UNMODIFIED = 1
PACMAN_CFG_FILE_LIST_CMD = ['pacman', '-Qii']


def pacman_get_versions(chroot_path=None):
    ls = check_output(PACMAN_CFG_FILE_LIST_CMD, universal_newlines=True, cwd=chroot_path).split('\n')
    name_reg = re.compile(r'Name *: (.*)')
    ver_reg = re.compile(r'Version *: (.*)')
    name = None
    ver = None
    r = odict()
    for l in ls:
        m = name_reg.match(l)
        if m:
            name = m.group(1)
        elif name:
            m = ver_reg.match(l)
            if m:
                ver = m.group(1)
                r[name] = ver
    return r


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
                assert(ver is not None)
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
                
            

PACSTRAP_INSTALL_PKG = ['/usr/bin/pacstrap', '-c', '-G', '-M', '-d']


def list_files(chroot_path):
    pkg_files = chroot_path.glob('**/*')
    pkg_files = [p.relative_to(chroot_path) for p in pkg_files]
    pkg_files = filter(lambda p: not is_system_file(p), pkg_files)
    pkg_files = filter(lambda p: (chroot_path / p).is_file(), pkg_files)
    return pkg_files


class PacmanException(Exception):
    pass

class AurException(Exception):
    pass


def install_pkg(chroot_path, pkg, job, path=None):
    if path is None:
        path = nosync_pacman()

    assert(isinstance(chroot_path, Path))
    assert(isinstance(pkg, str))
    assert(isinstance(path, str))
    #extract pkg
    mkdir_p(chroot_path)
    d = chroot_path / 'etc'
    mkdir_p(d)
    d = d / 'pacman.d'
    mkdir_p(d)
    pacstrap_cmd = ['sudo'] + PACSTRAP_INSTALL_PKG + [str(chroot_path), pkg]
    try:
        check_call(['env', 'PATH=%s' % path] + pacstrap_cmd, stdout=DEVNULL)
    except subprocess.CalledProcessError as e:
        raise PacmanException(str(e))

    d = str(chroot_path.absolute())
    chmod('ugo=rwx', d, sudo=True)

    r = job(chroot_path)

    versions = pacman_get_versions(chroot_path)
    version = None
    if pkg in versions:
        version = versions[pkg]

    shutil.rmtree(d)
    return version, r


def load_state():
    state = odict()
    state_path = get_state_path()
    if state_path.exists():
        for pkgf in state_path.glob('*.json'):
            pkg = pkgf.stem
            with pkgf.open('r') as f:
                state[pkg] = json.load(f, object_pairs_hook=odict)
    return state


def save_state(state):
    state_path = get_state_path()
    mkdir_p(state_path)
    for pkg in state:
        state_str = json.dumps(state[pkg], indent=2)
        pkgf = state_path / (pkg + '.json')
        with pkgf.open('w') as f:
            f.write(state_str)


def parse_installed_packages(s):
    pkgs = s.split('\n')
    pkgs = filter(lambda s: s != '', pkgs)
    pkgs = odict([p.split(' ') for p in pkgs])
    return pkgs
        

def get_owned_files(installed_pkgs):
    PACMAN_FILE_LIST_CMD = ['pacman', '-Ql'] + list(installed_pkgs.keys())
    fs = check_output(PACMAN_FILE_LIST_CMD, universal_newlines=True).split('\n')
    r = odict()
    for line in fs:
        if not line:
            continue
        l = line.split(' ', 1)
        if len(l) != 2:
            raise Exception(line)
        pkg, f = l

        assert(pkg in installed_pkgs)
        ver = installed_pkgs[pkg]
        r.setdefault(pkg, odict())
        r[pkg].setdefault(ver, [])
        r[pkg][ver].append(f)
    return r


def find_pkg_owned_files(chroot_path, chroot_default_files):
    pkg_files = list_files(chroot_path)
    pkg_files = filter(lambda p: p not in chroot_default_files, pkg_files)
    pkg_files = list(pkg_files)
    hashes = [file_hash(str(chroot_path / p)) for p in pkg_files]

    pkg_files = [Path('/') / p for p in pkg_files]

    return list(zip(map(str, pkg_files), hashes))


def install_pkg_aur(chroot_path, pkg, job):
    assert(isinstance(chroot_path, Path))
    assert(isinstance(pkg, str))
    pkgbuild_path = temp_dir('aurbuild-%s' % pkg)
    version_path = temp_dir('version-%s' % pkg)
    path = aur_pacman(pkg, str(chroot_path), str(pkgbuild_path), str(version_path))

    version, pkg_files = install_pkg(chroot_path, pkg, job, path)

    version = Path(version_path).read_text()
    version = version.split(' ', 1)[1].strip()
    return version, pkg_files


def prepare_pacman_db():
    if PACMAN_DB_PATH.exists():
        shutil.rmtree(str(PACMAN_DB_PATH))
    mkdir_p(PACMAN_DB_PATH)
    #check_call('sudo pacman -Sy -b '.split() + [str(PACMAN_DB_PATH)])
    (PACMAN_DB_PATH / 'sync').symlink_to('/var/lib/pacman/sync')

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
    assert(isinstance(path, str))
    return path


#patch pacman call so that it doesn't sync db /every/ time
def aur_pacman(pkg, chroot, pkgbuild_path, version_path):
    os.rmdir(pkgbuild_path)
    os.rmdir(version_path)

    aur_pkg_page = 'https://aur.archlinux.org/packages/%s/' % pkg
    r = requests.get(aur_pkg_page)
    if r.status_code != 200:
        raise AurException('Package %s not found in AUR at %s. (Did you install this package from a disabled pacman repo?)' % (pkg, aur_pkg_page)) 
    find_re = '<a href="([^"]+)">Download snapshot</a>'
    m = re.search(find_re, r.text)
    if not m:
        log.debug(r.text)
        raise AurException('no snapshot url found on %s' % aur_pkg_page)
    snapshot_url = m.group(1)
    snapshot_url_info = urllib.parse.urlsplit(snapshot_url)
    url_path = Path(snapshot_url_info.path)
    tar_file = url_path.name
    # hack off any leftover ext
    pkg_extract_dir = url_path.stem.split('.')[0]
    if not snapshot_url_info.hostname:
        snapshot_url = urllib.parse.urlunsplit(('https', 'aur.archlinux.org', snapshot_url_info.path, snapshot_url_info.query, snapshot_url_info.fragment))
    log.info('Getting snapshot from %s', snapshot_url)

    aur_pacman = Path(tempfile.mkdtemp(prefix='pacman')) / Path(pacman_base)

    # sudo -u $USERNAME -H git clone https://aur.archlinux.org/${PKG}.git $TEMPD

    cmd = r"""#!/usr/bin/env sh
    set -x
    set -e
    
    export PATH={PATH}

    _sudo() {{
    sudo -u {USERNAME} -H $@
    }}

    _sudo mkdir -p {TEMPD}
    cd {TEMPD}
    _sudo curl -q -L -O {SNAPSHOT}
    _sudo tar -xvf {TAR_FILE}
    _sudo rm {TAR_FILE}
    cd {EXTRACT_DIR}
    _sudo BUILDDIR=/tmp/makepkg-pacutil makepkg -sr --asdeps --noconfirm
    /usr/bin/pacman -r {CHROOT} -U --noconfirm --dbpath {PACMANDB} -dd --nodeps {TEMPD}/{PKG}/{PKG}*.pkg.tar.xz
    _sudo /usr/bin/pacman -Q --dbpath {PACMANDB} {PKG} > {VERSION_PATH}
    cd /
    _sudo rm -rf {TEMPD}
    """.format(PATH=os.getenv('PATH'), USERNAME=username, PACMANDB=str(PACMAN_DB_PATH), TEMPD=pkgbuild_path, PKG=pkg, CHROOT=chroot, VERSION_PATH=version_path, SNAPSHOT=snapshot_url, TAR_FILE=tar_file, EXTRACT_DIR=pkg_extract_dir)
    log.debug(cmd)
    aur_pacman.write_text(cmd)
    chmod('+x', aur_pacman)
    aur_path = str(aur_pacman.parent.absolute()) + ':' + os.getenv('PATH')
    return aur_path

def iter_states(state, pkgs):
    for pkg, version in pkgs.items():
        if pkg not in state or version not in state[pkg]:
            continue
        yield (pkg, version), state[pkg][version]

def search_filepath_state(p, state, pkgs):
    return [info for info, filenames in iter_states(state, pkgs) if p in filenames]
    
def search_filepath(p, pkgs):
    for pkg, versions in pkgs.items():
        assert(len(versions) == 1)
        for version in sorted(versions.keys(), key=lambda s: '_' if s is None else s):
            fs = versions[version]
            if p in fs:
                return (pkg, version)
    return None
    
def get_orphan_pkgs():
    ls = []
    if ORPHAN_PKGS_FILE.exists():
        with ORPHAN_PKGS_FILE.open('r') as f:
            ls = f.read().split('\n')

    ls = filter(len, ls)
    ls = filter(lambda l: not l.startswith('#'), ls)

    r = odict()
    for line in ls:
        l = line.split(' ', 1)
        if len(l) != 2:
            log.warning('malformed orphan pkg line: %s' % line)
            continue
        pkg, f = l
        if pkg.startswith(INTERNAL_PKG_MARKER):
            pkg = pkg.replace('$HOST', machine)
        r[f] = pkg
    return r


def get_file_org(pkg, version, files, outdir, is_aur):
    chroot_path = CHROOT_PATH / 'org' / pkg
    if is_aur:
        log.info('AUR package')
        pkgbuild_path = temp_dir('aurbuild-%s' % pkg)
        version_path = temp_dir('version-%s' % pkg)
        _path = aur_pacman(pkg, str(chroot_path), str(pkgbuild_path), str(version_path))
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

    ref_version, fs = install_pkg(chroot_path, pkg, job, str(_path))
    #if is_aur:
    #    aur_version = Path(version_path).read_text()
    #    aur_version = aur_version.split(' ', 1)[1].strip()
    assert(version == ref_version)
    return fs


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

class PkgRepo(_hg):
    def __init__(self, *args):
        _hg.__init__(self, *args, log=log)


    def files_differ(self, fs, integrity_check=False):
        #check if all files are present
        differs = False
        for f in fs:
            fp = Path(f)
            assert(fp.is_absolute())
            p = repo_path / fp.relative_to('/')
            if not p.exists() or (integrity_check and file_hash(str(p)) != file_hash(f)):
                differs = True
                log.info('%s differs from %s' % (p, f))

        #check if files were removed
        for p in self.repo_files():
            p = Path(p)
            if not p.is_file() or hg.is_repo_internal_dir(p):
                continue
            f = Path('/') / p.relative_to(repo_path)
            if not f.exists():
                differs = True
                log.info('%s differs from %s' % (p, f))
        return differs


def get_installed_pkgs(native_only=False):
    flags = '-Q'
    if native_only:
        flags += 'n'
    return parse_installed_packages(check_output(['pacman', flags], universal_newlines=True))


def check_packages(args):
    prepare_pacman_db()

    #get list of chroot pkg_owned_files
    noop_pacman = Path(pacman_base)
    noop_pacman.write_text('''#!/usr/bin/env sh
    touch /var/log/pacman.log
    echo $@''')
    chmod('+x', noop_pacman)
    path = str(noop_pacman.parent.absolute()) + ':' + os.getenv('PATH')
    chroot_default_version, chroot_default_files = install_pkg(CHROOT_PATH / 'DUMMY', 'DUMMY', list_files, path)

    installed_pkgs = get_installed_pkgs()
    installed_native_pkgs = get_installed_pkgs(native_only=True)
    filter_odict(installed_pkgs, pkg_blacklist)
    filter_odict(installed_native_pkgs, pkg_blacklist)

    pkgs_version_not_found = []

    state = load_state()
    config_files = get_config_files()
    for i, (pkg, version) in enumerate(installed_pkgs.items()):
        def print_progress(msg):
            log.message('[%s/%s]: %s %s' % (i + 1, len(installed_pkgs), col.header(pkg), msg))
        requested_version = version

        def owned_check(version, pkg_files):
            if pkg in config_files and version in config_files[pkg]:
                for changed, f in config_files[pkg][version]:
                    if not (f in map(str, pkg_files)):
                        raise Exception('%s not in %s' % (f, pkg_files))



        chroot_path = CHROOT_PATH / pkg
        is_aur = pkg not in installed_native_pkgs
        install_f = install_pkg
        if is_aur:
            if args.native_only:
                continue
            install_f = install_pkg_aur

        def find_files(_):
            return odict(find_pkg_owned_files(chroot_path, chroot_default_files))

        pkg_files = None
        try:
            if pkg in state and version in state[pkg]:
                for f, h in state[pkg][version].items():
                    if not f.startswith('/'):
                        raise Exception('%s %s %s' % (pkg, version, f))
                try:
                    owned_check(version, state[pkg][version])
                except Exception as e:
                    log.info(e)
                    print_progress('found')
                    version, pkg_files = install_f(chroot_path, pkg, find_files)
            else:
                msg = 'not checked yet'
                if pkg in state:
                    msg = 'version %s not checked yet, only %s' % (version, ', '.join(state[pkg].keys()))
                print_progress(msg)
                version, pkg_files = install_f(chroot_path, pkg, find_files)
                owned_check(version, pkg_files)
        except PacmanException as e:
            log.error('skipping %s: %s' % (pkg, str(e)))
            continue
        except AurException as e:
            log.error('skipping %s: %s' % (pkg, str(e)))
            continue

        if pkg_files:
            #print('\n'.join(list(map(str, (pkg_files)))))

            state.setdefault(pkg, odict())
            state[pkg][version] = pkg_files
            save_state({pkg: state[pkg]})

        if version != requested_version:
            pkgs_version_not_found.append(pkg)


def main(args):
    checked_paths = [Path(a) for a in args.paths]
    
    installed_pkgs = get_installed_pkgs()
    installed_native_pkgs = get_installed_pkgs(native_only=True)

    state = load_state()

    # a dict of pkg -> list of (modified_state, filepath)
    config_files = get_config_files()
    
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
    files = []
    for d in checked_paths:
        if Path(d).is_file():
            files += [Path(d)]
        else:
            files += clean_glob(d)
    log.info('hashing %s files...' % len(files), )


    start_time = time.perf_counter()
    last_time = start_time
    for ifile, p in enumerate(files):

        now = time.perf_counter()
        if now - last_time > progress_every:
            last_time = now
            log.debug('%s%%' % int(ifile / len(files) * 100), )

        presolved = p.resolve()

        # don't filter earlier as resolve is expensive
        if startswith_any(str(p), ignored_paths) or startswith_any(str(presolved), ignored_paths):
            continue

        p = presolved
        if not p.is_file():
            continue
        s = str(p)

        pkg = None
        version = None
        r = search_filepath_state(s, state, installed_pkgs)
        if r:
            # pacman knows the file and we've seen it before in this

            # assume pacman ensures that no two packages may own the same file
            r = r[0]

            pkg, version = r
            phash = state[pkg][version][s]
            try:
                hash = file_hash(p)
            except PermissionError:
                cmd = ['sudo', 'sha256sum', str(p)]
                log.message(' '.join(cmd))
                hash = check_output(cmd)

            if hash == phash:
                continue
        else:
            # pacman knows this as a config file
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

    #print
    for pkg, fs in modified_files.items():
        log.info(pkg)
        log.info(installed_pkgs[pkg])
        log.info('\t%s' % (' '.join(fs)))

    repo = PkgRepo(str(repo_path))
    repo.initialize()
    repo.ensure_branch(DEFAULT_BRANCH)


    branches = repo.branches(q=True)
    log.debug(branches)

    tags = repo.tags(q=True)
    #hg
    tags = [t for t in tags if t != 'tip']
    log.debug(tags)
    pkg_committed_versions = odict()
    for tag in tags:
        pkg, version = tag_split(tag)
        pkg_committed_versions.setdefault(pkg, [])
        pkg_committed_versions[pkg].append(version)
    pkg_committed_versions = odict([(pkg, list(sorted(versions, key=lambda v: ListComp(natural_comp(v)))))
                                    for pkg, versions in pkg_committed_versions.items()])
    log.debug(pkg_committed_versions)

    #machine main branch
    #branch = machine_branch_main()
    #if branch not in branches:
    #    #raise Exception('please create a branch named !%s with an initial commit' % (machine))
    #    repo.update(DEFAULT_BRANCH, clean=True)
    #    repo.branch(branch)
    #    repo.empty_commit('initial')


    pkgs = sorted(list(modified_files.keys()) + list(orphan_files.keys()))

    # unique pkgs only
    pkgs_unique = []
    for p in pkgs:
        if p in pkgs_unique:
            continue
        pkgs_unique.append(p)
    pkgs = pkgs_unique

    # drop pkgs that don't have state
    pkgs = [pkg for pkg in pkgs if pkg in state and installed_pkgs[pkg] in state[pkg]]
    
    machine_branches = []
    for pkg in pkgs:
        # blacklisted or version not found
        if pkg not in installed_pkgs:
            continue
        version = installed_pkgs[pkg]
        log.message(col.header('%s %s' % (pkg, version)))

        #can only update last version
        tag_version = tag_escape(version)
        #if pkg in pkg_committed_versions and ListComp(natural_comp(tag_version)) < ListComp(natural_comp(pkg_committed_versions[pkg][-1])):
        if pkg in pkg_committed_versions and earlier_version(tag_version, pkg_committed_versions[pkg][-1]):
            log.error('history rewriting (i.e. downgrading) not supported: %s %s < %s' % (pkg, tag_version, pkg_committed_versions[pkg][-1]))
            log.debug(versions)
            log.debug(natural_comp(tag_version), *[natural_comp(v) for v in pkg_committed_versions[pkg]])
            continue

        #create pkg branch from master branch
        repo.ensure_branch(pkg, from_branch=DEFAULT_BRANCH, commit=False, clean=True)

        #create org branch
        tag = tag_name(pkg, version)
        fs = modified_files.get(pkg, [])
        for s in fs:
            assert(not str(s) in orphan_files)
        log.info('with files: %s' % ' '.join(fs))

        if repo.files_differ(fs):
            fs = get_file_org(pkg, version, fs, repo_path, is_aur=pkg not in installed_native_pkgs)
            fs = list(map(str, fs))
            msg = tag_name(pkg, version)
            repo.commit_and_tag(fs, msg, tag)
            
        def repo_machine_branch(version, fs):
            #machine branches
            branch = machine_branch(pkg)

            if branch in pkg_committed_versions:
                cur = tag_escape(version)
                last = pkg_committed_versions[branch][-1]
                if earlier_version(cur, last):
                    log.error('history rewriting not supported %s %s < %s' % (branch, cur, last))
                    return

            has_pkg_branch = repo.has_branch(pkg)

            base = pkg if has_pkg_branch else DEFAULT_BRANCH
            repo.ensure_branch(branch, from_branch=base, clean=True, commit=False)
            if has_pkg_branch:
                repo.commit_merge(pkg)

            gfs = []
            for s in fs:
                src = Path(s)
                dst = repo_path / src.relative_to('/')
                mkdir_p(dst.parent)
                check_output(['sudo', 'cp', '-a', str(src), str(dst)])
                gfs.append(str(dst))


            tag = tag_name(branch, version)
            msg = '%s %s' % (pkg, version)
            repo.commit_and_tag(gfs, msg, tag)


        fs = []
        fs += modified_files.get(pkg, [])
        fs += orphan_files.get(pkg, [])
        repo_machine_branch(version, fs)

    def print_paths(l):
        log.message('\n'.join(map(str, l)))

    #print('### modified')
    #print_paths(modified_files)


    log.message('''### These files are not associated with any package:
    Add "<pkg> <filepath>" to %s to assign them to a package.''' % ORPHAN_PKGS_FILE)
    print_paths(ignored_orphan_files)
    log.message('### uncheckable')
    print_paths(uncheckable_files)



def merge_features(args):
    repo = PkgRepo(str(repo_path))
    repo.initialize()

    branches = args.branch

    for branch in branches:
        pkg, feature_name = branch.split(FEATURE_SEP, 1)

        base = pkg if repo.has_branch(pkg) else DEFAULT_BRANCH
        machine_b = machine_branch(pkg)
        repo.ensure_branch(machine_b, from_branch=base, commit=False, clean=True)
        repo.commit_merge(branch, machine_b)


def merge_machine_branches(args):
    repo = PkgRepo(str(repo_path))
    repo.initialize()

    machine_repo = hg(str(machine_repo_path))
    machine_repo.initialize()

    if machine_repo.has_branch(DEFAULT_BRANCH):
        machine_repo.update(DEFAULT_BRANCH)

    branches = repo.branches(q=True)
    for (pkg, version) in get_installed_pkgs().items():
        branch = machine_branch(pkg)
        if branch in branches:

            machine_repo.pull(repo_path, branch=branch)
            
            #machine master branch
            #machine_repo.update(machine_branch_main(), clean=True)
            machine_repo.commit_merge(branch)

def sync(args):
    machine_repo = hg(str(machine_repo_path))
    machine_repo.initialize()

    backup_repo = hg(str(backup_repo_path))
    backup_repo.initialize()

    #git ls-tree -r "!$(hostname)" --name-only --full-name
    files = machine_repo.status(all=True, **{'no-status': True})

    for f in files:
        repo_file = machine_repo_path / f
        fs_file = Path('/') / f
        backup_file = backup_repo_path / f

        log.debug(repo_file, fs_file, backup_file)

        if fs_file.exists():
            mkdir_p(backup_file.parent)
            copy_archive(fs_file, backup_file, sudo=True)

        copy_archive(repo_file, fs_file, sudo=True)
    backup_repo.add(*files)
    if backup_repo.diff():
        backup_repo.commit(message='synced')
    else:
        log.message('Nothing changed.')
        
p = argparse.ArgumentParser(description='check archlinux files for changes')
p.add_argument('--verbose', '-v', action='store_true', help='enable verbose info output')
p.add_argument('--debug', action='store_true', help='enable debug output')
p.add_argument('--quiet', '-q', action='store_true', help='disable informative output')

p.add_argument('--arch', default=None, help='override detected architecture')

subp = p.add_subparsers()

check_packages_p = subp.add_parser('check-packages')
check_packages_p.add_argument('--native-only', action='store_true')
check_packages_p.set_defaults(func=check_packages)

checkp = subp.add_parser('check-files')
checkp.add_argument('paths', nargs='+')
checkp.set_defaults(func=main)

merge_machine_branchesp = subp.add_parser('merge-features', description='''Merge feature branches $pkg>$feature-name into the corrensponding $pkg-$host branch for this machine.''')
merge_machine_branchesp.add_argument('branch', nargs='+')
merge_machine_branchesp.set_defaults(func=merge_features)

merge_machine_branchesp = subp.add_parser('merge', description='''For every package installed on this system, merge the $pkg-$host branches from the main repo into the machine repo.''')
merge_machine_branchesp.set_defaults(func=merge_machine_branches)

syncp = subp.add_parser('sync')
syncp.set_defaults(func=sync)

args = p.parse_args()

if args.arch is None:
    arch = check_output(['uname', '-a']).decode('utf-8').split()[-2]
else:
    arch = args.arch

log_level = log.WARNING
if args.debug:
    log_level = log.DEBUG
elif args.verbose:
    log_level = log.INFO

log.init(args.quiet, log_level)

if not 'func' in args:
    p.print_help()
    exit(1)
args.func(args)
