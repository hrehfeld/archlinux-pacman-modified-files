class _c:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def colored(s, c):
    return ('%s%s%s' % (c, s, _c.ENDC))


def bold(s): return colored(s, _c.BOLD)
def underline(s): return colored(s, _c.UNDERLINE)
def header(s): return colored(s, _c.HEADER)

def fail(s): return colored(s, _c.FAIL)
def warn(s): return colored(s, _c.WARNING)
def ok(s): return colored(s, _c.OKGREEN)
def info(s): return colored(s, _c.OKBLUE)
