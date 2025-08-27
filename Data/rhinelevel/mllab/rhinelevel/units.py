import enum
import pyproj

class GeographicPosition(tuple):
    @classmethod
    def conv(cls, x, y, proj4):
        # Define the source and target Coordinate Reference Systems (CRS)
        source_crs = pyproj.CRS(proj4)
        target_crs = pyproj.CRS("EPSG:4326") # WGS84 standard for latitude/longitude
        transformer = pyproj.Transformer.from_crs(source_crs, target_crs, always_xy=False)
        lat, lon = transformer.transform(x, y)
        return cls((lat, lon))

    def __str__(self):
        return repr(self)
    
    def __repr__(self):
        return "(lat={}, lon={})".format(self[0], self[1])

    @property
    def lon(self):
        return self[0]

    @property
    def lat(self):
        return self[1]


class River(enum.Enum):
    rhine = enum.auto()
    main = enum.auto()
    moselle = enum.auto()
    neckar = enum.auto()
    lahn = enum.auto()


class Meter(float):
    def __str__(self):
        return repr(self)

    def __repr__(self):
        return "{}m".format(super().__repr__())


class KiloMeter(float):
    def __str__(self):
        return repr(self)

    def __repr__(self):
        return "{}km".format(super().__repr__())
