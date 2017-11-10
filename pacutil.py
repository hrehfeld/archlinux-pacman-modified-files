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

import socket

import git as gitlib
import re

USERNAME = 'hrehfeld'

pkg_blacklist = ['nextcloud-client', 'rtags-git', 'signal-muon-git', 'ttf-google-fonts-git', 'thrust', 'unigine-superposition', 'ttf-material-design-icons', 'google-chrome']


INTERNAL_PKG_MARKER = '__'
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
git_repo_path = Path('etc-repo/').absolute()

checked_paths = [Path(a) for a in sys.argv[1:]]

BASE_DIR = Path(__file__).parent

ORPHAN_PKGS_FILE = BASE_DIR / '.orphans'

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
    check_call(['env', 'PATH=%s' % path] + pacstrap_cmd, stdout=DEVNULL
    )

    d = str(chroot_path.absolute())
    sudo_chmod(d, 'ugo=rwx')
    
    r = job(chroot_path)

    shutil.rmtree(d)
    return r


def load_state():
    state = odict()
    old_state = STATE_PATH / 'db.json'
    if old_state.exists():
        with old_state.open('r') as f:
            state = json.load(f, object_pairs_hook=odict)

    if STATE_PATH.exists():
        for pkgf in STATE_PATH.glob('*'):
            pkg = pkgf.stem
            with pkgf.open('r') as f:
                state[pkg] = json.load(f, object_pairs_hook=odict)
    return state


def save_state(state):
    mkdir_p(STATE_PATH)
    for pkg in state:
        state_str = json.dumps(state[pkg])
        pkgf = STATE_PATH / (pkg + '.json')
        print('############# saving state to %s #########' % pkgf)
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

#get list of chroot files
noop_pacman = Path(pacman_base)
noop_pacman.write_text('#!/usr/bin/env sh\necho $@')
check_call(['chmod', '+x', str(noop_pacman)], stdout=DEVNULL)
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
    check_call(['chmod', '+x', str(nosync_pacman)], stdout=DEVNULL)
    path = str(nosync_pacman.parent.absolute()) + ':' + os.getenv('PATH')
    return path

#patch pacman call so that it doesn't sync db /every/ time
def aur_pacman(pkg, chroot, version_path):
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
    """ % (os.getenv('PATH'), USERNAME, str(PACMAN_DB_PATH), tempfile.mkdtemp(prefix='aurbuild-%s' % pkg), pkg, chroot, version_path)
    aur_pacman.write_text(cmd)
    check_call(['chmod', '+x', str(aur_pacman)], stdout=DEVNULL)
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
            version_path = CHROOT_PATH / (pkg + '-version')
            if version_path.exists():
                version_path.unlink()
            _path = aur_pacman(pkg, str(chroot_path), version_path)
        else:
            _path = nosync_pacman()

        def job(_):
            #print('Getting package files...')
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
modified_files = []
uncheckable_files = []
for d in checked_paths:
    if Path(d).is_file():
        files = [Path(d)]
    else:
        files = d.glob('**/*')

    for p in files:
        if p.is_symlink():
            p = p.resolve()
        if not p.is_file():
            continue
        s = str(p)
        skip = False
        for ip in ignored_paths:
            if s.startswith(ip):
                skip = True
        if skip:
            continue

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

        modified_files.append((s, (pkg, version)))


def print_paths(l):
    print('\n'.join(map(str, l)))
    



print('### modified')
print_paths(modified_files)
print('### orphan')
print_paths(orphan_files)
#print('### uncheckable')
#print_paths(uncheckable_files)

        
            
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
        ver = None
        r[f] = (pkg, ver)
    return r


orphan_pkgs = get_orphan_pkgs()

modified_files += [(p, orphan_pkgs[p]) for p in orphan_files if p in orphan_pkgs]


def append_files(file_list, pkg_dict):
    for p, (pkg, version) in file_list:
        pkg_dict.setdefault(pkg, odict())
        pkg_dict[pkg].setdefault(version, [])
        pkg_dict[pkg][version].append(p)
files = odict()
append_files([(p, (pkg, version)) for p, (pkg, version) in modified_files if version is None], files)
append_files(sorted([(p, (pkg, version)) for p, (pkg, version) in modified_files if version is not None]
                    , key=lambda p: p[1][1]), files)


for pkg, versions in sorted(files.items(), key=lambda t: t[0]):
    print(pkg)
    for version, fs in versions.items():
        print('\t%s: %s' % (version, ' '.join(fs)))

TAG_SEP = '#'
BASE_BRANCH_NAME = 'base'
BASE_TAG_NAME = '0'
MACHINE_SEP = '!'

if git_repo_path.exists():
    git_repo = gitlib.Repo(str(git_repo_path))
    git_repo.git.reset(hard=True)
else:
    git_repo = gitlib.Repo.init(str(git_repo_path))
    git = git_repo.git
    git.commit(m='initial', **{'allow-empty': True})

git = git_repo.git

class Git:
    def __getattr__(self, name):
        g  = getattr(git_repo.git, name)
        def f(*args, **kwargs):
            print('git %s %s %s' % (name, ' '.join(['--%s=%s' % x for x in kwargs.items()]), ' '.join(map(str, args))))
            return g(*args, **kwargs)
        return f
git = Git()    

def commit_and_tag(files, msg, tag):
    commit_success = False
    try:
        git.add(*files)
        git.commit(*files, m=msg)
        commit_success = True
        
    except gitlib.exc.GitCommandError as e:
        #nothing to commit
        print(e)
        pass

    #reassign tag
    try:
        git.tag(tag)
    except gitlib.exc.GitCommandError as e:
        if commit_success:
            git.tag(tag, d=True)
            git.tag(tag)


def get_file_org(pkg, version, paths):
    print('------------------(%s/%s): %s %s' % (i+1, len(files), pkg, paths))

    chroot_path = CHROOT_PATH / 'org' / pkg
    is_aur = pkg not in installed_native_pkgs
    if is_aur:
        print('AUR package')
        version_path = CHROOT_PATH / (pkg + '-version')
        if version_path.exists():
            version_path.unlink()
        _path = aur_pacman(pkg, str(chroot_path), version_path)
    else:
        _path = nosync_pacman()

    def job(_):
        r = []
        for src, dst in paths:
            src = chroot_path / src
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
                

        

branches = list(filter(len, git.branch(l=True).split('\n')))
branches = [b[1:] if b.startswith('*') else b for b in branches]
branches = [b.strip() for b in branches]
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

tags = list(filter(len, git.tag(l=True).split('\n')))
branch_tags = odict()
for tag in tags:
    pkg, version = tag_split(tag)
    branch_tags.setdefault(pkg, [])
    branch_tags[pkg].append(version)
print(branch_tags)
branch_tags = odict([(pkg, list(sorted(versions, key=lambda v: ListComp(natural_comp(v))))) for pkg, versions in branch_tags.items()])
print(branch_tags)

#machine main branch
branch = machine_branch_main()
if branch not in branches:
    git.checkout('master')
    git.checkout(b=branch, force=True)
    git.commit(m='initial', **{'allow-empty': True})


for pkg, versions in sorted(files.items(), key=lambda t: t[0]):

    #pkg from base branch
    print(pkg, branches)
    if pkg in branches:
        git.checkout(pkg, force=True)
    else:
        git.checkout('master')
        git.checkout(b=pkg)
        git.reset(hard=True)
        git.commit(m=pkg + '-initial', **{'allow-empty': True})
        git.tag(tag_name(pkg))

    for version, fs in versions.items():
        #org branches
        if version is not None:
            tag = tag_name(pkg, version)
            if pkg in branch_tags:
                tag_version = tag_escape(version)
                if tag_version in branch_tags[pkg]:
                    git.checkout(tag, force=True)

                    #check if files are present
                    missing = False
                    for f in fs:
                        p = git_repo_path / Path(f).relative_to('/')
                        if not p.exists():
                            missing = True
                            break
                    assert(not missing)
                    continue
                    #fetch org file etc
                else:
                    if ListComp(natural_comp(tag_version)) < ListComp(natural_comp(branch_tags[pkg][-1])):
                        print('ERROR: history rewriting not supported %s %s %s' % (pkg, tag_version, branch_tags[pkg]))
                        print(versions)
                        print(natural_comp(tag_version), *[natural_comp(v) for v in branch_tags[pkg]])
                        continue
                    git.checkout(tag_name(pkg, branch_tags[pkg][-1]), force=True)
            else:
                git.checkout(tag_name(pkg), force=True)

            org_fs = []
            for s in fs:
                if s in orphan_files:
                    continue
                p = Path(s).relative_to('/')
                dst = git_repo_path / p
                mkdir_p(dst.parent)
                org_fs.append((p, dst))

            fs = get_file_org(pkg, version, org_fs)
            fs = list(map(str, fs))
            print('GIT: adding %s' % ' '.join(fs))


            commit_and_tag(fs, version, tag)
            
    for version, fs in versions.items():
        #machine branches
        branch = machine_branch(pkg)
        print('----------%s %s----------' % (branch, version))
        try:
            git.checkout(branch)
        except gitlib.exc.GitCommandError:
            git.checkout(tag_name(pkg))
            git.checkout(b=branch)
            git.commit(m=branch + '-initial', **{'allow-empty': True})

        tag = tag_name(branch, version)
        if tag in tags:
            git.checkout(tag, force=True)
        else:
            if branch in branch_tags:
                if ListComp(natural_comp(tag_escape(version))) < ListComp(natural_comp(branch_tags[branch][-1])):
                    print('ERROR: history rewriting not supported %s %s %s' % (branch, tag_version, branch_tags[branch]))
                    continue
                git.checkout(tag_name(branch, branch_tags[branch][-1]), force=True)

        git.merge(tag_name(pkg, version))
        gfs = []
        for s in fs:
            src = Path(s)
            dst = git_repo_path / src.relative_to('/')
            mkdir_p(dst.parent)
            check_output(['sudo', 'cp', '-a', str(src), str(dst)])
            gfs.append(str(dst))


        commit_and_tag(gfs, version if version is not None else 'initial', tag)


    #machine master branch
    branch = machine_branch_main()
    git.checkout(branch, force=True)
    git.merge(machine_branch(pkg))

