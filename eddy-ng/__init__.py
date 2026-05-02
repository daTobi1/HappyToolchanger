try:
    from klippy.configfile import ConfigWrapper
    from .probe_eddy_ng import ProbeEddy

    def load_config_prefix(config: ConfigWrapper):
        return ProbeEddy(config)
except ImportError:
    # Running outside Klipper (e.g. pytest) — plugin entry point not available
    pass
