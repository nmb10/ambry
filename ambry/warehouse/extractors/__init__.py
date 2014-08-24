"""Classes for converting warehouse databases to other formats.

Copyright (c) 2013 Clarinova. This file is licensed under the terms of the
Revised BSD License, included in this distribution as LICENSE.txt
"""

import ogr

class ExtractError(Exception):
    pass

def extract(database, table, format, cache, dest, force=False):

    from ambry.warehouse.extractors import CsvExtractor

    ex = dict(
        csv=CsvExtractor(),
        shapefile = ShapeExtractor(),
        geojson = GeoJsonExtractor(),
        kml = KmlExtractor()
    ).get(format, False)


    if not ex:
        raise ValueError("Unknown format name '{}'".format(format))

    extracted, path =  ex.extract(database, table, cache, dest, force=force)

    return extracted, path

class CsvExtractor(object):

    def __init__(self):
        pass

    def extract(self, database, table, cache, dest, force=False):

        import unicodecsv

        if cache.has(dest):
            if force:
                cache.remove(dest, True)
            else:
                return False, cache.path(dest)

        row_gen = database.connection.execute("SELECT * FROM {}".format(table))

        w = unicodecsv.writer(cache.put_stream(dest))

        for i,row in enumerate(row_gen):
            if i == 0:
                w.writerow(row.keys())

            w.writerow(row)

        return True, cache.path(dest)



class OgrExtractor(object):

    epsg = 4326

    def __init__(self):
        pass

        self.mangled_names = {}


    def geometry_type(self, database, table):
        """Return the name of the most common geometry type and the coordinate dimensions"""
        ce = database.connection.execute

        types = ce('SELECT count(*) AS count, GeometryType(geometry) AS type,  CoordDimension(geometry) AS cd '
                   'FROM {} GROUP BY type ORDER BY type desc;'.format(table)).fetchall()

        t = types[0][1]
        cd = types[0][2]

        if not t:
            raise ExtractError("No geometries in {}".format(table))

        return t, cd

    geo_map = {
        'POLYGON': ogr.wkbPolygon,
        'MULTIPOLYGON': ogr.wkbMultiPolygon,
        'POINT': ogr.wkbPoint,
        'MULTIPOINT': ogr.wkbMultiPoint,
        # There are a lot more , add them as they are encountered.
    }

    _ogr_type_map = {
        None: ogr.OFTString,
        '': ogr.OFTString,
        'TEXT': ogr.OFTString,
        'VARCHAR': ogr.OFTString,
        'INT': ogr.OFTInteger,
        'INTEGER': ogr.OFTInteger,
        'REAL': ogr.OFTReal,
        'FLOAT': ogr.OFTReal,

    }

    def ogr_type_map(self, v):
        return self._ogr_type_map[v.split('(',1)[0]] # Sometimes 'VARCHAR', sometimes 'VARCHAR(10)'


    def create_schema(self, database, table, layer):
        ce = database.connection.execute

        for row in ce('PRAGMA table_info({})'.format(table)).fetchall():

            if row['name'].lower() in ('geometry', 'wkt','wkb'):
                continue

            name = self.mangle_name(str(row['name']))

            fdfn = ogr.FieldDefn(name, self.ogr_type_map(row['type']))

            print "CREATE", name, self.ogr_type_map(row['type'])

            if row['type'] == '':
                fdfn.SetWidth(254) # FIXME Wasteful, but would have to scan table for max value.

            layer.CreateField(fdfn)



    def new_layer(self, abs_dest, name, t):

        driver = ogr.GetDriverByName(self.driver_name)
        ds = driver.CreateDataSource(abs_dest)

        srs = ogr.osr.SpatialReference()
        srs.ImportFromEPSG(self.epsg)

        layer = ds.CreateLayer(name, srs, self.geo_map[t])

        return ds, layer

    def mangle_name(self, name):

        if len(name) <= self.max_name_len:
            return name

        if name in self.mangled_names:
            return self.mangled_names[name]

        for i in range(0,20):
            mname = name[:self.max_name_len]+str(i)
            if mname not in  self.mangled_names.values():
                self.mangled_names[name] = mname
                return mname

        raise Exception("Ran out of names")


    def extract(self, database, table, cache, dest, force=False):

        import ogr
        import os

        abs_dest = cache.path(dest, missing_ok = True)

        if os.path.exists(abs_dest):
            if force:
                from ambry.util import rm_rf
                rm_rf(abs_dest)
            else:
                return False, abs_dest

        t, cd = self.geometry_type(database, table)

        ds, layer = self.new_layer(abs_dest, table, t)

        self.create_schema(database, table, layer)

        q = "SELECT *, AsText(Transform(geometry, {} )) AS _wkt FROM {}".format(self.epsg, table)

        for i,row in enumerate(database.connection.execute(q)):

            feature = ogr.Feature(layer.GetLayerDefn())

            for name, value in row.items():
                if name.lower() in ('geometry', 'wkt', 'wkb', '_wkt'):
                    continue
                if value:
                    try:
                        if isinstance(value, unicode):
                            value = str(value)

                        name = self.mangle_name(str(name))

                        feature.SetField(name, value)
                    except Exception as e:
                        print 'Failed for {}={} ({})'.format(name, value, type(value))
                        raise


            geometry = ogr.CreateGeometryFromWkt(row['_wkt'])

            feature.SetGeometryDirectly(geometry)
            if layer.CreateFeature(feature) != 0:
                import gdal
                raise Exception(
                    'Failed to add feature: {}: geometry={}'.format(gdal.GetLastErrorMsg(), geometry.ExportToWkt()))

            feature.Destroy()

        ds.SyncToDisk()
        ds.Release()

        return True, abs_dest

class ShapeExtractor(OgrExtractor):
    driver_name = 'Esri Shapefile'
    max_name_len = 8 # For ESRI SHapefiles


class GeoJsonExtractor(OgrExtractor):
    driver_name = 'GeoJSON'
    max_name_len = 40

class KmlExtractor(OgrExtractor):
    driver_name = 'KML'
    max_name_len = 40