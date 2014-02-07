"""
Copyright (c) 2013 Clarinova. This file is licensed under the terms of the
Revised BSD License, included in this distribution as LICENSE.txt
"""

from ..dbexceptions import DependencyError
from . import WarehouseInterface

class RelationalWarehouse(WarehouseInterface):
    


    def get(self, name_or_id):
        """Return true if the warehouse already has the referenced bundle or partition"""
        
        return self.library.resolve(name_or_id)

    def has(self, ref):
        r = self.library.resolve(ref)

        if bool(r):
            return True
        else:
            return False

    def _to_vid(self,partition):

        from ..partition import PartitionBase
        from ..identity import Identity

        if isinstance(partition, PartitionBase):
            pid = partition.identity.vid
        elif isinstance(partition, Identity):
            pid = partition.vid
        else:
            pid = partition

        return pid

    def _partition_to_dataset_vid(self,partition):

        from ..partition import PartitionBase
        from ..identity import Identity

        if isinstance(partition, PartitionBase):
            did = partition.identity.as_dataset().vid
        elif isinstance(partition, Identity):
            did = partition.as_dataset().vid
        else:
            from ..identity import ObjectNumber
            did = str(ObjectNumber(str(partition)).dataset)

        return did



    def install_partition(self, bundle, partition):
        '''Install the records for the partition, the tables referenced by the partition,
        and the bundle, if they aren't already installed'''
        from sqlalchemy.orm.exc import NoResultFound

        ld = self.library.database

        pid = self._to_vid(partition)

        ld.install_partition(bundle, pid)

        p = bundle.partitions.get(pid)

        for table_name in p.tables:
            self.create_table(p, table_name)

    ##
    ## Tables
    ##

    def has_table(self, table_name):

        return table_name in self.database.inspector.get_table_names()

    def augmented_table_name(self, d_vid, table_name):
        return d_vid.replace('/', '_') + '_' + table_name

    def is_augmented_name(self, d_vid, table_name):

        return table_name.startswith(d_vid.replace('/', '_') + '_')


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
        from ..schema import Schema

        p_vid = self._to_vid(partition)
        d_vid = self._partition_to_dataset_vid(partition)

        meta, table = self.table_meta(d_vid, p_vid, table_name)

        if not self.has_table(table.name):
            table.create(bind=self.database.engine)
            self.logger.log('create_table {}'.format(table.name))
        else:
            self.logger.log('table_exists {}'.format(table.name))

        return table, meta


class OldRelationalWarehouse(WarehouseInterface):

        

      
    def install_by_name(self,name):
    
        self.logger.log('install_name {}'.format(name))

        d = self.resolver.get_ref(name)
        p = d.partition
        
        if not d:
            raise DependencyError("Resolver failed to get dataset reference for {}".format(name))
        
        if not p:
            raise ValueError("Name must refer to a partition")
        
        if not self.has_dataset(d.vid):
            self.logger.log('install_dataset {}'.format(name))
            
            b = self.resolver.get(d.vid)
            
            if not b:
                raise DependencyError("Resolver failed to get dataset for {}".format(d.vname))
                  
            self.logger.log('install_bundle {}'.format(b.identity.vname))
            self.library.database.install_dataset(b)
        else:
            self.logger.log('Dataset already installed {}'.format(d.vname))

        if p:
            if self.has_partition(p.vid):
                self.logger.log('Partition already installed {}'.format(d.vname))
            else:
                b = self.resolver.get(d.vid)
                self.library.database.install_partition(b,p)
                self.install_partition_by_name(b, p)

    
    def install_partition_by_name(self, bundle, p):
        
        self.logger.log('install_partition '.format(p.vname))

        partition = bundle.partitions.partition(p.id_)
    
        if partition.record.format == 'geo':
            self._install_geo_partition(partition)
            
        elif partition.record.format == 'hdf':
            self._install_hdf_partition(partition)
            
        else:
            self._install_partition(partition)



    
        return meta, table
    
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
        
    

        
    
    def _install_partition_insert(self, partition):
        from ..database.inserter import  ValueInserter
        
        self.logger.log('install_partition_insert {}'.format(partition.identity.name))

        pdb = partition.database
     
        tables = partition.data.get('tables',[])

        # Create the tables
        for table_name in tables:
            self.create_table(partition.identity, table_name)

        if self.database.driver == 'mysql':
            cache_size = 5000
        elif self.database.driver == 'postgres':
            cache_size = 20000
        else:
            cache_size = 50000
    
        cache_size = 1000
    

        p_vid = partition.identity.vid
        d_vid = partition.identity.as_dataset.vid
       
        for table_name in tables:

            _, dest_table = self.table_meta(d_vid, p_vid,  table_name)
            _, src_table = partition.bundle.schema.get_table_meta(table_name, use_id=False)

            dest_table._db_orm_table = self.table(d_vid, p_vid,  table_name)

            self.logger.log('populate_table {}'.format(table_name))

            with ValueInserter(self.database,None, dest_table,cache_size = cache_size) as ins:
   
                try: self.database.connection.execute('DELETE FROM "public"."{}"'.format(dest_table.name))
                except: pass
                   
   
                for i,row in enumerate(pdb.session.execute(src_table.select())):
                    self.logger.progress('add_row',table_name,i)
  
                    ins.insert(row)
                    
                self.library.install_table(dest_table.vid, dest_table.name)

        self.logger.log('done {}'.format(partition.vname))

    def _install_partition(self, partition):
        self._install_partition_insert(partition)

    def _install_geo_partition(self, partition):
        #
        # Use ogr2ogr to copy. 
        #
        raise NotImplemented()

    def _install_hdf_partition(self, partition):
        
        raise NotImplemented()
          
    def remove_by_name(self,name):
        from ..orm import Dataset
        from ..bundle import LibraryDbBundle
        from ..identity import PartitionNameQuery
        from sqlalchemy.exc import  NoSuchTableError, ProgrammingError
        
        dataset  = self.get(name)

        if dataset.partition:
            b = LibraryDbBundle(self.library.database, dataset.vid)
            p = b.partitions.find(id_=dataset.partition.id_)
            self.logger.log("Dropping tables in partition {}".format(p.identity.vname))
            for table_name in p.tables: # Table name without the id prefix
                
                table_name = self.augmented_table_name(p.identity.as_dataset().vid, table_name)
                
                try:
                    self.database.drop_table(table_name)
                    self.logger.log("Dropped table: {}".format(table_name))
                    
                except NoSuchTableError:
                    self.logger.log("Table does not exist (a): {}".format(table_name))
                    
                except ProgrammingError:
                    self.logger.log("Table does not exist (b): {}".format(table_name))

            self.library.database.remove_partition(dataset.partition)
            
            
        elif dataset:
            
            b = LibraryDbBundle(self.library.database, dataset.vid)
            for p in b.partitions:
                self.remove_by_name(p.identity.vname)

            self.logger.log('Removing bundle {}'.format(dataset.vname))
            self.library.database.remove_bundle(dataset)
        else:
            self.logger.error("Failed to find partition or bundle by name '{}'".format(name))
        
    def clean(self):
        self.database.clean()
        
    def drop(self):
        self.database.drop()
        

        
    def exists(self):
        self.database.exists()
        
    def info(self):
        config = self.config.to_dict()

        if 'password' in config['database']: del config['database']['password']
        return config
     
 