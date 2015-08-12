"""Parsers and exractors for the bundle source files.

This module manages reading and writing files that configure a source bundle:

- bundle.py: Main code file for building a bundle
- meta.py: One-time executed code for manipulating bundle metadata.
- bundle.yaml: Main metadata file.
- schema.csv: Describes tables and columns.
- column_map.csv: Maps column names from a source file to the schema
- sources.csv: Describes the name, description and URL of input data

This module connects the filesystem to the File records in a dataset. A parallel module,
ambry.orm.files, connects between the File records and the other types of records in a Dataset

Build source file data is stored in File records in msgpack format. Files that are essentially spreadsheets,
such as schema, column_map and sources, are stored as a list of lists, one list per row.YAML files
are stored as dicts, and python files are stored as strings. Msgpack format is used because it is
fast and small, which is important for largest schema files, such as those in the US Census.

"""

# Copyright (c) 2015 Civic Knowledge. This file is licensed under the terms of the
# Revised BSD License, included in this distribution as LICENSE.txt

import imp
import msgpack
import sys
import time
import yaml

from six import string_types, iteritems

from ambry.dbexceptions import ConfigurationError
from ambry.orm import File
from ambry.util import Constant, get_logger

logger = get_logger(__name__)


class FileTypeError(Exception):
    """Bad file type"""


class BuildSourceFile(object):

    SYNC_DIR = Constant()
    SYNC_DIR.FILE_TO_RECORD = 'ftr'
    SYNC_DIR.RECORD_TO_FILE = 'rtf'
    SYNC_DIR.OBJECT_TO_FILE = 'otf'

    def __init__(self, bundle, dataset, filesystem, file_const):
        """
        Construct a new Build Source File acessor
        :param dataset: The dataset that will hold File records
        :param filesystem: A FS filesystem abstraction object
        :param file_const: The BSFILE file contact
        :return:
        """

        assert not isinstance(filesystem, string_types)  # Old Datatypes are leaking through.

        self._bundle = bundle
        self._dataset = dataset
        self._fs = filesystem
        self._file_const = file_const

    def exists(self):
        return self._fs.exists(file_name(self._file_const))

    def size(self):
        return self._fs.getsize(file_name(self._file_const))

    @property
    def record(self):
        return self._dataset.bsfile(self._file_const)

    @property
    def default(self):
        """Return default contents"""
        return file_default(self._file_const)

    def prepare_to_edit(self):
        """Ensure there is a file to edit, either by syncing to the filesystem or by installing the default"""

        if not self.record.contents and not self.exists():
            self._fs.setcontents(file_name(self._file_const), self.default)

        self.sync()

    @property
    def path(self):
        return self._fs.getsyspath(file_name(self._file_const))

    @property
    def fs_modtime(self):
        from fs.errors import ResourceNotFoundError

        fn_path = file_name(self._file_const)

        try:
            info = self._fs.getinfokeys(fn_path, "modified_time")
            return time.mktime(info['modified_time'].timetuple())
        except ResourceNotFoundError:
            return None

    @property
    def fs_hash(self):
        from ambry.util import md5_for_file

        if not self.exists():
            return None

        fn_path = file_name(self._file_const)

        with self._fs.open(fn_path) as f:
            return md5_for_file(f)

    def sync_dir(self):
        """ Report on which direction a synchronization should be done.
        :return:
        """

        # NOTE: These are ordered so the FILE_TO_RECORD has preference over RECORD_TO_FILE
        # if there is a conflict.

        if self.exists() and bool(self.size()) and not self.record.size:
            # The fs exists, but the record is empty

            return self.SYNC_DIR.FILE_TO_RECORD

        if self.fs_modtime > self.record.modified and self.record.source_hash != self.fs_hash:
            # Filesystem is newer

            return self.SYNC_DIR.FILE_TO_RECORD

        if self.record.size and not self.exists():
            # Record exists, but not the FS

            return self.SYNC_DIR.RECORD_TO_FILE

        if self.record.modified > self.fs_modtime:
            # Record is newer

            return self.SYNC_DIR.RECORD_TO_FILE

        return None

    def sync(self, force=None):
        """Synchronize between the file in the file system and the field record"""

        if force:
            sd = force
        else:
            sd = self.sync_dir()

        if sd == self.SYNC_DIR.FILE_TO_RECORD:

            if force and not self.exists():
                return None

            self.fs_to_record()

        elif sd == self.SYNC_DIR.RECORD_TO_FILE:
            self.record_to_fs()

        else:
            return None

        self._dataset.config.sync[self._file_const][sd] = time.time()
        return sd

    def clean_objects(self):
        pass

    def fs_to_record(self):
        """Load a file in the filesystem into the file record"""
        raise NotImplementedError

    def record_to_fs(self):
        """Create a filesystem file from a File"""
        raise NotImplementedError


class RowBuildSourceFile(BuildSourceFile):
    """A Source Build file that is a list of rows, like a spreadsheet"""

    def fs_to_record(self):
        """Load a file in the filesystem into the file record"""
        import unicodecsv as csv

        fn_path = file_name(self._file_const)

        fr = self._dataset.bsfile(self._file_const)
        fr.path = fn_path
        rows = []
        with self._fs.open(fn_path) as f:
            for row in csv.reader(f):
                row = [e if e.strip() != '' else None for e in row]
                if any(bool(e) for e in row):
                    rows.append(row)

        try:
            fr.update_contents(msgpack.packb(rows))
        except AssertionError:
            raise

        fr.mime_type = 'application/msgpack'
        fr.source_hash = self.fs_hash

        fr.modified = self.fs_modtime

    def record_to_fs(self):
        """Create a filesystem file from a File"""
        import unicodecsv as csv

        fr = self._dataset.bsfile(self._file_const)

        fn_path = file_name(self._file_const)

        if fr.contents:
            with self._fs.open(fn_path, 'wb') as f:
                w = csv.writer(f)
                for row in fr.unpacked_contents:
                    w.writerow(row)

            fr.source_hash = self.fs_hash
            fr.modified = self.fs_modtime


class DictBuildSourceFile(BuildSourceFile):
    """A Source Build file that is a list of rows, like a spreadsheet"""

    def fs_to_record(self):
        """Load a file in the filesystem into the file record"""

        fn_path = file_name(self._file_const)
        fr = self._dataset.bsfile(self._file_const)
        fr.path = fn_path
        if fn_path.endswith('.yaml'):
            with self._fs.open(fn_path) as f:
                fr.update_contents(msgpack.packb(yaml.load(f)))
            fr.mime_type = 'application/msgpack'
        else:
            raise FileTypeError('Unknown file type for : %s' % fn_path)

        fr.source_hash = self.fs_hash

        fr.modified = self.fs_modtime

    def record_to_fs(self):
        """Create a filesystem file from a File"""

        fr = self._dataset.bsfile(self._file_const)

        fn_path = file_name(self._file_const)

        if fr.contents:
            with self._fs.open(fn_path, 'wb') as f:
                # FIXME: f is unused?
                yaml.dump(fr.unpacked_contents, default_flow_style=False)
            fr.source_hash = self.fs_hash
            fr.modified = self.fs_modtime


class StringSourceFile(BuildSourceFile):
    """A Source Build File that is a single file. """

    def clean_objects(self):
        """This sort of file can only be set from files, and there are no associated object"""
        pass

    def record_to_objects(self):
        pass

    def objects_to_record(self):
        pass

    def fs_to_record(self):
        """Load a file in the filesystem into the file record"""

        fn_path = file_name(self._file_const)
        fr = self._dataset.bsfile(self._file_const)
        fr.path = fn_path

        # Not dealing with encodings in in/out here, since the recod is supposed to be a striaght copy of the
        # file.
        with self._fs.open(fn_path, 'rb') as f:
            fr.update_contents(f.read())

        fr.mime_type = 'text/plain'
        fr.source_hash = self.fs_hash
        fr.modified = self.fs_modtime

    def record_to_fs(self):
        """Create a filesystem file from a File"""

        fr = self._dataset.bsfile(self._file_const)

        if fr.contents:
            with self._fs.open(file_name(self._file_const), 'wb') as f:
                f.write(fr.contents)

            fr.source_hash = self.fs_hash
            fr.modified = self.fs_modtime


class MetadataFile(DictBuildSourceFile):

    def clean_objects(self):
        self._dataset.configs = [c for c in self._dataset.configs if c.type != 'metadata']

        # TODO: Not sure if these should be cleaned or no

    def record_to_objects(self):
        """Create config records to match the file metadata"""
        from ..util import AttrDict

        fr = self._dataset.bsfile(self._file_const)

        contents = fr.unpacked_contents

        if not contents:
            return

        ad = AttrDict(contents)

        # Get time that filessystem was synchronized to the File record.
        # Maybe use this to avoid overwriting configs that changed by bundle program.
        # fs_sync_time = self._dataset.config.sync[self._file_const][self.file_to_record]

        self._dataset.config.metadata.set(ad)

        self._dataset._database.commit()

        return ad

    def objects_to_record(self):

        fr = self._dataset.bsfile(self._file_const)

        if fr.has_contents:

            o = fr.unpacked_contents

        else:
            o = yaml.safe_load(file_default(self._file_const))

            try:
                act = self._bundle.library.config.account('ambry').to_dict()

                if act:
                    del act['_name']
                    o['contacts']['creator'] = act

            except ConfigurationError:
                pass

        o['identity'] = self._dataset.identity.ident_dict
        o['names'] = self._dataset.identity.names_dict

        with self._fs.open(file_name(self._file_const), 'wb') as f:
            yaml.safe_dump(o, f, default_flow_style=False, indent=4, encoding='utf-8')


class PythonSourceFile(StringSourceFile):

    def clean_objects(self):
        """The python sources can only be set from files, and there are no associated objects"""
        pass

    def import_bundle(self):
        """Add the filesystem to the Python sys path with an import hook, then import
        to file as Python"""

        try:
            import ambry.build
            module = sys.modules['ambry.build']
        except ImportError:
            module = imp.new_module('ambry.build')
            sys.modules['ambry.build'] = module

        bf = self._dataset.bsfile(self._file_const)

        if not bf.has_contents:
            from ambry.bundle import Bundle
            return Bundle

        exec(bf.contents, module.__dict__)

        #print self._file_const, bundle.__dict__.keys()
        #print bf.contents

        return module.Bundle

    def import_lib(self):
        """Import the lib.py file into the bundle module"""

        try:
            import ambry.build
            module = sys.modules['ambry.build']
        except ImportError:
            module = imp.new_module('ambry.build')
            sys.modules['ambry.build'] = module

        bf = self._dataset.bsfile(self._file_const)

        if not bf.has_contents:
            return

        exec(bf.contents, module.__dict__)

        # print self._file_const, bundle.__dict__.keys()
        # print bf.contents

        return module


class SourcesFile(RowBuildSourceFile):

    def clean_objects(self):

        self._dataset.sources[:] = []

    def record_to_objects(self):
        """Create config records to match the file metadata"""
        from ..orm.source import DataSource

        fr = self._dataset.bsfile(self._file_const)

        contents = fr.unpacked_contents

        if not contents:
            return

        # Zip transposes an array when in the form of a list of lists, so this transposes so
        # each row starts with the heading and the rest of the row are the values
        # for that row. The bool and filter return false when none of the values
        # are non-empty. Then zip again to transpose to original form.

        # TODO: next row is so complicated. Try to refactor.
        # FIXME: Needs smart 2to3 conversion. Auto conversion breaks tests.
        non_empty_rows = zip(*[row for row in zip(*contents) if bool(filter(bool, row[1:]))])

        s = self._dataset._database.session

        for i, row in enumerate(non_empty_rows):

            if i == 0:
                header = row
            else:
                d = dict(list(zip(header, row)))

                if 'widths' in d:
                    del d['widths']  # Obsolete column in old spreadsheets.

                if 'table' in d:
                    d['dest_table_name'] = d['table']
                    del d['table']

                if 'dest_table' in d:
                    d['dest_table_name'] = d['dest_table']
                    del d['dest_table']

                if 'source_table' in d:
                    d['source_table_name'] = d['source_table']
                    del d['source_table']

                d['d_vid'] = self._dataset.vid

                ds = self._dataset.source_file(d['name'])
                if ds:
                    ds.update(**d)

                else:
                    ds = DataSource(**d)

                s.merge(ds)

        self._dataset._database.commit()

    def objects_to_record(self):

        sorter = lambda r: ('A' if r['urltype'] == 'ref' else 'z' if r['urltype'] is None else r['urltype'], r['name'])

        rows = sorted([s.row for s in self._dataset.sources], key=sorter)

        if rows:
            rows = [list(rows[0].keys())] + [list(r.values()) for r in rows]

            # Transpose trick to remove empty columns
            # FIXME: needs smart 2to3 conversion. Auto conversion breaks tests.
            rows = zip(*[row for row in zip(*rows) if bool(filter(bool, row[1:]))])
        else:
            # No contents, so use the default file
            import csv
            rows = list(csv.reader(file_default(self._file_const).splitlines()))

        bsfile = self._dataset.bsfile(self._file_const)

        bsfile.mime_type = 'application/msgpack'
        bsfile.update_contents(msgpack.packb(rows))


class SchemaFile(RowBuildSourceFile):

    def clean_objects(self):
        self._dataset.tables[:] = []

    def record_to_objects(self):
        """Create config records to match the file metadata"""

        def _clean_int(i):
            if i is None:
                return None
            elif isinstance(i, int):
                return i
            elif isinstance(i, string_types):
                if len(i) == 0:
                    return None

                return int(i.strip())

        bsfile = self._dataset.bsfile(self._file_const)

        contents = bsfile.unpacked_contents

        if not contents:
            return

        line_no = 1  # Accounts for file header. Data starts on line 2

        errors = []
        warnings = []

        extant_tables = {t.name: t for t in self._dataset.tables}

        def get_or_new_table(row):

            table_name = row['table']

            if table_name not in extant_tables:

                t = self._dataset.new_table(
                    table_name,
                    description=row.get('description') if row['column'] == 'id' else '')

                extant_tables[table_name] = t

            return extant_tables[table_name]

        for row in bsfile.dict_row_reader:

            line_no += 1

            # Skip blank lines
            if not row.get('column', False) and not row.get('table', False):
                continue

            if not row.get('column', False):
                raise ConfigurationError('Row error: no column on line {}'.format(line_no))
            if not row.get('table', False):
                raise ConfigurationError('Row error: no table on line {}'.format(line_no))
            if not row.get('datatype', False):
                raise ConfigurationError('Row error: no type on line {}'.format(line_no))

            table = get_or_new_table(row)

            data = {k.replace('d_', '', 1): v for k, v in list(row.items()) if k and k.startswith('d_')}

            table.add_column(
                row['column'],
                fk_vid=row['is_fk'] if row.get('is_fk', False) else None,
                description=(row.get('description', '') or '').strip().encode('utf-8'),
                datatype=row['datatype'].strip().lower(),
                proto_vid=row.get('proto_vid'),
                derivedfrom=row.get('derivedfrom'),
                size=_clean_int(row.get('size', None)),
                width=_clean_int(row.get('width', None)),
                data=data,
                keywords=row.get('keywords'),
                measure=row.get('measure'),
                caster=row.get('caster'),
                units=row.get('units', None),
                universe=row.get('universe'))

        return warnings, errors

    def objects_to_record(self):

        rows = []

        for table in self._dataset.tables:
            for col in table.columns:
                row = col.row

                if not rows:
                    rows.append([e if e != 'name' else 'column' for e in list(row.keys())])

                rows.append(list(row.values()))

            rows.append([None for e in rows[0]])  # Transpose trick fails if rows not all same size

        # Transpose trick to remove empty columns
        if rows:
            rows_before_transpose = len(rows)
            # FIXME: Needs smart 2to3 conversion. Auto-conversion breaks tests.
            rows = zip(*[r for r in zip(*rows) if bool(filter(bool, r[1:]))])
            assert rows_before_transpose == len(rows)  # The transpose trick removes all of the rows if anything goes wrong

        else:
            # No contents, so use the default file
            import csv
            rows = list(csv.reader(file_default(self._file_const).splitlines()))

        bsfile = self._dataset.bsfile(self._file_const)
        bsfile.mime_type = 'application/msgpack'
        bsfile.update_contents(msgpack.packb(rows))


class SourceSchemaFile(RowBuildSourceFile):

    def clean_objects(self):
        self._dataset.source_tables[:] = []

    def record_to_objects(self):
        """Write from the stored file data to the source records"""
        bsfile = self._dataset.bsfile(self._file_const)

        failures = set()
        for row in bsfile.dict_row_reader:
            st = self._dataset.source_table(row['table'])

            if not st:
                st = self._dataset.new_source_table(row['table'])

            if 'datatype' not in row:
                row['datatype'] = 'unknown'

            del row['table']

            st.add_column(**row)  # Create or update

        if failures:
            raise ConfigurationError("Failed to load source schema, missing sources: {} ".format(failures))

        self._dataset._database.commit()

    def objects_to_record(self):

        bsfile = self._dataset.bsfile(self._file_const)

        rows = []
        for table in self._dataset.source_tables:

            for column in table.columns:
                rows.append(column.row)

        if rows:
            rows = [list(rows[0].keys())] + [list(r.values()) for r in rows]

        else:
            # No contents, so use the default file
            import csv
            rows = list(csv.reader(file_default(self._file_const).splitlines()))

        bsfile.mime_type = 'application/msgpack'
        bsfile.update_contents(msgpack.packb(rows))

        self._dataset._database.commit()

file_info_map = {
    File.BSFILE.BUILD: (File.path_map[File.BSFILE.BUILD], PythonSourceFile),
    File.BSFILE.LIB: (File.path_map[File.BSFILE.LIB], PythonSourceFile),
    File.BSFILE.DOC: (File.path_map[File.BSFILE.DOC], StringSourceFile),
    File.BSFILE.META: (File.path_map[File.BSFILE.META], MetadataFile),
    File.BSFILE.SCHEMA: (File.path_map[File.BSFILE.SCHEMA], SchemaFile),
    File.BSFILE.SOURCESCHEMA: (File.path_map[File.BSFILE.SOURCESCHEMA], SourceSchemaFile),
    File.BSFILE.SOURCES: (File.path_map[File.BSFILE.SOURCES], SourcesFile)
}


def file_name(const):
    """Return the file name for a file constant"""
    return file_info_map[const][0]


def file_class(const):
    """Return the class for a file constant"""
    return file_info_map[const][1]


def file_default(const):
    """Return the default content for the file"""

    import ambry.bundle.default_files as df
    import os

    path = os.path.join(os.path.dirname(df.__file__),  file_name(const))

    with open(path) as f:
        return f.read()


class BuildSourceFileAccessor(object):

    def __init__(self, bundle, dataset, filesystem=None):
        assert not isinstance(filesystem, string_types)  # Bundle fs changed from FS to URL; catch use of old values
        self._bundle = bundle
        self._dataset = dataset
        self._fs = filesystem

    @property
    def build_file(self):
        return self.file(File.BSFILE.BUILD)

    @property
    def meta_file(self):
        return self.file(File.BSFILE.META)

    def file(self, const_name):

        fc = file_class(const_name)

        bsfile = fc(self._bundle, self._dataset, self._fs, const_name)

        return bsfile

    def record_to_objects(self, preference=None):
        """Create objects from files, or merge the files into the objects. """
        from ambry.orm.file import File

        for file_const, (file_name, clz) in iteritems(file_info_map):
            f = self.file(file_const)

            pref = preference if preference else f.record.preference

            if pref == File.PREFERENCE.FILE:
                self._bundle.logger.debug('   Cleaning objects {}'.format(file_const))
                f.clean_objects()

            if pref in (File.PREFERENCE.FILE, File.PREFERENCE.MERGE):
                self._bundle.logger.debug('   rto {}'.format(file_const))
                f.record_to_objects()

    def objects_to_record(self, preference=None):
        """Create file records from objects. """
        from ambry.orm.file import File

        for file_const, (file_name, clz) in iteritems(file_info_map):
            f = self.file(file_const)

            pref = preference if preference else f.record.preference

            if pref in (File.PREFERENCE.MERGE, File.PREFERENCE.OBJECT):
                self._bundle.logger.debug('   otr {}'.format(file_const))
                f.objects_to_record()

    def sync(self, force=None, defaults=False):

        syncs = []

        for file_const, (file_name, clz) in iteritems(file_info_map):

            f = self.file(file_const)

            sync_info = (None, None)

            if defaults and force == f.SYNC_DIR.RECORD_TO_FILE and not f.record.contents:
                sync_info = (file_const, f.prepare_to_edit())
            elif force == f.SYNC_DIR.OBJECT_TO_FILE:
                try:
                    self._bundle.logger.debug('   otr {}'.format(file_const))
                    f.objects_to_record()
                    self._bundle.logger.debug('   rtf {}'.format(file_const))
                    sync_info = (file_const, f.sync(f.SYNC_DIR.RECORD_TO_FILE))
                except AttributeError:
                    pass
            elif force == f.SYNC_DIR.FILE_TO_RECORD:
                self._bundle.logger.debug("   ftr {}".format(file_const))
                sync_info = (file_const, f.sync(force))
            else:
                sync_info = (file_const, f.sync())
                if sync_info[1]:
                    self._bundle.logger.debug('   {} {}'.format(sync_info[1], file_const))

            syncs.append(sync_info)

        return syncs

    def sync_dirs(self):
        return [(file_const, self.file(file_const).sync_dir())
                for file_const, (file_name, clz) in iteritems(file_info_map)]
