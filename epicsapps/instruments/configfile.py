
from ..utils import ConfigFile, get_default_configfile

CONFFILE = 'instruments.yaml'

class InstrumentConfig(ConfigFile):
    def __init__(self, fname='instruments.yaml', default_config=None):
        if default_config is None:
            default_config = dict(server='sqlite', dbname=None, host=None,
                                  port='5432', user=None, password=None,
                                  recent_dbs=[])
        ConfigFile.__init__(self, fname, default_config=default_config)
