import logging
from logging import DEBUG, INFO, WARNING, ERROR, CRITICAL

try:
    import coloredlogs
    coloredlogs.install()
except ImportError:
    pass

# can only import these after basicConfig is set
message = lambda *args: print(*args)
debug, info, warning, error, critical = logging.debug, logging.info, logging.warning, logging.error, logging.critical

def init(quiet, log_level):
    if quiet:
        log_level = logging.WARNING
        message = debug
        
    logging.basicConfig(level=log_level)
    # import after basicConfig
    debug, info, warning, error, critical = logging.debug, logging.info, logging.warning, logging.error, logging.critical

    
