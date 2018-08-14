import logging
from logging import DEBUG, INFO, WARNING, ERROR, CRITICAL

try:
    import coloredlogs
except ImportError:
    pass

DEBUG = 'debug'
INFO = 'info'
WARNING = 'warning'
ERROR = 'error'
CRITICAL = 'critical'

_loglevels = { DEBUG: logging.DEBUG, INFO: logging.INFO, WARNING: logging.WARNING, ERROR: logging.ERROR, CRITICAL: logging.CRITICAL }

# can only import these after basicConfig is set
message = lambda *args: print(*args)
debug = info = lambda *args: None
warning = error = critical = lambda *args: print(*args)

def init(quiet, log_level):
    global message, debug, info, warning, error, critical
    if quiet:
        print('quiet mode')
        log_level = WARNING
        message = debug

    log_level = _loglevels[log_level]

    coloredlogs.install(level=log_level)
    # import after basicConfig / install

    debug, info, warning, error, critical = logging.debug, logging.info, logging.warning, logging.error, logging.critical

    
