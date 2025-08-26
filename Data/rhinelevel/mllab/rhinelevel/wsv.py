"""
This module provides a programming interface for the river levels dataset.

The dataset contains measurements of river levels from 15 stations in Germany.
The stations are selected by starting at the river Rhine in Bonn, and then 
follow the Rhine upstream including tributary rivers.

Each measurement consists of a timestamp and a river level in cm. The time range
is from around the year 1980 to 2013 in 10 minute intervals.

Source for the data:

    Wasserstraßen- und Schifffahrtsverwaltung des Bundes (WSV),
    bereitgestellt durch die Bundesanstalt für Gewässerkunde (BfG).

which translates to

    German Federal Waterways and Shipping Administration (WSV),
    provided by the German Federal Institute of Hydrology (BfG).
"""
import tarfile
import tempfile
import re
import json
from collections import namedtuple
from datetime import datetime, date, timedelta
import os.path as path
import os

from pytz import timezone
from dateutil.parser import parse as parse_date, isoparse
import requests

from .units import GeographicPosition, KiloMeter, Meter, River

import pandas as pd
import numpy as np

_TZ_CET = timezone('CET')

_TZ_UTC = timezone('UTC')

StationInfo = namedtuple('StationInfo', ['name', 'river', 'river_km', 'pos', 'zero'])
StationInfo.name.__doc__ = "Name of the station."
StationInfo.river.__doc__ = "At which river is the station, element of wsv.River enum."
StationInfo.river_km.__doc__ = "Kilometers until outlet of the river, except for river Rhine."
StationInfo.pos.__doc__ = "Geographic position of the station (longitudal, latidudal)."
StationInfo.zero.__doc__ = "Zero point of the station relative to  sea level (Normalhöhennull)."


# See https://www.pegelonline.wsv.de/gast/karte/standard
_STATIONS_META_INFORMATION = {
    # https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr=27100400
    'Andernach': StationInfo('Andernach', River.rhine, KiloMeter(613.78), 
        GeographicPosition.conv(3385857.77, 5590961.77, "EPSG:31467"), Meter(51.49)),
    # https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr=25300200
    'Bingen': StationInfo('Bingen', River.rhine, KiloMeter(528.36),
        GeographicPosition.conv(3421137.65, 5537688.05, "EPSG:31467"), Meter(76.19)),
    # https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr=2710080
    'Bonn': StationInfo('Bonn', River.rhine, KiloMeter(654.80), 
        GeographicPosition.conv(3366516.59, 5624027.69, "EPSG:31467"), Meter(42.69)),
    # https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr=24700404
    'Frankfurt-Osthafen': StationInfo('Frankfurt Osthafen', River.main, KiloMeter(37.59), 
        GeographicPosition.conv(3479685.59, 5552206.97, "EPSG:31467"), Meter(90.64)),
    # https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr=25800600
    'Kalkofen-Neu': StationInfo('Kalkofen Neu', River.lahn, KiloMeter(106.40), 
        GeographicPosition.conv(3421009.36, 5576352.48, "EPSG:31467"), Meter(86.40)),
    # https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr=25700100
    'Kaub': StationInfo('Kaub', River.rhine, KiloMeter(546.30),
        GeographicPosition.conv(3411685.58, 5550640.41, "EPSG:31467"), Meter(67.68)),
    # https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr=25900700
    'Koblenz-Rh': StationInfo('Koblenz', River.rhine, KiloMeter(591.49),
        GeographicPosition.conv(3400787.86, 5581229.69, "EPSG:31467"), Meter(57.68)),
    # https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr=26900900
    'Koblenz-UP-Mosel': StationInfo('Koblenz Up', River.moselle, KiloMeter(1.91),
        GeographicPosition.conv(3399454.00, 5582094.00, "EPSG:31467"), Meter(58.01)),
    # https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr=25100100
    'Mainz': StationInfo('Mainz', River.rhine, KiloMeter(498.27),
        GeographicPosition.conv(3448122.43, 5541102.74, "EPSG:31467"), Meter(78.38)),
    # https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr=27100700
    'Oberwinter': StationInfo('Oberwinter', River.rhine, KiloMeter(638.19),
        GeographicPosition.conv(3373702.17, 5608786.43, "EPSG:31467"), Meter(47.20)),
    # https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr=25100300
    'Oestrich': StationInfo('Oestrich', River.rhine, KiloMeter(518.08),
        GeographicPosition.conv(3430537.71, 5541176.27, "EPSG:31467"), Meter(77.57)),
    # https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr=24900108
    'Raunheim': StationInfo('Raunheim', River.main, KiloMeter(12.21),
        GeographicPosition.conv(3460532.36, 5542349.81, "EPSG:31467"), Meter(82.90)),
    # https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr=23800690
    'Rockenau-SKA': StationInfo('Rockenau Ska', River.neckar, KiloMeter(60.70),
        GeographicPosition.conv(3500439.43, 5477927.75, "EPSG:31467"), Meter(119.73)),
    # https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr=23700600
    'Speyer': StationInfo('Speyer', River.rhine, KiloMeter(400.60),
        GeographicPosition.conv(459939.00, 5463599.00, "EPSG:25832"), Meter(88.51)),
    # https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr=23900200
    'Worms': StationInfo('Worms', River.rhine, KiloMeter(443.40),
        GeographicPosition.conv(455049.00, 5497884.00, "EPSG:25832"), Meter(84.16)),
}


class LevelData:
    __slots__ = ['time', 'level']

    def __init__(self, time, level):
        self.time = time
        self.level = level

    @classmethod
    def parse(cls, line, tzinfo, invalid_marker):
        time, level = line.split(b' ', 1)
        level = float(level)
        if level == invalid_marker:
            level = None

        year = int(time[:4])
        month = int(time[4:6])
        day = int(time[6:8])
        hour = int(time[8:10])
        minute = int(time[10:12])
        second = int(time[12:14])
        if hour == 24:
            # The dataset contains the time 24:00:00
            # Python does need this as 0:00:00 of the next day.
            add = timedelta(days=1)
            hour = 0
        else:
            add = timedelta()
        time = datetime(year, month, day, hour, minute, second, tzinfo=tzinfo) + add
        time = time.astimezone(_TZ_UTC)
        return cls(time, level)

    def __str__(self):
        return repr(self)

    def __repr__(self):
        level = 'level was marked invalid' if self.level is None else '{}cm'.format(self.level)
        return "LevelData({}, {})".format(self.time, level)


class StationData:
    max_line_len = 22
    min_line_len = 10

    def __init__(self, meta, file, nbytes):
        self.meta = meta
        self._file = file
        self._nbytes = nbytes
        self._range = None
        self._read_meta()

    def recent(self):
        """Download and return the recent levels for this station.

        Data from the last 30 days is returned in 15 minute intervals."""
        url = "https://www.pegelonline.wsv.de/webservices/" \
              "rest-api/v2/stations/{}/W/measurements.json?start=P31D".format(self.id)
        measurements = json.loads(requests.get(url).text)
        for measurement in measurements:
            time = isoparse(measurement['timestamp'])
            level = Meter(measurement['value'])
            yield LevelData(time, level)

    def to_frame(self, recent=False):
        """Return a Pandas DataFrame of all measured levels for this station."""
        levels = []
        times = []

        data = self.recent() if recent else self.data()
        for measurement in data:
            levels.append(measurement.level)
            times.append(measurement.time)

        return pd.DataFrame(data={'level': levels, 'time': times})

    def data(self, begin=None, end=None):
        """Return an inclusive range of the data set for this station.
        The measurements are in 15 minute intervals.

        The arguments `begin' and `range' can be
          - a string describing a time or date
          - a date or datetime object
          - None (no restrictions apply in this case for beginning or end)

        If a date is given, the time 0:00 is used.
        """
        begin = self._normalize_range_limit(begin)
        end = self._normalize_range_limit(end)
        
        if begin is None:
            begin_byte = self.data_offset
        else:
            begin_byte = self._bin_search(begin, after=False)

        if end is None:
            end_byte = self._nbytes
        else:
            end_byte = self._bin_search(end, after=True)

        self._file.seek(begin_byte)
        lines = self._file.read(end_byte - begin_byte).split(b'\r\n')
        for line in lines:
            if line:
                yield LevelData.parse(line, self.tzinfo, self.invalid_marker)

    def statistics(self):
        data = self.data()
        first = next(data).level
        lmin = first
        lmax = first
        prev = first
        mean = first
        diff = 0
        none_count = 0

        valid = 0
        invalid = 0
        for data in data:
            lev = data.level
            if lev is None:
                invalid += 1
                none_count += 1
            else:
                valid += 1
                lmin = min(lmin, lev)
                lmax = max(lmax, lev)
                if none_count <= 2: # < 30min gap
                    dist = abs(lev - prev)
                    if dist > diff:
                        diff = dist
                none_count = 0
                prev = lev

                n = float(valid)
                mean = (n - 1) / n * mean + lev / n

        return {
            'min': lmin,
            'max': lmax,
            'max-increase': diff,
            'mean': mean,
            'n_valid': valid,
            'n_invalid': invalid,
        }

    @property
    def time_range(self):
        """Timespan of the data. First measurement and last measurement time."""
        if self._range is None:
            self._file.seek(0)
            for line in self._file:
                start = line.strip()
                if not start.startswith(b'#'):
                    break
            
            self._file.seek(-50, 2)
            last_lines = self._file.read().strip()
            end = last_lines[last_lines.rfind(b'\n')+1:]
            self._range = (
                LevelData.parse(start, self.tzinfo, self.invalid_marker).time,
                LevelData.parse(end, self.tzinfo, self.invalid_marker).time)
        return self._range

    def _bin_search(self, goal, after):
        low = self.data_offset
        high = self._nbytes

        while high - low > self.min_line_len:
            mid = (high + low) // 2

            self._file.seek(mid - self.max_line_len)
            window = self._file.read(3 * self.max_line_len)
            begin_pos = window[:self.max_line_len].rfind(b'\n') + 1
            end_pos = self.max_line_len + window[self.max_line_len:].find(b'\n') - 1
            line = window[begin_pos:end_pos]
            time = LevelData.parse(line, self.tzinfo, self.invalid_marker).time
            if time < goal:
                low = mid
            elif time > goal:
                high = mid
            else:
                if after:
                    return mid - self.max_line_len + end_pos
                else:
                    return mid - self.max_line_len + begin_pos
                break

        if goal < time:
            pos = self.data_offset
            if after:
                self._file.seek(pos)
                window = self._file.read(self.max_line_len)
                pos = pos - self.max_line_len + window.find('\r\n')
        else:
            pos = self._nbytes
            if not after:
                self._file.seek(pos - self.max_line_len)
                window = self._file.read(self.max_line_len)
                pos = pos - self.max_line_len + window.rfind('\n') - 1

        return pos


    def _normalize_range_limit(self, lim):
        if lim is None:
            return lim
        elif isinstance(lim, str):
            return self._normalize_range_limit(parse_date(lim))
        elif isinstance(lim, date):
            return datetime(lim.year, lim.month, lim.day, 0, 0, 0, tzinfo=_TZ_CET)
        elif isinstance(lim, datetime):
            if lim.tzinfo is None:
                lim = lim.replace(tzinfo=_TZ_CET)
            else:
                lim = lim.astimezone(_TZ_CET)
            
            lower_15min = (lim.minute // 15) * 15
            return lim.replace(minute=lower_15min, second=0, microsecond=0)
        else:
            raise ValueError("Unknown range limit, {} is no date".format(lim))

    def _read_meta(self):
        self._file.seek(0)
        self.tzinfo = None
        self.id = None
        self.data_offset = 0
        self.invalid_marker = None
        for line in self._file:
            if not line.startswith(b'#'):
                break
            self.data_offset += len(line)
            if self.tzinfo is not None and self.id is not None and self.invalid_marker is not None:
                continue
            for meta in line[1:].split(b'|*|'):
                if meta.startswith(b'TZ'):
                    if meta[2:] == b'MEZ':
                        self.tzinfo = _TZ_CET
                    else:
                        raise ValueError("Unknown timezone {}".format(meta[2:]))
                elif meta.startswith(b'SANR'):
                    self.id = int(meta[4:].decode('ascii'))
                elif meta.startswith(b'RINVAL'):
                    self.invalid_marker = float(meta[6:].decode('ascii'))

        if self.tzinfo is None:
            print("Warning: Assume for station {} that the timezone is CET".format(self.meta.name))
            self.tzinfo = _TZ_CET
        if self.id is None:
            raise ValueError("No station id could be found in the file for station {}".format(self.meta.name))


class CachedTarfile:
    def __init__(self, tar_path, cache_dir):
        cache_dir = path.join(cache_dir)
        if path.exists(cache_dir):
            extract = False
            assert path.isdir(cache_dir)
        else:
            extract = True
            os.makedirs(cache_dir)
        self.cache_dir = cache_dir
        self.tar_path = tar_path

        if extract or path.getmtime(tar_path) > path.getmtime(cache_dir):
            self._extract()
            os.utime(cache_dir)

    def files(self):
        return os.listdir(self.cache_dir)

    def open(self, file, mode='rb'):
        return open(path.join(self.cache_dir, file), mode)

    def size(self, file):
        return path.getsize(path.join(self.cache_dir, file))

    def _extract(self):
        print("Extract files...")
        tar = tarfile.open(self.tar_path, 'r:bz2')
        for member in iter(tar):
            print("  File {}".format(member.name))
            tar.extract(member, self.cache_dir)
        print("Done.")


class RiverLevelData:
    def __init__(self, dataset_path, cache_dir):
        self._dataset = CachedTarfile(dataset_path, path.join(cache_dir, 'waterlevels'))

        self._stations = {}
        for station in self._dataset.files():
            name = re.match(r'^(.*)-W15\.zrx$', station)[1]
            self._stations[station] = _STATIONS_META_INFORMATION[name]

    def stations(self):
        return list(sorted(self._stations.values(), key=lambda s: s.name))

    def station_data(self, name):
        if isinstance(name, StationInfo):
            name = str(name.name)
        if isinstance(name, str):
            station_info = None
            for file_name, station_info in self._stations.items():
                if station_info.name == name:
                    break
            if station_info is None:
                raise ValueError("Station with name '{}' not found".format(name))
        else:
            raise ValueError("Value {} must be the name of a station or a StationInfo instance.".format(name))

        file = self._dataset.open(file_name)
        size = self._dataset.size(file_name)
        return StationData(station_info, file, size)

    def to_frame(self, recent=False):
        """Return the full dataset as one Pandas DataFrame."""
        df = None
        for station_info in self.stations():
            print("Parse " + station_info.name)
            loc_df = self.station_data(station_info.name).to_frame(recent=recent)
            loc_df.rename(columns={'level': station_info.name}, inplace=True)
            if df is None:
                df = loc_df
            else:
                df = df.merge(loc_df, on='time', how='outer')
        if df is None:
            return None
        cols = df.columns.tolist()
        ti = cols.index('time')
        cols = [cols[ti]] + cols[:ti] + cols[ti+1:]
        df = df[cols]

        delta = df.time[1:].values - df.time[:-1].values
        # Check if all times are 15min apart
        assert np.sum(np.not_equal(delta, np.timedelta64(15, 'm'))) == 0
        df.set_index(pd.date_range(df['time'].iloc[0], df['time'].iloc[-1], freq='15min'), inplace=True)
        del df['time']

        return df
