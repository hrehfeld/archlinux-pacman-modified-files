import tempfile

from pathlib import Path

import subprocess
import hashlib

import socket

from . import logging as log

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

