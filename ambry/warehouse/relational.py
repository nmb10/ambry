"""
Copyright (c) 2013 Clarinova. This file is licensed under the terms of the
Revised BSD License, included in this distribution as LICENSE.txt
"""

from ..dbexceptions import DependencyError
from . import WarehouseInterface

class RelationalWarehouse(WarehouseInterface):





    ##
    ## Tables
    ##

    def has_table(self, table_name):

        return table_name in self.database.inspector.get_table_names()



    def table_meta(self, d_vid, p_vid, table_name):
        '''Get the metadata directly from the database. This requires that
        table_name be the same as the table as it is in stalled in the database'''
        from ..schema import Schema

        meta, table = Schema.get_table_meta_from_db(self.library.database,
                                                    table_name,
                                                    d_vid=d_vid,
                                                    driver=self.database.driver,
                                                    alt_name=self.augmented_table_name(d_vid, table_name),
                                                    session=self.library.database.session)

        return meta, table

    def create_table(self, partition, table_name):
        '''Create the table in the warehouse, using an augmented table name '''
        from ..schema import Schema

        p_vid = self._to_vid(partition)
        d_vid = self._partition_to_dataset_vid(partition)

        meta, table = self.table_meta(d_vid, p_vid, table_name)

        if not self.has_table(table.name):
            table.create(bind=self.database.engine)
            self.logger.info('create_table {}'.format(table.name))
        else:
            self.logger.info('table_exists {}'.format(table.name))

        return table, meta


    def load_insert(self, partition, table_name):
        from ..database.inserter import ValueInserter
        from sqlalchemy import Table, MetaData

        replace = False

        self.logger.info('load_insert {}'.format(partition.identity.name))

        if self.database.driver == 'mysql':
            cache_size = 5000
        elif self.database.driver == 'postgres':
            cache_size = 20000
        else:
            cache_size = 50000

        p_vid = partition.identity.vid
        d_vid = partition.identity.as_dataset().vid

        source_table_name = table_name
        dest_table_name = self.augmented_table_name(d_vid, table_name)

        self.logger.info('populate_table {}'.format(table_name))

        dest_metadata = MetaData()
        dest_table = Table(dest_table_name, dest_metadata, autoload=True, autoload_with=self.database.engine)

        insert_statement = dest_table.insert()

        source_metadata = MetaData()
        source_table = Table(source_table_name, source_metadata, autoload=True, autoload_with=partition.database.engine)

        select_statement = source_table.select()

        if replace:
            insert_statement = insert_statement.prefix_with('OR REPLACE')

        cache = []

        with self.database.engine.begin() as conn:
            for i, row in enumerate(partition.database.session.execute(select_statement)):
                self.logger.progress('add_row', table_name, i)

                cache.append(row)

                if len(cache) > cache_size:
                    conn.execute(insert_statement, cache)
                    cache = []

            if len(cache):
                conn.execute(insert_statement, cache)

        self.logger.info('done {}'.format(partition.identity.vname))


    def load_ogr(self, partition, table_name):
        #
        # Use ogr2ogr to copy.
        #
        import shlex
        from sh import ogr2ogr

        p_vid = partition.identity.vid
        d_vid = partition.identity.as_dataset().vid

        a_table_name = self.augmented_table_name(d_vid, table_name)

        args = [
            "-t_srs EPSG:2771",
            "-nlt PROMOTE_TO_MULTI",
            "-nln {}".format(a_table_name),
            "-progress ",
            "-overwrite",
            "-skipfailures",
            ] + self._ogr_args(partition)

        def err_output(line):
            self.logger.error(line)

        global count
        count = 0


        def out_output(c):
            global count
            count += 1
            if count % 10 == 0:
                pct = (int(count / 10) - 1) * 20
                if pct <= 100:
                    self.logger.info("Loading {}%".format(pct))


        self.logger.info("Loading with: ogr2ogr {}".format(' '.join(args)))

        # Need to shlex it b/c the "PG:" part gets bungled otherwise.
        p = ogr2ogr(*shlex.split(' '.join(args)), _err=err_output, _out=out_output, _iter=True, _out_bufsize=0)
        p.wait()

        return


class OldRelationalWarehouse(WarehouseInterface):


    
    def table(self, d_vid, p_vid, table_name):
        '''Return an ORM table from the local schema. Unlike
        table_meta, this pulls the data from library records, 
        so table_name must be unaugmented. '''
        
        from ..schema import Schema
        

        return Schema.get_table_from_database(self.library.database, 
                                              table_name, 
                                              d_vid = d_vid,
                                              session = self.library.database.session, 
                                              )
        

          
    def remove_by_name(self,name):
        from ..orm import Dataset
        from ..bundle import LibraryDbBundle
        from ..identity import PartitionNameQuery
        from sqlalchemy.exc import  NoSuchTableError, ProgrammingError
        
        dataset  = self.get(name)

        if dataset.partition:
            b = LibraryDbBundle(self.library.database, dataset.vid)
            p = b.partitions.find(id_=dataset.partition.id_)
            self.logger.info("Dropping tables in partition {}".format(p.identity.vname))
            for table_name in p.tables: # Table name without the id prefix
                
                table_name = self.augmented_table_name(p.identity.as_dataset().vid, table_name)
                
                try:
                    self.database.drop_table(table_name)
                    self.logger.info("Dropped table: {}".format(table_name))
                    
                except NoSuchTableError:
                    self.logger.info("Table does not exist (a): {}".format(table_name))
                    
                except ProgrammingError:
                    self.logger.info("Table does not exist (b): {}".format(table_name))

            self.library.database.remove_partition(dataset.partition)
            
            
        elif dataset:
            
            b = LibraryDbBundle(self.library.database, dataset.vid)
            for p in b.partitions:
                self.remove_by_name(p.identity.vname)

            self.logger.info('Removing bundle {}'.format(dataset.vname))
            self.library.database.remove_bundle(dataset)
        else:
            self.logger.error("Failed to find partition or bundle by name '{}'".format(name))
        
