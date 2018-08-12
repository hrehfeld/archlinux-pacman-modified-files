import tempfile

from pathlib import Path

import subprocess
import hashlib

import socket

from . import logging as log


import re


hostname = socket.gethostname()

def temp_dir(prefix):
    path = tempfile.mkdtemp(prefix=prefix)
    return Path(path)


def mkdir_p(p):
    return p.mkdir(exist_ok=True, parents=True)
    
def check_output(cmd, *args, **kwargs):
    log.debug(' '.join(cmd))
    return subprocess.check_output(cmd, *args, **kwargs)

def check_call(cmd, *args, **kwargs):
    log.debug(' '.join(cmd))
    return subprocess.check_call(cmd, *args, **kwargs)

def copy_archive(fa, fb, sudo=False):
    cmd = ['cp', '-a', str(fa), str(fb)]
    if sudo:
        cmd = ['sudo'] + cmd
    return check_call(cmd)
    

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

def handle_filepath(p):
    vars = dict(HOSTNAME=hostname, HOME=str(Path.home()))
    p = p.strip()
    for k, v in vars.items():
        p = p.replace('$%s' % k, v)
    return p


def clean_glob(d: Path):
    for child in d.iterdir():
        yield child

        # is_dir() can throw if symlink points to removed device
        try:
            if child.is_dir():
                yield from clean_glob(child)
        except OSError as e:
            log.warning('Cannot check %s: %s' % (child, e))
            continue

def chmod(mode, path, sudo=False):
    cmd = ['chmod', '-R', mode, str(path)]
    if sudo:
        cmd = ['sudo'] + cmd
    return check_call(cmd, stdout=subprocess.DEVNULL)


def filter_odict(d, keys):
    for k in keys:
        if k in d:
            del d[k]


def filter_odict_f(d, filter):
    for k in d:
        if not filter(k, d[k]):
            del d[k]

def startswith_any(s, tests):
    for test in tests:
        if s.startswith(test):
            return True
    return False


        
def is_system_file(p):
    s = str(p)
    return s.startswith('proc') or s.startswith('sys')


def split_lines(s):
    ls = [s.strip() for s in s.split('\n')]
    ls = [l for l in ls if l]
    return ls


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
                

