#!/usr/bin/env python3

import json
import os
import sys
from plexapi.myplex import MyPlexAccount
from getpass import getpass

############################################################
# BASE CONFIGURATION
# Default values used for first-time config creation and upgrades.
############################################################

config_path = os.path.join(os.path.dirname(os.path.realpath(sys.argv[0])), 'config.json')

base_config = {
    'RUNTIME': {  # Overarching category for settings that affect the runtime
        'DRY_RUN': False,  # Simulate deletions without applying them
        'AUTO_DELETE': False,  # Whether to auto-delete lowest scored dupes
        'FIND_DUPLICATE_FILEPATHS_ONLY': False,  # Only mark duplicates with identical filepaths
        'SKIP_OTHER_DUPES': False,  # Skip interactive/automatic deletion of other duplicates
        'FIND_UNAVAILABLE': False,  # Try to remove missing media files from Plex
        'FIND_EXTRA_TS': False,  # Remove .ts files when other copies exist
        'SKIP_PLEX_VERSIONS_FOLDER': True,  # Skip Plex Versions folder contents
        'LOGGING_TIMEZONE': 'UTC'  # Timezone used for logging timestamps
    },
    'PLEX': {  # Overarching category for Plex server settings
        'LIBRARIES': {},  # Plex libraries to scan (e.g., ['Movies', 'TV'])
        'SERVER_URL': 'https://plex.your-server.com',  # Plex server URL (https/http)
        'AUTH_TOKEN': '',  # Plex API token (fetched via login or manually set)
    },
    'SCORING': {  # Overarching category for enabling/disabling various scoring options
        'VIDEO_HEIGHT_MULTIPLIER': 2,  # Used in scoring: height (in pixels) * multiplier
        'SCORE_FILESIZE': True,  # Include file size in scoring
        'SCORE_AUDIOCHANNELS': True,  # Include audio channel count in scoring
        'SCORE_VIDEOBITRATE': {  # Bitrate score multiplier
            'enabled': True,
            'multiplier': 2
        },
    },
    'AUDIO_CODEC_SCORES': {  # Custom audio codec scoring
        'Unknown': 0, 'wmapro': 200, 'mp2': 500, 'mp3': 1000, 'ac3': 1000, 'dca': 2000, 'pcm': 2500, 
        'flac': 2500, 'dca-ma': 4000, 'truehd': 4500, 'aac': 1000, 'eac3': 1250},
    'VIDEO_CODEC_SCORES': {  # Custom video codec scoring
        'Unknown': 0, 'h264': 10000, 'h265': 5000, 'hevc': 5000, 'mpeg4': 500, 'vc1': 3000, 'vp9': 1000,
        'mpeg1video': 250, 'mpeg2video': 250, 'wmv2': 250, 'wmv3': 250, 'msmpeg4': 100, 'msmpeg4v2': 100, 'msmpeg4v3': 100},
    'VIDEO_RESOLUTION_SCORES': {  # Resolution label scoring
        'Unknown': 0, '4k': 20000, '1080': 10000, '720': 5000, '480': 3000, 'sd': 1000},
    'FILENAME_SCORES': {},  # Keyword pattern scoring (e.g. *Remux*)
    'SKIP_LIST': [],  # Filenames or folders to always skip

}
cfg = None

############################################################
# CONFIG GENERATION (Interactive Setup)
############################################################

def prefilled_default_config(configs):
    """
    Generate default config using user-supplied inputs
    (URL, token, deletion preference), filling in all other base values.
    """

    default_config = base_config.copy()

    # Set the token and server url
    default_config['PLEX']['SERVER_URL'] = configs['url']
    default_config['PLEX']['AUTH_TOKEN'] = configs['token']

    # Set AUTO_DELETE config option
    default_config['RUNTIME']['AUTO_DELETE'] = configs['auto_delete']

    # Set Sections/Libraries
    default_config['PLEX']['LIBRARIES'] = [
        'Movies',
        'TV'
    ]

    # Set Filename Scores
    default_config['FILENAME_SCORES'] = {
        '*Remux*': 20000,
        '*1080p*BluRay*': 15000,
        '*720p*BluRay*': 10000,
        '*WEB*NTB*': 5000,
        '*WEB*VISUM*': 5000,
        '*WEB*KINGS*': 5000,
        '*WEB*CasStudio*': 5000,
        '*WEB*SiGMA*': 5000,
        '*WEB*QOQ*': 5000,
        '*WEB*TROLLHD*': 2500,
        '*REPACK*': 1500,
        '*PROPER*': 1500,
        '*WEB*TBS*': -1000,
        '*HDTV*': -1000,
        '*dvd*': -1000,
        '*.avi': -1000,
        '*.ts': -1000,
        '*.vob': -5000
    }

    return default_config


def build_config():
    """
    Create config.json if it doesn't exist, based on user input and Plex login.
    """
    if os.path.exists(config_path):
        return False

    print(f"Dumping default config to: {config_path}")

    configs = dict(url='', token='', auto_delete=False)

    # Get URL
    configs['url'] = input("Plex Server URL: ")

    # Get Credentials for plex.tv
    user = input("Plex Username: ")
    password = getpass('Plex Password: ')

    # Get choice for Auto Deletion
    auto_del = input("Auto Delete duplicates? [y/n]: ").strip().lower()
    while auto_del not in ['y', 'n']:
        auto_del = input("Auto Delete duplicates? [y/n]: ").strip().lower()
    configs['auto_delete'] = (auto_del == 'y')

    # Authenticate and get token
    account = MyPlexAccount(user, password)
    configs['token'] = account.authenticationToken

    # Write config file
    with open(config_path, 'w') as fp:
        json.dump(prefilled_default_config(configs), fp, sort_keys=True, indent=2)

    return True

############################################################
# CONFIG LOAD, SAVE, UPGRADE
############################################################

def dump_config():
    """Write current config (cfg) back to disk."""
    if not os.path.exists(config_path):
        return False
    with open(config_path, 'w') as fp:
        json.dump(cfg, fp, sort_keys=True, indent=2)
    return True


def load_config():
    """Load config.json from disk."""
    with open(config_path, 'r') as fp:
        return json.load(fp)


def upgrade_settings(defaults, currents):
    """
    Recursively upgrade a config dictionary with new keys from default.
    Logs added keys and returns a new merged config.
    """
    upgraded = False

    def inner_upgrade(default, current, key=None):
        sub_upgraded = False
        merged = current.copy()
        if isinstance(default, dict):
            for k, v in default.items():
                # missing k
                if k not in current:
                    merged[k] = v
                    sub_upgraded = True
                    if not key:
                        print("Added %r config option: %s" % (str(k), str(v)))
                    else:
                        print("Added %r to config option %r: %s" % (str(k), str(key), str(v)))
                    continue
                # iterate children
                if isinstance(v, (dict, list)):
                    did_upgrade, merged[k] = inner_upgrade(default[k], current[k], key=k)
                    sub_upgraded = did_upgrade or sub_upgraded

        elif isinstance(default, list) and key:
            for v in default:
                if v not in current:
                    merged.append(v)
                    sub_upgraded = True
                    print("Added to config option %r: %s" % (str(key), str(v)))
                    continue
        return sub_upgraded, merged

    upgraded, upgraded_settings = inner_upgrade(defaults, currents)
    return upgraded, upgraded_settings


############################################################
# LOAD CFG
############################################################

# dump/load config
if build_config():
    print("Please edit the default configuration before running again!")
    sys.exit(0)
else:
    current_config = load_config()
    upgraded, cfg = upgrade_settings(base_config, current_config)
    if upgraded:
        dump_config()
        print("New config options were added, adjust and restart!")
        sys.exit(0)

