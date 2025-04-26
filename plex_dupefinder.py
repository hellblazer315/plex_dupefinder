#!/usr/bin/env python3

# === Import Standard Libraries ===
import collections
import itertools
import logging
import os
import sys
import time
import argparse
from fnmatch import fnmatch
from tabulate import tabulate
from datetime import datetime
from pytz import timezone

# === Import Project Config ===
from config import cfg

# === Ensure URL Compatibility ===
try:
    from urlparse import urljoin  # Python 2
except ImportError:
    from urllib.parse import urljoin  # Python 3

# === Import External Libraries ===
from plexapi.server import PlexServer
import requests

############################################################
# INIT (LOGGER & PLEX SERVER SETUP)
# Configure logger timezone based on configuration
# tz is used globally for consistent timezone-aware logging
# tz_abbr and utc_offset are passed as extras to log statements
############################################################


# Set up timezone from config
tz_str=cfg['RUNTIME']['LOGGING_TIMEZONE']
tz=timezone(tz_str)
tz_abbr = datetime.now(tz).strftime('%Z')
utc_offset = int(datetime.now(tz).strftime('%z')) // 100
log_tz = {'timezone': tz_abbr, 'utc_offset': utc_offset}

# Ensure logger uses correct timezone for timestamps
logging.Formatter.converter = lambda *args: datetime.now(tz=tz).timetuple()

# Define location for activity logs (next to the script)
log_filename = os.path.join(os.path.dirname(os.path.realpath(sys.argv[0])), 'activity.log')

# Configure logger
logging.basicConfig(
    filename=log_filename,
    level=logging.DEBUG,
    format='[%(asctime)s %(timezone)s(%(utc_offset)s)] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Silence excessive output from urllib3 (used by requests)
logging.getLogger('urllib3.connectionpool').disabled = True

# Main logger instance for this script
log = logging.getLogger("Plex_Dupefinder")

# Configure Plex server object using URL and token from config
try:
    plex = PlexServer(cfg['PLEX']['SERVER_URL'], cfg['PLEX']['AUTH_TOKEN'])
except:
    log.exception("Exception connecting to server %r with token %r", cfg['PLEX']['SERVER_URL'], cfg['PLEX']['AUTH_TOKEN'])
    print(f"Exception connecting to {cfg['PLEX']['SERVER_URL']} with token: {cfg['PLEX']['AUTH_TOKEN']}")
    exit(1)

############################################################
# PLEX METHODS
# Methods that interact with the Plex API to fetch duplicates,
# determine section types, extract media metadata, compute
# scoring metrics, and handle deletion of media items.
############################################################


def get_dupes(plex_section_name):
    """
    Query Plex for duplicate media items in the given library section.
    If configured, only consider items with identical file paths.
    """
    sec_type = get_section_type(plex_section_name)
    dupe_search_results = plex.library.section(plex_section_name).search(duplicate=True, libtype=sec_type)

    dupe_search_results_new = dupe_search_results.copy()
    if cfg['RUNTIME']['FIND_DUPLICATE_FILEPATHS_ONLY']:
        for dupe in dupe_search_results:
            # Only keep items where all locations match the first one
            if any(x != dupe.locations[0] for x in dupe.locations):
                dupe_search_results_new.remove(dupe)

    log.info("Plex Dupe Finder Start - %s Library", plex_section_name, extra=log_tz)
    return dupe_search_results_new


def get_section_type(plex_section_name):
    """
    Determine whether the given library section contains TV shows or movies.
    Required to perform a proper duplicate search.
    """
    try:
        plex_section_type = plex.library.section(plex_section_name).type
    except Exception:
        log.exception("Exception occurred while trying to lookup the section type for Library: %s", plex_section_name)
        exit(1)
    return 'episode' if plex_section_type == 'show' else 'movie'


def get_score(media_info):
    """
    Calculate a custom score for a media item based on codecs, resolution,
    filename patterns, bitrate, duration, dimensions, audio channels, and size.
    Scoring logic is configurable in the config file.
    """
    score = 0

    # Score based on audio codec
    for codec, codec_score in cfg['AUDIO_CODEC_SCORES'].items():
        if codec.lower() == media_info['audio_codec'].lower():
            score += int(codec_score)
            log.debug("Added %d to score for audio_codec being %r", int(codec_score), str(codec), extra=log_tz)
            break

    # Score based on video codec
    for codec, codec_score in cfg['VIDEO_CODEC_SCORES'].items():
        if codec.lower() == media_info['video_codec'].lower():
            score += int(codec_score)
            log.debug("Added %d to score for video_codec being %r", int(codec_score), str(codec), extra=log_tz)
            break

    # Score based on video resolution category 
    for resolution, resolution_score in cfg['VIDEO_RESOLUTION_SCORES'].items():
        if resolution.lower() == media_info['video_resolution'].lower():
            score += int(resolution_score)
            log.debug("Added %d to score for video_resolution being %r", int(resolution_score), str(resolution), extra=log_tz)
            break

    # Score based on keywords in filename
    for filename_keyword, keyword_score in cfg['FILENAME_SCORES'].items():
        for filename in media_info['file']:
            if fnmatch(os.path.basename(filename.lower()), filename_keyword.lower()):
                score += int(keyword_score)
                log.debug("Added %d to score for match filename_keyword %s", int(keyword_score), filename_keyword, extra=log_tz)
                
    # Score based on video bitrate (if enabled)
    if cfg['SCORING']['SCORE_VIDEOBITRATE']['enabled']:
        score += int(media_info['video_bitrate']) * cfg['SCORING']['SCORE_VIDEOBITRATE']['multiplier']
        log.debug("Added %d to score for video bitrate being %r",
                  int(media_info['video_bitrate']) * cfg['SCORING']['SCORE_VIDEOBITRATE']['multiplier'],
                  str(media_info['video_bitrate']), extra=log_tz)
        
    # Score based on video duration
    score += int(media_info['video_duration']) / 300
    log.debug("Added %d to score for video duration being %r",
              int(media_info['video_duration']) / 300,
              str(media_info['video_duration']), extra=log_tz)

    # Score based on video resolution width
    score += int(media_info['video_width']) * 2
    log.debug("Added %d to score for video width being %r",
              int(media_info['video_width']) * 2,
              str(media_info['video_width']), extra=log_tz)

    # Score based on video resolution height
    score += int(media_info['video_height']) * cfg['SCORING']['VIDEO_HEIGHT_MULTIPLIER']
    log.debug("Added %d to score for video height being %r",
              int(media_info['video_height']) * cfg['SCORING']['VIDEO_HEIGHT_MULTIPLIER'],
              str(media_info['video_height']), extra=log_tz)

    # Score based on number of audio channels (if enabled)
    if cfg['SCORING']['SCORE_AUDIOCHANNELS']:
        score += int(media_info['audio_channels']) * 1000
        log.debug("Added %d to score for audio channels being %r",
                  int(media_info['audio_channels']) * 1000,
                  str(media_info['audio_channels']), extra=log_tz)

    # Score based on file size (if enabled)
    if cfg['SCORING']['SCORE_FILESIZE']:
        score += int(media_info['file_size']) / 100000
        log.debug("Added %d to score for total file size being %r",
                  int(media_info['file_size']) / 100000,
                  str(media_info['file_size']), extra=log_tz)

    return int(score)

def get_item_metadata(item, item_metadata=None):
    metadata = {
        'media_type': safe_getattr(item, 'type', default=None, label="Media item has no type"),
        'tmdb_id': 0,
        'tvdb_id': 0
    }

    guids = safe_getattr(item, 'guids', default=[], label="Media item has no guids")
    for guid in item.guids:
        if guid.id.startswith("tmdb://"):
            try:
                metadata['tmdb_id']=int(guid.id.replace("tmdb://", "").split("?")[0])
            except ValueError:
                log.warning(f"Failed to extract TMDB ID from guid: {guid.id}", extra=log_tz)
        if guid.id.startswith("tvdb://"):
            try:
                metadata['tvdb_id'] = int(guid.id.replace("tvdb://", "").split("?")[0])
            except ValueError:
                log.warning(f"Failed to extract TVDB ID from guid: {guid.id}", extra=log_tz)

    return metadata

def get_media_info(item, item_metadata):
    """
    Extract relevant metadata from a Plex media item object.
    This includes codecs, resolution, bitrate, dimensions,
    audio streams, filenames, file sizes, and existence state.
    """
    info = {
        'id': 'Unknown',
        'video_bitrate': 0,
        'audio_codec': 'Unknown',
        'audio_channels': 0,
        'video_codec': 'Unknown',
        'video_resolution': 'Unknown',
        'video_width': 0,
        'video_height': 0,
        'video_duration': 0,
        'file': [],
        'file_short': [],
        'multipart': False,
        'file_exists': True,  # Used with FIND_UNAVAILABLE
        'file_exts': {},      # Used with FIND_EXTRA_TS; It is an array whose keys are the file extensions and whose values are the count of media with that file extension
        'file_size': 0,
        'media_type': 'Unknown',
        'tmdb_id': 0
    }

    # Retrieve attributes with logging & fallback
    info['id'] = safe_getattr(item, 'id', default='Unknown', label="Media item has no id")
    info['video_bitrate'] = safe_getattr(item, 'bitrate', default=0, label="Media item has no bitrate")
    info['video_codec'] = safe_getattr(item, 'videoCodec', default='Unknown', label="Media item has no videoCodec")
    info['video_resolution'] = safe_getattr(item, 'videoResolution', default='Unknown', label="Media item has no videoResolution")
    info['video_height'] = safe_getattr(item, 'height', default=0, label="Media item has no height")
    info['video_width'] = safe_getattr(item, 'width', default=0, label="Media item has no width")
    info['video_duration'] = safe_getattr(item, 'duration', default=0, label="Media item has no duration")
    info['audio_codec'] = safe_getattr(item, 'audioCodec', default='Unknown', label="Media item has no audioCodec")
    info['media_type'] = item_metadata.get('media_type', 'Unknown') if item_metadata else 'Unknown'
    info['tmdb_id'] = item_metadata.get('tmdb_id', 0) if item_metadata else 0

    # Get Audio Channels
    try:
        for part in item.parts:
            for stream in part.audioStreams():
                if stream.channels:
                    log.debug(f"Added {stream.channels} channels for {stream.title if stream.title else 'Unknown'} audioStream", extra=log_tz)
                    info['audio_channels'] += stream.channels
        if info['audio_channels'] == 0:
            info['audio_channels'] = item.audioChannels if item.audioChannels else 0

    except AttributeError:
        log.debug("Media item has no audioChannels", extra=log_tz)

    # Check if media has multiple parts (e.g. CD1/CD2)
    if len(item.parts) > 1:
        info['multipart'] = True

    # If multiple parts, loop through each part to extract path/size/existence
    for part in item.parts:
        info['file'].append(part.file)
        shortenedFilePath = os.path.join('/', *info['file'][0].split('/')[-3:])
        info['file_short'].append(shortenedFilePath)
        info['file_size'] += part.size if part.size else 0

        if cfg['RUNTIME']['FIND_UNAVAILABLE'] and not part.exists:
            info['file_exists'] = False

        if cfg['RUNTIME']['FIND_EXTRA_TS']:
            name, ext = os.path.splitext(part.file)
            ext = ext.lower()
            if ext in info['file_exts']:
                info['file_exts'][ext] += 1
            else:
                info['file_exts'][ext] = 1

    return info


def delete_item(show_key, media_id, file_size, file_path):
    """
    Send a DELETE request to the Plex API to remove a media item by ID.
    Honors DRY_RUN config for previewing deletions.
    Updates global counters for reporting.
    """

    # Use local helper to convert bytes into a human-readable string
    file_size_str = bytes_to_string(file_size)

    # Construct the URL used to send the DELETE request
    delete_url = urljoin(cfg['PLEX']['SERVER_URL'], '%s/media/%d' % (show_key, media_id))
    log.debug("Sending DELETE request to %r" % delete_url, extra=log_tz)

    # Define success log message formatting
    def log_deletion_result(prefix, emoji):
        print(f"{emoji} {prefix} {file_path!r}, Size: {file_size_str}")
        log.info(f"{emoji} {prefix} file: {file_path!r}, Size: {file_size_str}", extra=log_tz)

    # Track Deletions for Summary
    def track_deletion(file_size):
        global total_deleted_files, total_deleted_size
        total_deleted_files += 1
        total_deleted_size += file_size
    
    if cfg['RUNTIME']['DRY_RUN']:
        # Simulate deletion without sending API request
        track_deletion(file_size)
        log_deletion_result("Would've deleted media item (DRY RUN):", "\t\tDRY RUN -- ✨")
    else:
        # Perform actual deletion
        response = requests.delete(delete_url, headers={'X-Plex-Token': cfg['PLEX']['AUTH_TOKEN']})
        if response.status_code == 200:
            track_deletion(file_size)
            log_deletion_result("Successfully deleted", "✨")
        else:
            log_deletion_result("Deletion failed", "⚠️")
            

############################################################
# MISC METHODS
# Utility functions for logging decisions, filtering skipped items,
# and converting numerical values (milliseconds, bytes, kbps)
# into human-readable formats.
############################################################

decision_filename = os.path.join(os.path.dirname(os.path.realpath(sys.argv[0])), 'decisions.log')

def safe_getattr(obj, attr, default=None, label=None):
    """
    Safely get an attribute from an object with fallback.
    Logs a debug message if the attribute is missing.

    Args:
        obj: The object to fetch from
        attr (str): Attribute name
        default: Value to return if not found
        label (str): Optional log label (e.g. "Media item has no bitrate")

    Returns:
        The attribute value or the default
    """
    try:
        return getattr(obj, attr)
    except AttributeError:
        if label:
            log.debug(label, extra=log_tz)
        else:
            log.debug("Missing attribute: %s", attr, extra=log_tz)
        return default


def write_decision(title=None, keeping=None, removed=None):
    """
    Log the decision-making process of what to keep vs remove.
    Appends information to a log file for post-review.
    """
    lines = []
    if title:
        lines.append('\nTitle    : %s\n' % title)
    if keeping:
        lines.append('\tKeeping (%d): %r\n' % (keeping['score'], keeping))
    if removed:
        lines.append('\tRemoving (%d): %r\n' % (removed['score'], removed))

    with open(decision_filename, 'a') as fp:
        fp.writelines(lines)
    return


def is_skip_list(files):
    """
    Determine whether any file path in the given list
    matches any item in the skip list defined in config.
    """
    return any(skip_item in str(files_item) for files_item, skip_item in itertools.product(files, cfg['SKIP_LIST']))


def should_skip_deletion(media_id, part_info, context, check_file_size=True):
    """
    Checks if a media item should be skipped from deletion based on:
    - File size exists (even if Plex reports as unavailable)
    - File path matches an item in SKIP_LIST
    
    Logs the reason for skipping based on the provided context.

    Args:
        media_id (str): The media item ID for logging
        part_info (dict): The parsed metadata for the part
        context (str): A label for which section this check is from (e.g. 'UNAVAILABLE', 'EXTRA_TS')
        check_file_size (bool): If True, skip if file_size > 0

    Returns:
        bool: True if the item should be skipped
    """
    # Check if size suggests it's still valid
    if check_file_size and part_info['file_size'] > 0:
        log.info(f"[{context}] Skipping removal due to non-zero file size (%r): %r - %r",
                 part_info['file_size'], media_id, part_info['file_short'], extra=log_tz)
        print(f"\t[{context}] Skipping removal due to file size > 0: %r - %r" %
              (media_id, part_info['file_short']))
        return True

    # Check if file path is in skip list
    if should_skip(part_info['file']):
        log.info(f"[{context}] Skipping removal per SKIP_LIST: %r - %r",
                 media_id, part_info['file_short'], extra=log_tz)
        print(f"\t[{context}] Skipping removal per SKIP_LIST: %r - %r" %
              (media_id, part_info['file_short']))
        return True

    return False


def millis_to_string(millis):
    """
    Convert milliseconds to HH:MM:SS format for human-readable display.
    Reference: https://stackoverflow.com/a/35990338
    """
    try:
        seconds = (millis / 1000) % 60
        seconds = int(seconds)
        minutes = (millis / (1000 * 60)) % 60
        minutes = int(minutes)
        hours = (millis / (1000 * 60 * 60)) % 24
        return "%02d:%02d:%02d" % (hours, minutes, seconds)
    except Exception:
        log.exception(f"Exception occurred converting {millis} millis to readable string: ")
    return "%d milliseconds" % millis


def bytes_to_string(size_bytes):
    """
    Convert a number of bytes into a human-readable string
    (e.g. 1.4 GB, 512 MB).
    Reference: https://stackoverflow.com/a/6547474
    """
    try:
        if size_bytes == 1:
            return "1 byte"
        suffixes_table = [('bytes', 0), ('KB', 0), ('MB', 1), ('GB', 2), ('TB', 2), ('PB', 2)]

        num = float(size_bytes)
        for suffix, precision in suffixes_table:
            if num < 1024.0:
                break
            num /= 1024.0
        if precision == 0:
            formatted_size = "%d" % num
        else:
            formatted_size = str(round(num, ndigits=precision))
        return f"{formatted_size} {suffix}"
    except Exception:
        log.exception(f"Exception occurred converting {size_bytes} bytes to readable string: ")

    return "%d bytes" % size_bytes


def kbps_to_string(size_kbps):
    """
    Convert kilobits per second to Kbps or Mbps as appropriate.
    """
    try:
        if size_kbps < 1024:
            return "%d Kbps" % size_kbps
        else:
            return "{:.2f} Mbps".format(size_kbps / 1024.)
    except Exception:
        log.exception(f"Exception occurred converting {size_kbps} Kbps to readable string: ")
    return "%d Bbps" % size_kbps


def build_tabulated(parts, items, arr_override_id=None):
    """
    Build a tabular representation of duplicates for user-friendly CLI display.
    Headers and layout adapt based on config flags.

    Args:
        parts (dict): Dictionary containing metadata about each part (media version).
        items (dict): Mapping of numeric choices to media part IDs.

    Returns:
        headers (list): Column headers.
        part_data (list of lists): Tabulated row data.
    """
    
    headers = ['choice', 'score', 'id', 'file', 'size', 'duration', 'bitrate', 'resolution', 'codecs']
    if cfg['RUNTIME']['FIND_DUPLICATE_FILEPATHS_ONLY']:
        headers.remove('score')

    part_data = []

    for choice, item_id in items.items():
        # add to part_data
        tmp = []
        for k in headers:
            if 'choice' in k:
                tmp.append(choice)
            elif 'score' in k:
                tmp.append(str(format(parts[item_id][k], ',d')))
            elif 'id' in k:
                id_val = str(parts[item_id][k])
                if item_id == arr_override_id and cfg['SCORING']['RADARR']['enabled']:
                    id_val += " ⭐"
                tmp.append(id_val)
            elif 'file' in k:
                tmp.append(parts[item_id]['file_short'])
            elif 'size' in k:
                tmp.append(bytes_to_string(parts[item_id]['file_size']))
            elif 'duration' in k:
                tmp.append(millis_to_string(parts[item_id]['video_duration']))
            elif 'bitrate' in k:
                tmp.append(kbps_to_string(parts[item_id]['video_bitrate']))
            elif 'resolution' in k:
                tmp.append("%s (%d x %d)" % (parts[item_id]['video_resolution'], parts[item_id]['video_width'], parts[item_id]['video_height']))
            elif 'codecs' in k:
                tmp.append("%s, %s x %d" % (parts[item_id]['video_codec'], parts[item_id]['audio_codec'], parts[item_id]['audio_channels']))
        part_data.append(tmp)

    return headers, part_data

def get_radarr_file(tmdb_id):
    """Fetch the file name from Radarr using the TMDB ID."""
    radarr_url = cfg['SCORING']['RADARR']['url']
    api_key = cfg['SCORING']['RADARR']['api_key']
    
    headers = {"X-Api-Key": api_key}
    params = {"tmdbId": tmdb_id}
    
    response = requests.get(f"{radarr_url}/api/v3/movie", headers=headers, params=params)
    if response.status_code == 200:
        movies = response.json()
        if movies:
            movie = movies[0]  # Assume the first match is correct
            if "movieFile" in movie and "relativePath" in movie["movieFile"]:
                return movie["movieFile"]["relativePath"]  # Extract just the filename
    return None

def get_sonarr_file(tvdb_id):
    """Fetch the file name from Sonarr using the TVDB ID."""
    sonarr_url = cfg['SCORING']['SONARR']['url']
    api_key = cfg['SCORING']['SONARR']['api_key']
    
    headers = {"X-Api-Key": api_key}
    params = {"tvdbId": tvdb_id}
    
    response = requests.get(f"{sonarr_url}/api/v3/series", headers=headers, params=params)
    if response.status_code == 200:
        series_list = response.json()
        if series_list:
            series = series_list[0]  # Assume first result is correct
            if "episodeFile" in series and "relativePath" in series["episodeFile"]:
                return series["episodeFile"]["relativePath"]
    return None


def get_arr_override_id(parts):
    """
    Return the media_id of the part matching a *arr (Radarr/Sonarr) preferred file.
    Only used if *arr integration is enabled in config.
    """
    for media_id, part_info in parts.items():
        if part_info['media_type'] == 'movie' and cfg['SCORING']['RADARR']['enabled']:
            # Movie/Radarr Logic
            tmdb_id = part_info['tmdb_id']
            if tmdb_id:
                radarr_file = get_radarr_file(tmdb_id)
                if radarr_file and os.path.basename(part_info['file'][0]) == radarr_file:
                    log.info(f"Radarr override matched file: {radarr_file}", extra=log_tz)
                    return media_id
        elif part_info['media_type'] == 'episode' and cfg['SCORING']['SONARR']['enabled']:
            # TV/Sonarr Logic
            tvdb_id = part_info['tvdb_id']
            if tvdb_id:
                sonarr_file = get_sonarr_file(tvdb_id)
                if sonarr_file and os.path.basename(part_info['file'][0]) == sonarr_file:
                    log.info(f"Sonarr override matched file: {sonarr_file}", extra=log_tz)
                    return media_id
    return None


############################################################
# MAIN
# Entry point for the script. Handles argument parsing,
# library section iteration, scoring, deletion logic,
# and outputs summary results.
############################################################


if __name__ == "__main__":
    print("""
       _                 _                   __ _           _
 _ __ | | _____  __   __| |_   _ _ __   ___ / _(_)_ __   __| | ___ _ __
| '_ \| |/ _ \ \/ /  / _` | | | | '_ \ / _ \ |_| | '_ \ / _` |/ _ \ '__|
| |_) | |  __/>  <  | (_| | |_| | |_) |  __/  _| | | | | (_| |  __/ |
| .__/|_|\___/_/\_\  \__,_|\__,_| .__/ \___|_| |_|_| |_|\__,_|\___|_|
|_|                             |_|

#########################################################################
# Maintainer:    hellblazer315                                          #
# Current URL:    https://github.com/hellblazer315/plex_dupefinder      #
# Original Author:   l3uddz                                             #
# Source URL:      https://github.com/l3uddz/plex_dupefinder            #
# --                                                                    #
#         Part of the Cloudbox project: https://cloudbox.works          #
#########################################################################
#                   GNU General Public License v3.0                     #
#########################################################################
""")
    print("Initialized")
    # Process command-line arguments
    # Create argument parser
    parser = argparse.ArgumentParser(
        description='Find duplicate media in your Plex Library and remove lowest-rated (based on user-specified scoring) versions.')
    # Add arguments
    parser.add_argument('--dry-run', action='store_true', help='Simulate deletions without making actual changes. Temporarily sets DRY_RUN.')
    parser.add_argument('--skip-other-dupes', action='store_true', help='Skip interactive or automatic deletion logic for other dupes. Temporarily sets SKIP_OTHER_DUPES')
    # Parse the arguments
    args = parser.parse_args()
    # Override config values if arguments passed
    if args.dry_run:
        cfg['RUNTIME']['DRY_RUN'] = True
    if args.skip_other_dupes:
        cfg['RUNTIME']['SKIP_OTHER_DUPES'] = True

    process_later = {} # Queue of media items to decide on after scanning

    # Process Sections/Libraries
    print("Finding dupes...")
    for section in cfg['PLEX']['LIBRARIES']:
        dupes = get_dupes(section)
        print("Found %d dupes for section %r" % (len(dupes), section))
        # Loop over duplicates in section
        for item in dupes:
            # Compose friendly title string for logging
            if item.type == 'episode':
                # Handle occasional "None" index value
                if item.index is None:
                    title = "%s - %s - %s" % (item.grandparentTitle, item.seasonEpisode, item.title)
                else:
                    title = "%s - %02dx%02d - %s" % (item.grandparentTitle, int(item.parentIndex), int(item.index), item.title)
            elif item.type == 'movie':
                title = item.title
            else:
                title = 'Unknown'

            log.info("Processing: %r", title, extra=log_tz)

            # If configured, revalidate media file existence and log the latest status for debugging
            if cfg['RUNTIME']['FIND_UNAVAILABLE']:
                if all(part.exists for media in item.media for part in media.parts):
                    # If all files are already marked as available, log it and move on
                    log.debug("All media is available for %s", item.title, extra=log_tz)
                else:
                    # If any files are marked unavailable, tell Plex to recheck to verify
                    log.debug("Reloading %s", item.title, extra=log_tz)
                    item.reload(timeout=90)  # Force a Plex scan to confirm status
                media = parts = {}
                for media in item.media:
                    for part in media.parts:
                        log.debug("%r,%r -- %s exists = %s; size = %s", media.id, part.id, part.file, part.exists, part.size, extra=log_tz)
                            
            # Loop through returned parts for media item (copy 1, copy 2...)
            item_metadata = get_item_metadata(item)
            parts = {}
            for part in item.media:
                # Extract metadata and file info from media part
                part_info = get_media_info(part, item_metadata)
                
                # Skip Plex-optimized versions
                if part.isOptimizedVersion:
                    log.info("ID: %r (%r) -- Skipping optimized version", part.id, part_info['file_short'], extra=log_tz)
                    print("ID: %r (%r) -- Skipping optimized version" % (part.id, part_info['file_short']))
                    continue
                elif cfg['RUNTIME']['SKIP_PLEX_VERSIONS_FOLDER'] and any("\\Plex Versions\\" in file_path for file_path in part_info['file']):
                    # Skip is "isOptimizedVersion" is not set but file is in a "\\Plex Versions\\" folder
                    log.info("ID: %r (%r) -- Skipping Plex Versions; isOptimizedVersion = %r", part.id, part_info['file_short'], part.isOptimizedVersion, extra=log_tz)
                    print("ID: %r (%r) -- Skipping Plex Versions; isOptimizedVersion = %r" % (part.id, part_info['file_short'], part.isOptimizedVersion))
                    continue
                
                # Log all other instances in case troubleshooting is needed                
                log.debug("ID: %r (%r) -- Including; isOptimizedVersion = %r", part.id, part_info['file_short'], part.isOptimizedVersion, extra=log_tz)
                                
                # Score media if not just matching file paths
                if not cfg['RUNTIME']['FIND_DUPLICATE_FILEPATHS_ONLY']:
                    part_info['score'] = get_score(part_info)
                    
                # Store the Plex key needed for deletion
                part_info['show_key'] = item.key
                
                # Log summary of metadata and score for this media file
                log.info("ID: %r - Score: %s - Meta:\n%r", part.id, part_info.get('score', 'N/A'), part_info, extra=log_tz)
                
                # Add the part to our collection of candidates to evaluate
                parts[part.id] = part_info

            # If more than one file remains, add it to the queue for later processing
            if len(parts) > 1:
                process_later[title] = parts
            else:
                # If only one file remains, skip it
                log.info("No duplicates after ignoring optimized versions for : %r", item.title, extra=log_tz)
                print("No duplicates after ignoring optimized versions for : %r" % item.title)

    # Define global variables for deletion stats
    total_deleted_files = 0
    total_deleted_size = 0

    ############################################################
    # PROCESS_DUPLICATES
    # For each media item queued in process_later:
    # - Handle missing files (FIND_UNAVAILABLE)
    # - Handle .ts files if unnecessary (FIND_EXTRA_TS)
    # - Score and select the best version to keep
    # - Offer interactive or automatic deletion
    ############################################################

    try:
        for item, parts in process_later.items():
            # Remove all unavailable media that are not in SKIP_LIST
            if cfg['RUNTIME']['FIND_UNAVAILABLE']:
                title_decided = False
                for media_id, part_info in parts.items():
                    if not part_info['file_exists']:
                        if not title_decided:
                            title_decided = True
                            write_decision(title=item)

                        if should_skip_deletion(media_id, part_info, context="UNAVAILABLE"):
                            continue
                        
                        # Delete the media part
                        log.info("Removing unavailable media : %r - %r (size: %r)",
                                media_id, part_info['file_short'], part_info['file_size'], extra=log_tz)
                        print("Removing unavailable media : %r - %r (size: %r)" %
                            (media_id, part_info['file_short'], part_info['file_size']))
                        delete_item(part_info['show_key'], media_id, part_info['file_size'], part_info['file_short'])
                        write_decision(removed=part_info)
                        time.sleep(2)

            # Additional cleanup logic for .ts files (if configured)
            if cfg['RUNTIME']['FIND_EXTRA_TS']:
                title_decided = False
                file_exts = {}
                for media_id, part_info in parts.items():
                    for k,v in part_info['file_exts'].items():
                        file_exts[k] = file_exts.get(k, 0) + v

                # Only remove .ts files if there's at least one other type present
                if len(file_exts) > 1 and ".ts" in file_exts:
                    for media_id, part_info in parts.items():
                        if ".ts" in part_info['file_exts']:
                            if not title_decided:
                                title_decided = True
                                write_decision(title=item)
                            
                            # Skip if part contains multiple types
                            if len(part_info['file_exts']) != 1:
                                print("\tSkipping removal of %r as there is more than one file type that make up %r for %s."
                                    % (part_info['file_short'], media_id, item))
                                continue

                            if should_skip_deletion(media_id, part_info, context="EXTRA_TS", check_file_size=False):
                                continue

                            # Delete the TS files
                            log.info("Removing extra TS media : %r - %r", media_id, part_info['file_short'], extra=log_tz)
                            print("Removing extra TS media : %r - %r" % (media_id, part_info['file_short']))
                            delete_item(part_info['show_key'], media_id, part_info['file_size'], part_info['file_short'])
                            write_decision(removed=part_info)
                            time.sleep(2)

            if not cfg['RUNTIME']['SKIP_OTHER_DUPES']:

                ############################################################
                # DUPLICATE RESOLUTION
                # Decide which duplicate to keep and remove others
                # Based on config: interactive prompt or auto-delete
                ############################################################

                if not cfg['RUNTIME']['AUTO_DELETE']:
                    # Interactive/Manual Mode
                    partz = {}
                    print("\nWhich media item do you wish to keep for %r ?\n" % item)

                    if cfg['RUNTIME']['FIND_DUPLICATE_FILEPATHS_ONLY']:
                        sort_key = "id"
                        sort_order_reverse = False
                    else:
                        sort_key = "score"
                        sort_order_reverse = True

                    # Sort parts by score (or ID) and build choice map
                    media_items = {}
                    best_item = None
                    for pos, (media_id, part_info) in enumerate(collections.OrderedDict(
                            sorted(parts.items(), key=lambda x: x[1][sort_key], reverse=sort_order_reverse)).items(), start=1):

                        if pos == 1:
                            best_item = part_info  # Presume best item by score
                        media_items[pos] = media_id
                        partz[media_id] = part_info

                    arr_override_id = get_arr_override_id(parts)
                    headers, data = build_tabulated(partz, media_items, arr_override_id)
                    print(tabulate(data, headers=headers))

                    # Prompt user for selection
                    prompt_msg = "\nChoose item to keep (0 or s = skip | 1 or b = best"
                    if arr_override_id:
                        if (item.type == 'movie' and cfg['SCORING']['RADARR']['enabled']) or \
                        (item.type == 'episode' and cfg['SCORING']['SONARR']['enabled']):
                            prompt_msg += " | r = *arr preferred"
                    prompt_msg += "): "

                    keep_item = input(prompt_msg).lower().strip()   
                    if (keep_item != 's') and (keep_item == 'b' or keep_item == 'r' or 0 < int(keep_item) <= len(media_items)):
                        # Process if either "r", "b" or a valid 'id' input
                        write_decision(title=item)
                        for media_id, part_info in parts.items():
                            if keep_item == 'r' and arr_override_id and media_id == arr_override_id:
                                # Use *arr preferred if "r" input and a *arr override exists
                                print("\tKeeping (%d - *arr preferred): %r" % (part_info['score'], media_id))
                                write_decision(keeping=part_info)
                            elif keep_item == 'b' and best_item == part_info:
                                # Keep best item if "b" input
                                print("\tKeeping (%d - best score): %r" % (part_info['score'], media_id))
                                write_decision(keeping=part_info)
                            elif keep_item not in ['b', 'r'] and media_id == media_items[int(keep_item)]:
                                # Keep user specified 'id'
                                print("\tKeeping (%d - manual): %r" % (part_info['score'], media_id))
                                write_decision(keeping=part_info)
                            else:
                                # Remove any other part
                                print("\tRemoving (%d): %r" % (part_info['score'], media_id))
                                delete_item(part_info['show_key'], media_id, part_info['file_size'], part_info['file_short'])
                                write_decision(removed=part_info)
                                time.sleep(2)
                    elif keep_item == 's' or int(keep_item) == 0:
                        # Skip item if "s" input
                        print("Skipping deletion(s) for %r" % item)
                    else:
                        print("Unexpected response, skipping deletion(s) for %r" % item)
                else:
                    # Auto-Delete Mode
                    print("\nDetermining best media item to keep for %r ..." % item)
                    keep_score = 0
                    keep_id = None

                    # Determine best item
                    if cfg['RUNTIME']['FIND_DUPLICATE_FILEPATHS_ONLY']:
                        # Decide best item by lowest ID (file path match)
                        for media_id, part_info in parts.items():
                            if keep_score == 0 or int(part_info['id']) < keep_score:
                                keep_score = int(part_info['id'])
                                keep_id = media_id
                    else:
                        if cfg['SCORING']['RADARR']['enabled'] or cfg['SCORING']['SONARR']['enabled']:
                            # Decide best item by Radarr/Sonarr
                            arr_override_id = get_arr_override_id(parts)
                            if arr_override_id:
                                keep_id = arr_override_id
                                if item.type == 'movie':
                                    arr="Radarr"
                                elif item.type == 'episode':
                                    arr="Sonarr"
                                log.info("Auto-deleting using *arr override (%s): %s", arr, parts[keep_id]['file_short'], extra=log_tz)
                                print(f"🛑 Auto-selected using {arr} override: {parts[keep_id]['file_short']}")
                            else:
                                log.info("No *arr override found, using score-based selection", extra=log_tz)
                        else:
                            log.info("*arr override disabled; using score-based selection", extra=log_tz)

                        # Decide best item by score if *arr not used or found
                        if not keep_id:
                            for media_id, part_info in parts.items():
                                if int(part_info['score']) > keep_score:
                                    keep_score = part_info['score']
                                    keep_id = media_id

                    if keep_id:
                        # Delete other items
                        write_decision(title=item)
                        for media_id, part_info in parts.items():
                            formatted_score = '{:,}'.format(part_info['score'])
                            if media_id == keep_id:
                                print("✅%s🔺 %s 🆔%d" % (formatted_score, part_info['file_short'], media_id))
                                write_decision(keeping=part_info)
                            else:
                                print("\tRemoving : %r - %r" % (media_id, part_info['file']))
                                if is_skip_list(part_info['file']):
                                    print("☑️%s🔺 %s 🆔%d" % (formatted_score, part_info['file_short'], media_id))
                                else:
                                    print("❌%s🔺 %s 🆔%d" % (formatted_score, part_info['file_short'], media_id))
                                    delete_item(part_info['show_key'], media_id, part_info['file_size'], part_info['file_short'])
                                    write_decision(removed=part_info)
                                    time.sleep(2)
                    else:
                        print("Unable to determine best media item to keep for %r", item)
    except KeyboardInterrupt:
        print("\n⛔️ Process interrupted by user.")
    finally:
        # Print/log final stats
        total_deleted_size_gb = total_deleted_size / (1024 * 1024 * 1024)
        print("Total Deleted Files:", total_deleted_files)
        log.info("Total Deleted Files: %r", total_deleted_files, extra=log_tz)
        print("Total Deleted Size (GB): {:.2f}".format(total_deleted_size_gb))
        log.info("Total Deleted Size (GB): {:.2f}".format(total_deleted_size_gb), extra=log_tz)

