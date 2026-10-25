#!/usr/bin/env python3
"""
Correct GPS drift in MAGENE C706 FIT files.

⚠️  PREFERRED APPROACH: Use GPX intermediary method instead.
   See: references/gpx-intermediary-method.md
   
   This script uses the old Decoder→Encoder approach which may lose
   metadata and cause Strava "There was an error processing your activity".
   It is kept here for reference only.

Usage:
    python3 correct_gps.py [input.fit] [output.fit] [lat_offset_deg] [lon_offset_deg]

Examples:
    # 05-30: subtract uniform offset to align start point
    python3 correct_gps.py input.fit corrected.fit -0.0109 -0.0065

Offset direction: value is SUBTRACTED from each point (positive = shift "down/left").
The offset signs are derived by: offset = ref_point - current_point.

Requires: garmin-fit-sdk, fitdecode
  venv: ~/.hermes/hermes-agent/venv/bin/python
"""
import os
import sys
from pathlib import Path
from garmin_fit_sdk import Decoder, Encoder
from garmin_fit_sdk.stream import Stream

HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
CACHE = HERMES_HOME / "cache" / "documents"


def _default_fit(name_hint: str) -> str:
    """在缓存目录里找一个匹配的 FIT（不写死任何设备序列号）。"""
    if CACHE.exists():
        for p in sorted(CACHE.glob(f"*{name_hint}*.fit")):
            return str(p)
    return str(CACHE / f"MAGENE_C706_{name_hint}.fit")


IN_PATH  = sys.argv[1] if len(sys.argv) > 1 else _default_fit("WGS84")
OUT_PATH = sys.argv[2] if len(sys.argv) > 2 else _default_fit("corrected")
OFF_LAT  = float(sys.argv[3]) if len(sys.argv) > 3 else -0.0109
OFF_LON  = float(sys.argv[4]) if len(sys.argv) > 4 else -0.0065

# ⚠️  CORRECT formula for semicircles (WGS84):
#     degrees = semicircles / (2**32 / 360)
#     semicircles = int(degrees * (2**32 / 360))
OFFSET_LAT_SC = int(OFF_LAT * (2**32 / 360))
OFFSET_LON_SC = int(OFF_LON * (2**32 / 360))

print(f"Offset: {OFFSET_LAT_SC} sc lat, {OFFSET_LON_SC} sc lon")
print(f"  = {OFF_LAT:.4f}° lat, {OFF_LON:.4f}° lon")

encoder = Encoder()
counts = {'total': 0, 'record': 0}

def on_message(mesg_num, mesg):
    counts['total'] += 1
    if mesg_num == 20:  # RECORD
        counts['record'] += 1
        lat = mesg.get('position_lat')
        lon = mesg.get('position_long')
        if lat is not None:
            mesg['position_lat'] = lat + OFFSET_LAT_SC
        if lon is not None:
            mesg['position_long'] = lon + OFFSET_LON_SC
    encoder.on_mesg(mesg_num, mesg)

stream = Stream.from_file(IN_PATH)
decoder = Decoder(stream)
decoder.read(
    apply_scale_and_offset=False,
    convert_types_to_strings=False,
    expand_sub_fields=False,
    expand_components=False,
    merge_heart_rates=False,
    mesg_listener=on_message,
)
encoder.close()
with open(OUT_PATH, 'wb') as f:
    f.write(bytes(encoder._output_stream._buffer))

import os
size = os.path.getsize(OUT_PATH)
print(f"\nWritten: {OUT_PATH} ({size:,} bytes)")
print(f"Messages: {counts['total']} total, {counts['record']} records")

# Verify with garmin_fit_sdk (fitdecode crashes on definition messages)
lons, lats = [], []
def verify(mesg_num, mesg):
    if mesg_num == 20:
        lat = mesg.get('position_lat')
        lon = mesg.get('position_long')
        if lat is not None: lats.append(lat)
        if lon is not None: lons.append(lon)
Stream.from_file(OUT_PATH)  # re-open
Stream.from_file(OUT_PATH)  # re-open
decoder2 = Decoder(Stream.from_file(OUT_PATH))
decoder2.read(mesg_listener=verify)
lons_d = [x / (2**32/360) for x in lons]
lats_d = [x / (2**32/360) for x in lats]
print(f"\nCorrected ranges:")
print(f"  lon: {min(lons_d):.4f}° ~ {max(lons_d):.4f}°")
print(f"  lat: {min(lats_d):.4f}° ~ {max(lats_d):.4f}°")