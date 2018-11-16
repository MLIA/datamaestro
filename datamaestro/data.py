"""
Contains 
"""

import sys
import os
import tempfile
import urllib
import shutil
from functools import lru_cache
import logging
import re
import inspect
from .context import Context
import urllib.request
from pathlib import Path
from itertools import chain
import importlib

import yaml

YAML_SUFFIX = ".yaml"

class DatasetReference:
    def __init__(self, value):
        self.value = value

    def resolve(self, reference):
        did = self.value
        logging.debug("Resolving dataset reference %s", did)

        pos = did.find("!")
        if pos > 0:
            namespace = did[:pos]
            name = did[pos+1:]
            did = "%s.%s" % (reference.resolvens(namespace), name)
        elif did.startswith("."):
            did = reference.baseid + did

        return Dataset.find(reference.context, did)

def datasetref(loader, node):
    """A dataset reference"""
    assert(isinstance(node.value, str))
    return DatasetReference(node.value)

yaml.Loader.add_constructor('!dataset', datasetref)

def readyaml(path):
    with open(path) as f:
        return yaml.load(f)

def readyamls(path):
    with open(path) as f:
        for p in yaml.load_all(f):
            yield p



class DataFile:
    """A data configuration file"""
    @lru_cache()
    def __init__(self, repository, prefix: str, path: str):
        self.repository = repository
        logging.debug("Reading %s", path)
        self.path = path
        self.datasets = {}
        self.id = prefix

        first = None
        for doc in readyamls(path):
            fulldid = "%s.%s" % (prefix, doc["id"])  if "id" in doc else self.id
            ds = Dataset(self, fulldid, doc, first)
            self.datasets[fulldid] = ds

            if not first:
                first = ds
                self.name = doc.get("name", self.id)


    def __contains__(self, name):
        """Returns true if the dataset belongs to this datafile"""
        return name in self.datasets

    def __getitem__(self, name):
        return self.datasets[name]

    def resolvens(self, ns):
        return self.content["namespaces"][ns]

    def __iter__(self):
        return self.datasets.values().__iter__()

    @property
    def description(self):
        return self.content.get("description", "")

    @property
    def context(self):
        return self.repository.context

    @property
    def baseid(self):
        return self.id
    

class Dataset:
    """Represents one dataset"""

    def __init__(self, datafile: DataFile, datasetid: str, content: object, parent: "Dataset"):
        """
        Construct a new dataset

        :param datafile: the attached definition file
        :param id: the ID of this dataset
        :param content: The dataset definition
        """
        self.datafile = datafile
        self.id = datasetid
        self.content = content
        self._handler = None
        self.parent = parent
        self.isalias = isinstance(content, DatasetReference)

    def parent(self):
        pos = self.id.rfind(".")
        return self.datafile.repository.search(self.id[:pos])

    @property
    def context(self):
        """Returns the context"""
        return self.datafile.context

    @property
    def ids(self):
        """Returns all the IDs of this dataset"""
        return [self.id]
    
    @property
    def repository(self):
        """Main ID is the first one"""
        return self.datafile.repository

    @property
    def baseid(self):
        """Main ID is the first one"""
        return self.datafile.id

    def resolvens(self, ns):
        return self.datafile.resolvens(ns)

    def __repr__(self):
        return "Dataset(%s)" % (", ".join(self.ids))


    def __getitem__(self, key):
        """Get the item"""

        # If content is a dataset reference, then resolve it
        if isinstance(self.content, DatasetReference):
            self.content = self.content.resolve(self).content

        # Tries first ourselves, then go upward
        if key in self.content:
            return self.content[key]
        if self.parent:
            return self.parent[key]

        raise IndexError()
    

    def get(self, key, defaultvalue):
        try:
            return self[key]
        except IndexError:
            return defaultValue

    @property
    def datadir(self):
        """Path containing real data"""
        datapath = self.datafile.repository.datapath
        if "datapath" in self:
            steps = self.id.split(".")
            steps.extend(self["datapath"].split("/"))
        else:
            steps = self.id.split(".")
        return datapath.joinpath(*steps)

    @staticmethod
    def find(config: "Context", name: str):
        """Find a dataset given its name"""
        logging.debug("Searching dataset %s", name)
        for repository in config.repositories():
            logging.debug("Searching dataset %s in %s", name, repository)
            dataset = repository.search(name)
            if dataset is not None:
                return dataset
        raise Exception("Could not find the dataset %s" % (name))

    @property
    def handler(self):
        if not self._handler:
            if "handler" in self:
                name = self["handler"]
                if isinstance(name, dict):
                    (key, value), = name.items()
                    self._handler = self.repository.findhandler("dataset", key)(self, self.content, value)                
                else:
                    self._handler = self.repository.findhandler("dataset", name)(self, self.content, None)
            else:
                from datamaestro.handlers.dataset import DatasetHandler
                self._handler = DatasetHandler(self, self.content, None)
        return self._handler

    def download(self):
        return self.handler.download()

    def description(self):
        return self.handler.description()

    def tags(self):
        return self.handler.tags()

    def prepare(self):
        return self.handler.prepare()
        

class Repository:
    """A repository"""
    def __init__(self, context: Context, basedir:Path= None):
        """Initialize a new repository

        :param context: The dataset main context
        :param basedir: The base directory of the repository
            (by default, the same as the repository class)
        """
        self.context = context
        self.basedir = basedir 
        if not self.basedir:
            p = inspect.getabsfile(self.__class__)
            self.basedir = Path(p).parent
        self.configdir = self.basedir.joinpath("config")
        self.id = self.__class__.NAMESPACE
        self.name = self.id
        self.module = self.__class__.__module__
        
    def __repr__(self):
        return "Repository(%s)" % self.basedir

    def search(self, name: str):
        """Search for a dataset in the definitions"""
        logging.debug("Searching for %s in %s", name, self.configdir)

        # Search for the YAML file that might contain the definition
        components = name.split(".")
        sub = None
        prefix = None
        path = self.configdir
        for i, c in enumerate(components):
            path = path.joinpath(c)
            if path.with_suffix(YAML_SUFFIX).is_file():
                prefix = ".".join(components[:i+1])
                sub = ".".join(components[i+1:])
                path = path.with_suffix(path.suffix + YAML_SUFFIX)
                break
            if not path.is_dir():
                logging.error("Could not find %s", path)
                return None

        # Get the dataset
        logging.debug("Found file %s [prefix=%s/id=%s]", path, prefix, sub)
        f = DataFile(self, prefix, path)
        if not name in f:
            return None

        dataset = f[name]
        if isinstance(dataset.content, DatasetReference):
            dataset = dataset.content.resolve(f)
        return dataset

    def datafile(self, did):
        """Returns a datafile given the its id"""
        path = self.basedir.joinpath("config").joinpath(*did.split(".")).with_suffix(YAML_SUFFIX)
        return DataFile(self, did, path)

    def datafiles(self):
        """Iterates over all datafiles in this repository"""
        logging.debug("Looking at definitions in %s", self.configdir)
        for path in self.configdir.rglob("*%s" % YAML_SUFFIX):
            try:
                c = [p.name for p in path.relative_to(self.configdir).parents][:-1][::-1]
                c.append(path.stem)
                fid = ".".join(c)
                datafile = DataFile(self, fid, path)
                yield datafile
            except Exception as e:
                import traceback
                traceback.print_exc()
                logging.error("Error while reading definitions file %s: %s", path, e)

    def __iter__(self):
        """Iterates over all datasets in this repository"""
        for datafile in self.datafiles():
            for dataset in datafile:
                yield dataset

    def findhandler(self, handlertype, fullname):
        """
        Find a handler of a given type

        A handle can be specified using

        `module/subpackage:class`

        will map to class <class> in <module>.handlers.<handlertype>.subpackage

        Two shortcuts can be used:
        - `/subpackage:class`: module = datamaestro
        - `subpackage:class`: module = repository module



        """
        logging.debug("Searching for handler %s of type %s", fullname, handlertype)
        pattern = re.compile(r"^((?P<module>[\w_]+)?(?P<slash>/))?(?P<path>[\w_]+):(?P<name>[\w_]+)$")
        m = pattern.match(fullname)
        if not m:
            raise Exception("Invalid handler specification %s" % name)

        name = m.group('name')
        if m.group('slash') is None:
            # relative path
            module = self.module
        else:
            # absolute path
            if m.group('module'):
                module = m.group('module')
            else:
                module = "datamaestro"
        

        package = "%s.handlers.%s.%s" % (module, handlertype, m.group("path"))
        
        logging.debug("Searching for handler: package %s, class %s", package, name)
        try:
            package = importlib.import_module(package)
        except ModuleNotFoundError:
            raise Exception(f"""Could not find handler "{fullname}" of type {handlertype}: module {package} not found""")

        return getattr(package, name)

    @property
    def generatedpath(self):
        return self.basedir.joinpath("generated")

    @property
    def downloadpath(self):
        return self.context.datapath.joinpath(self.id)
        
    @property
    def datapath(self):
        return self.context.datapath.joinpath(self.id)

    @property
    def extrapath(self):
        """Path to the directory containing extra configuration files"""
        return self.basedir.joinpath("data")

