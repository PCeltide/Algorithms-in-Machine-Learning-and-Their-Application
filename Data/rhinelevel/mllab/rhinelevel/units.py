import enum
import pyproj

class GeographicPosition(tuple):
    @classmethod
    def conv(cls, x, y, proj4):
        x, y = pyproj.Proj(init=proj4)(x, y, inverse=True)
        return cls((y, x))

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