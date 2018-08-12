import subprocess
from pathlib import Path

def split_lines(s):
    ls = [s.strip() for s in s.split('\n')]
    ls = [l for l in ls if l]
    return ls


class hg:
    class HgException(Exception):
        pass

    @staticmethod
    def is_repo_internal_dir(p):
        return p.exists() and p.is_dir() and p.name == '.hg'

    def __init__(self, repo_path, log=None):
        self.repo_path = repo_path
        self.log = log

    def make_command(self, name):
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
            if self.log:
                self.log.info(self.repo_path + ': ' + ' '.join(cmd))
            try:
                r = subprocess.check_output(cmd, cwd=self.repo_path, universal_newlines=True, bufsize=16384 * 16)
            except subprocess.CalledProcessError as e:
                raise hg.HgException(str(e))
            return r
        return f

    def __getattr__(self, name):
        return self.make_command(name)

    def empty_commit(self, msg):
        t = Path(self.repo_path) / '.empty'
        t.write_text('')
        self.add(t)
        self.commit(m=msg)
        self.rm(t)
        self.commit(m=msg, amend=True)

    def commit_merge(self, branch, cur=None):
        if not cur:
            cur = self.branch().strip()
        if split_lines(self.merge(branch, P=True)):
            self.merge(branch)
            self.commit(m='mrg: %s into %s' % (branch, cur))

    def has_branch(self, branch):
        return branch in self.branches(q=True)

    def initialize(self):
        repo_path = Path(self.repo_path) / '.hg'
        if not self.is_repo_internal_dir(repo_path):
            mkdir_p(repo_path)
            self.init()

    def commit_and_tag(self, files, msg, tag):
        self.add(*files)

        if self.diff():
            self.commit(*files, m=msg)

            #reassign tag
            self.tag(tag, local=True, force=True)


    def repo_files(self):
        ls = self.status(A=True)
        ls = [l.split(' ', 1)[1] for l in ls if not l.startswith('?')]
        return ls

    
            

    def ensure_branch(self, name, from_branch=None, commit=True, clean=False):
        if not self.has_branch(name):
            if from_branch:
                self.update(from_branch, clean=clean)
            elif clean:
                self.update(clean=True)
            self.branch(name)
            if commit:
                self.empty_commit('initial')
            return True
        else:
            self.update(name, clean=clean)
            return False


    def branches(self, *args, **kwargs):
        return split_lines(self.make_command('branches')(*args, **kwargs))

    def tags(self, *args, **kwargs):
        return split_lines(self.make_command('tags')(*args, **kwargs))

    def status(self, *args, **kwargs):
        return split_lines(self.make_command('status')(*args, **kwargs))

        
