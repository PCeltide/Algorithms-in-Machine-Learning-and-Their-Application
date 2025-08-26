"""Module to conveniently access the FTP servers of the Deutscher Wetterdienst
(DWD).

An API is provided to download climate data and weather forecasts. For forecasts
MOSMIX is used, see
    https://www.dwd.de/EN/ourservices/met_application_mosmix/met_application_mosmix.html 
"""
from datetime import datetime, date, timedelta
import ftplib
import os
import os.path as path
import socket
import tempfile
import time
import threading
import zipfile
import hashlib
from urllib.request import urlopen
from urllib.parse import urlparse
from email.utils import parsedate, formatdate
import calendar
from collections import namedtuple


import requests
from cachecontrol.heuristics import BaseHeuristic
from dateutil.parser import parse as parse_date
from cachecontrol import CacheControl
from cachecontrol.caches.file_cache import FileCache
import pytz
from tqdm import tqdm
from bs4 import BeautifulSoup, FeatureNotFound

from .units import GeographicPosition, Meter, River


def _sizeof_fmt(num, suffix='B'):
    for unit in ['','K','M','G','T','P','E','Z']:
        if abs(num) < 1000.0:
            return "{:.1f}{}{}".format(num, unit, suffix)
        num /= 1000.0
    return "{:.1f}{}{}".format(num, 'Y', suffix)


class CachedFTP:
    """Accesses a FTP server as anonymous user and caches the downloaded files."""
    _TZ_GMT = pytz.timezone('GMT')

    def __init__(self, host, cache_dir):
        if path.exists(cache_dir):
            assert path.isdir(cache_dir)
        else:
            os.makedirs(cache_dir)
        self.cache_dir = cache_dir
        self.host = host
        self.ftp = ftplib.FTP(host)
        self.ftp.login()
        self._start_noop_thread()
    
    def ls(self, dirname=None):
        if dirname is None:
            return self.ftp.nlst()
        else:
            return self.ftp.nlst(dirname)
    
    def open(self, file_path, mode='rb'):
        cache_path = self._get_cache_path(file_path)
        cache_dir = path.dirname(cache_path)
        if not path.exists(cache_dir):
            os.makedirs(cache_dir)
        if not path.exists(cache_path) or self._cachefile_needs_update(cache_path, file_path):
            with open(cache_path, 'wb') as fp:
                self._download(file_path, fp)
        return open(cache_path, mode)

    def cd(self, dirname):
        self.ftp.cwd(dirname)
    
    def pwd(self):
        return self.ftp.pwd()
    
    def exists(self, file_path):
        return file_path in self.ftp.nlst(file_path)

    def close(self):
        if hasattr(self, '_noop_lock'):
            self._noop_lock.acquire()
            self._noop = False
            self._noop_lock.release()
            self._noop_thread.join()
        self.ftp.close()
    
    def _get_cache_path(self, file_path):
        ftp_pwd = self.ftp.pwd()
        ftp_path = path.normpath(path.join(ftp_pwd, file_path))
        if ftp_path.startswith('/'):
            ftp_path = ftp_path[1:]

        cache_path = path.join(self.cache_dir, ftp_path)
        return cache_path 
    
    def _download(self, file_path, fp):
        full_path = path.normpath(path.join(self.ftp.pwd(), file_path))
        self.ftp.sendcmd('TYPE I')
        size = self.ftp.size(file_path)
        if size is not None:
            print('Downloading {} ({})...'.format(full_path, _sizeof_fmt(size)))

            with tqdm(total=size, unit='B', unit_scale=True, leave=False) as pbar:
                
                def _handle(blk):
                    pbar.update(len(blk))
                    fp.write(blk)
                
                self.ftp.retrbinary('RETR {}'.format(file_path), _handle)
        else:
            print('Downloading {}...'.format(full_path))
            self.ftp.retrbinary('RETR {}'.format(file_path), fp.write)
    
    def _ftp_mod_time(self, file_path):
        resp = self.ftp.sendcmd('MDTM {}'.format(file_path))
        if not resp or resp[0] != '2':
            raise Exception('FTP command MDTM failed. Returned {}'.format(resp))
        
        time = resp[4:]
        year = int(time[:4])
        month = int(time[4:6])
        day = int(time[6:8])
        hour = int(time[8:10])
        minute = int(time[10:12])
        second = int(time[12:14])
        if len(time) > 14:
            ms = int(time[15:])
        else:
            ms = 0
        
        time = datetime(year, month, day, hour, minute, second, ms, tzinfo=self._TZ_GMT)
        return time.timestamp()
    
    def _cache_mod_time(self, file_path):
        try:
            return path.getmtime(file_path)
        except:
            return None
    
    def _cachefile_needs_update(self, cache_path, ftp_path):
        cache_ts = self._cache_mod_time(cache_path)
        if cache_ts is None:
            return True
        return cache_ts + 5 < self._ftp_mod_time(ftp_path)
        
    def _start_noop_thread(self, interval=5):
        self._noop = True
        self._noop_lock = threading.Lock()
        
        def run():
            lock = self._noop_lock
            must = max(1, round(interval / .1))
            step = interval / float(must)
            it = 0
            while True:
                time.sleep(step)
                lock.acquire()
                if not self._noop:
                    break
                elif it == must:
                    self.ftp.sendcmd('NOOP')
                    it = 0
                else:
                    it += 1
                lock.release()
            
        self._noop_thread = threading.Thread(target=run)
        self._noop_thread.start()


class KMZCachedFTP(CachedFTP):
    def open(self, file_path, mode='rb', *args, **kwargs):
        if file_path.endswith('.kml') \
           and not self.exists(file_path) \
           and self.exists(file_path[:-1] + 'z'):
            with self.open(file_path[:-1] + 'z') as kmz:
                kml_cache_path = self._get_cache_path(file_path)
                kmz_cache_path = self._get_cache_path(file_path[:-1] + 'z')
                if self._ftp_mod_time(file_path[:-1] + 'z') > 5 + (self._cache_mod_time(kml_cache_path) or 0):
                    kml = zipfile.ZipFile(kmz)
                    kml_file_name = path.basename(file_path)
                    assert tuple(kml.namelist()) == (kml_file_name,)
                    size = kml.getinfo(kml_file_name).file_size
                    
                    print("Decompress {}, size is {}...".format(kml_file_name, _sizeof_fmt(size)))
                    kml.extract(kml_file_name, path=path.dirname(kml_cache_path))
            return open(kml_cache_path, mode)
        else:
            return super().open(file_path, *args, **kwargs)    


class TimespanHeuristic(BaseHeuristic):
    def __init__(self, delta, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.delta = delta

    def update_headers(self, response):
        date = parsedate(response.headers['date'])
        expires = datetime(*date[:6]) + self.delta
        return {
            'expires' : formatdate(calendar.timegm(expires.timetuple())),
            'cache-control' : 'public',
        }

    def warning(self, response):
        msg = 'Automatically cached! Response is Stale.'
        return '110 - "%s"' % msg


class CachedHTTP:
    """Class to download files via HTTP."""

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.13; rv:59.0) Gecko/20100101 Firefox/59.0',
    }

    def __init__(self, cache_dir, delta=None):
        if path.exists(cache_dir):
            assert path.isdir(cache_dir)
        else:
            os.makedirs(cache_dir)
        heuristic = TimespanHeuristic(delta) if delta is not None else None
        self._sess = CacheControl(requests.session(), cache=FileCache(cache_dir), heuristic=heuristic)

    def text(self, url):
        return self._sess.get(url, headers=self.headers).text

    def stream(self, url, chunk_size=4096):
        return self._sess.get(url, headers=self.headers).iter_content(chunk_size)

    def binary(self, url):
        return self._sess.get(url, headers=self.headers).content


def _K2C(kelvin):
    """Convert Kelvin to Celsius."""
    return kelvin - 273.15

def _sum_hourly_percipitation(hours):
    def _sum(measurement):
        total_percipitation_mm = 0
        for i in range(hours):
            total_percipitation += measurement.prev(i).R1
        return total_percipitation_mm
    return _sum

def _first_of(vals, default=None):
    for val in vals:
        if val is not None:
            return val
    return default    

_HOURLY_CLIMATE_KEYS = {
    'air_temperature': 'TU',
    'cloud_type': 'CS',
    'cloudiness': 'N',
    'precipitation': 'RR',
    'pressure': 'P0',
    'soil_temperature': 'EB',
    'solar': 'ST',
    'sun': 'SD',
    'visibility': 'VV',
    'wind': 'FF',
}

_DAILY_CLIMATE_KEYS = {
    'more_precip': 'RR',
    'water_equiv': 'Wa',
}

# For the MOSMIX definitions see https://opendata.dwd.de/weather/lib/MetElementDefinition.xml
# For the historical data check the directories in ftp://ftp-cdc.dwd.de/pub/CDC/observations_germany/climate
# _ELEMENT_DEFINITIONS = {
#     # Format: (description, unit, how to extract from climate, how to extract from MOSIX
#     'TTT': ('Temperature 2m above surface', '°C',
#         ('h', 'TU', 'TT_TU'),
#         lambda m: _K2C(m.TTT)),
#     'N': ('Total cloud cover', '%', 
#         ('h', 'N', 'V_N'),
#         lambda m: m.N),
#     'RR1c': ('Total percipitation during the last hour', 'kg/m^2', 
#         ('h', 'RR', _sum_hourly_percipitation(1)),
#         lambda m: m.RR1c),
#     'RR3c': ('Total percipitation during the last three hours', 'kg/m^2',
#         ('h', 'RR', _sum_hourly_percipitation(3)),
#         lambda m.RR3c),
#     'RR24c', ('Total percipitation during the last 24 hours', 'kg/m^2',
#         ('d', 'RR', 'RS'),
#         ),
#     'RSunD': ('Relative sunshine duration within the last 24 hours', '%', 
#         (),
#         lambda m: m.RSunD),
#     'RRS24c': ('Snow-Rain-Equivalent', 'kg/m^2', 
#         ('d', 'Wa', lambda m: _first_of(m.WAAS_6, m.WASH_6, 0),
#         ) 
# }


# Temperature (mean, max, min)
# Humidity
# Sun engery
# Wind
# Percipitation: Snow-Rain-Equivalent, Percipitation type, amount


def _read_rst_table(lines):
    lines = iter(lines)

    head_line = None
    while not head_line:
        head_line = next(lines).strip()
    
    format_line = next(lines).strip()
    lengths = [len(col) for col in format_line.split(' ')]
    if not lengths:
        return []
    spans = [(0, lengths[0])]
    for length in lengths[1:]:
        start = spans[-1][1] + 1
        end = start + length
        spans.append((start, end))

    headings = [head_line[i:j].strip() for (i,j) in spans]
    for line in lines:
        vals = [line[i:j].strip() for (i,j) in spans]
        yield dict(zip(headings, vals))


StationDescription = namedtuple('StationDescription', ['id', 'begin', 'end', 'height', 'pos', 'name', 'state'])

def _read_station_txt(lines):
    lines = iter(lines)
    next(lines)
    next(lines)

    for line in lines:
        if not line.strip():
            continue
        id = int(line[:5])
        begin = date(int(line[6:10]), int(line[10:12]), int(line[12:14]))
        end = date(int(line[15:19]), int(line[19:21]), int(line[21:23]))
        height = Meter(float(line[24:38]))
        lat = float(line[38:50])
        lon = float(line[51:60])
        pos = GeographicPosition((lat,lon))
        name = line[61:101].strip()
        bl = line[102:].strip()
        yield StationDescription(id, begin, end, height, pos, name, bl)


class StationInfo:
    __slots__ = ['name', 'id', 'features', 'pos', 'height', 'river']

    def __init__(self, name, id, pos, height, river):
        self.name = name
        self.id = id
        self.features = dict()
        self.pos = pos
        self.height = height
        self.river = river

    def __str__(self):
        return repr(self)

    def __repr__(self):
        features = ["{} (started {})".format(k,v or '?') for k,v in self.features.items()]
        return "StationInfo({}, id={}, pos={}, height={}, features={})".format(self.name, self.id, self.pos, self.height, features)


class WeatherData:
    OPENDATA_FTP = 'opendata.dwd.de'
    CLIMATE_FTP = 'ftp-cdc.dwd.de'
    # See also https://www.dwd.de/DE/leistungen/klimadatendeutschland/stationsliste.html
    STATIONS_ENCYCLOPEDIA_URL = 'https://www.dwd.de/DE/leistungen/klimadatendeutschland/statliste/statlex_rich.txt?view=nasPublication&nn=16102'
    
    def __init__(self, cache_dir=None):
        if cache_dir is None:
            cache_dir = tempfile.mkdtemp(suffix='dwd')
        self.cache_dir = cache_dir
        self._http_cache = None
        self._cdc_ftp = None
        self._opendata_ftp = None

    def stations(self, only_active=True, state_filter=('NW', 'BW', 'RP', 'SL', 'HE'), features=('KL', 'RR', 'SO', 'TU', 'ST')):
        """Return a list of available weather stations.
        
        Name, position, height, river area and features of every station are
        provided. A 'feature' describes what the station can measure.

        For the definition of abbreviations see
        https://rcccm.dwd.de/DE/leistungen/klimadatendeutschland/stationsliste.html

        An additional feature 'ST' is provided, which corresponds to hourly
        solar energy measurments.

        :param only_active: Only return stations which are still active
        :param state_filter: Only return stations which are in the the listed federal states.
        :param features: Only returns stations which have at least one of the listed features.

        Lacking:
            - For features occuring multiple times the last one is used
            - River is set for the whole station using the first occurence
        """
        rst_table = self._http.text(self.STATIONS_ENCYCLOPEDIA_URL)

        if only_active:
            end_after = date.today() - timedelta(days=5)
        else:
            end_after = date(1, 1, 1)  # year 1 AD
        stations = {}

        for data in _read_rst_table(rst_table.strip().split('\n')):
            end = data['ENDE']
            if end:
                end = parse_date(end).date()
                if end < end_after:
                    continue

            if state_filter is not None:
                if data['BL'] not in state_filter:
                    continue

            begin = data['BEGINN'] or None  # sic
            if begin is not None:
                begin = parse_date(begin).date()

            id = int(data['STAT_ID'])
            if id not in stations:
                pos = GeographicPosition((float(data['BR_HIGH']), float(data['LA_HIGH'])))
                height = Meter(float(data['HS']))
                river = int(data['HFG_NFG']) if data['HFG_NFG'] else None
                stations[id] = StationInfo(data['STAT_NAME'], id, pos, height, river)
            stations[id].features[data['KE']] = begin

        ftp = self._cdc
        if only_active:
            end_after = date.today() - timedelta(days=2*31)
        ftp.cd('/pub/CDC/observations_germany/climate/hourly/solar/')
        with ftp.open('ST_Stundenwerte_Beschreibung_Stationen.txt') as fp:
            def lines(fp):
                for line in fp:
                    yield line.decode('latin1')
            for station in _read_station_txt(lines(fp)):
                if station.end < end_after:
                    continue
                if station.id not in stations:
                    continue
                stations[station.id].features['ST'] = station.begin

        if features is not None:
            for id in list(stations.keys()):
                st_feats = stations[id].features.keys()
                has_feature = any(f in features for f in st_feats)
                if not has_feature:
                    del stations[id]

        return stations

    @property
    def _http(self):
        if self._http_cache is None:
            cache_dir = path.join(self.cache_dir, 'http')
            self._http_cache = CachedHTTP(cache_dir, timedelta(days=2))
        return self._http_cache

    @property
    def _cdc(self):
        if self._cdc_ftp is None:
            cache_dir = path.join(self.cache_dir, self.CLIMATE_FTP)
            self._cdc_ftp = CachedFTP(self.CLIMATE_FTP, cache_dir)
        return self._cdc_ftp

    @property
    def _opendata(self):
        if self._opendata_ftp is None:
            cache_dir = path.join(self.cache_dir, self.OPENDATA_FTP)
            self._opendata_ftp = KMZCachedFTP(self.OPENDATA_FTP, cache_dir)
        return self._opendata_ftp
