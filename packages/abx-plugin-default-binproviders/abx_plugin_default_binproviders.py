
import abx

from typing import Dict

from pydantic_pkgr import (
    AptProvider,
    BrewProvider,
    EnvProvider,
    BinProvider,
)
apt = APT_BINPROVIDER = AptProvider()
brew = BREW_BINPROVIDER = BrewProvider()
env = ENV_BINPROVIDER = EnvProvider()


@abx.hookimpl(tryfirst=True)
def get_BINPROVIDERS() -> Dict[str, BinProvider]:

    return {
        'apt': APT_BINPROVIDER,
        'brew': BREW_BINPROVIDER,
        'env': ENV_BINPROVIDER,
    }
